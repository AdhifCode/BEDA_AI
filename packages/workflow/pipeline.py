import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db_session
from db.models.schema import (
    AIRunModel,
    AttachmentModel,
    AuditEventModel,
    CRMResolutionModel,
    DraftModel,
    EnquiryModel,
    OutboxEventModel,
    RawEventModel,
    ReviewTaskModel,
    WorkflowStepModel,
)
from packages.ai_gateway.llm_provider import LLMProvider, get_llm_provider
from packages.audit.audit_service import AuditService
from packages.crm.client import CRMClient, get_crm_client
from packages.domain.models import (
    CanonicalEnquiry,
    ClassificationResult,
    DraftResult,
    ExtractedInformation,
    ReviewDecision,
    ReviewTask,
    WorkflowStatus,
)
from packages.identity.resolver import IdentityResolver
from packages.observability.logger import get_logger, enquiry_id_ctx, step_id_ctx
from packages.policy.engine import AutonomyPolicyEngine
from packages.policy.router import StaffRouter
from packages.validation.normalizer import normalize_whitespace

logger = get_logger("pipeline")


class WorkflowPipeline:
    def __init__(self, llm: Optional[LLMProvider] = None, crm: Optional[CRMClient] = None):
        self.llm = llm or get_llm_provider()
        self.crm = crm or get_crm_client()
        self.identity_resolver = IdentityResolver(self.crm)

    async def ingest_and_persist(
        self,
        enquiry: CanonicalEnquiry,
        raw_payload: Dict[str, Any],
        source_channel: str,
    ) -> Tuple[CanonicalEnquiry, bool]:
        """
        Persists raw event and canonical enquiry atomically under idempotency key constraint.
        If duplicate, records DUPLICATE_IGNORED and returns (existing_enquiry, True).
        """
        async with get_db_session() as session:
            # Check for existing idempotency key
            stmt = select(EnquiryModel).where(EnquiryModel.idempotency_key == enquiry.idempotency_key)
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing:
                logger.info(f"Duplicate enquiry detected for key: {enquiry.idempotency_key}. Returning existing ID: {existing.id}")
                await AuditService.record_event(
                    session=session,
                    enquiry_id=existing.id,
                    event_type="DUPLICATE_IGNORED",
                    actor_type="SYSTEM",
                    actor_id="idempotency_guard",
                    outcome="IGNORED",
                    metadata_json={"idempotency_key": enquiry.idempotency_key},
                )
                await session.commit()
                enquiry.enquiry_id = existing.id
                enquiry.workflow_status = WorkflowStatus(existing.workflow_status)
                return enquiry, True

            # Insert new enquiry
            db_enquiry = EnquiryModel(
                id=enquiry.enquiry_id,
                idempotency_key=enquiry.idempotency_key,
                source_channel=source_channel,
                source_message_id=enquiry.source_message_id,
                received_at=enquiry.received_at,
                sender_name=enquiry.sender.name,
                sender_email=enquiry.sender.email,
                sender_phone=enquiry.sender.phone,
                subject=enquiry.subject,
                body_text=enquiry.body_text,
                workflow_status=WorkflowStatus.RECEIVED.value,
            )
            session.add(db_enquiry)

            # Insert raw event
            raw_bytes = json.dumps(raw_payload, sort_keys=True).encode("utf-8")
            raw_hash = hashlib.sha256(raw_bytes).hexdigest()
            db_raw = RawEventModel(
                enquiry_id=enquiry.enquiry_id,
                payload_json=raw_payload,
                payload_hash=raw_hash,
                received_at=enquiry.received_at,
                source_channel=source_channel,
            )
            session.add(db_raw)

            # Insert attachments if any
            for att in enquiry.attachments:
                db_att = AttachmentModel(
                    id=att.id,
                    enquiry_id=enquiry.enquiry_id,
                    filename=att.filename,
                    content_type=att.content_type,
                    storage_ref=att.storage_ref,
                    sha256=att.sha256,
                    sanitized=True,
                    extracted_text=att.extracted_text,
                )
                session.add(db_att)

            # Initial Audit Event
            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry.enquiry_id,
                event_type="ENQUIRY_INGESTED",
                actor_type="SYSTEM",
                actor_id="channel_adapter",
                outcome="SUCCESS",
                metadata_json={
                    "channel": source_channel,
                    "idempotency_key": enquiry.idempotency_key,
                    "attachments_count": len(enquiry.attachments),
                },
            )
            await session.commit()

        return enquiry, False

    async def execute_workflow(self, enquiry_id: str) -> None:
        """
        Executes the bounded workflow pipeline for an enquiry.
        """
        enquiry_id_ctx.set(enquiry_id)
        logger.info(f"Starting workflow execution for enquiry: {enquiry_id}")

        async with get_db_session() as session:
            stmt = select(EnquiryModel).where(EnquiryModel.id == enquiry_id)
            enquiry_db = (await session.execute(stmt)).scalar_one_or_none()
            if not enquiry_db:
                logger.error(f"Enquiry {enquiry_id} not found in database!")
                return

            # Reconstruct CanonicalEnquiry
            enquiry = CanonicalEnquiry(
                enquiry_id=enquiry_db.id,
                idempotency_key=enquiry_db.idempotency_key,
                source_channel=enquiry_db.source_channel,
                source_message_id=enquiry_db.source_message_id,
                received_at=enquiry_db.received_at,
                sender={"name": enquiry_db.sender_name, "email": enquiry_db.sender_email, "phone": enquiry_db.sender_phone},
                subject=enquiry_db.subject,
                body_text=enquiry_db.body_text,
                workflow_status=WorkflowStatus(enquiry_db.workflow_status),
            )

        # Step 1: Normalization
        await self._record_step(enquiry_id, "NORMALIZATION", "IN_PROGRESS")
        enquiry.body_text = normalize_whitespace(enquiry.body_text)
        await self._update_status(enquiry_id, WorkflowStatus.NORMALIZED)
        await self._record_step(enquiry_id, "NORMALIZATION", "COMPLETED")

        # Step 2: AI Understanding (Classification & Extraction)
        await self._record_step(enquiry_id, "AI_UNDERSTANDING", "IN_PROGRESS")
        await self._update_status(enquiry_id, WorkflowStatus.UNDERSTANDING)
        
        start_ai = time.perf_counter()
        cls_result, ext_result = await self.llm.classify_and_extract(enquiry)
        pipeline_latency_ms = int((time.perf_counter() - start_ai) * 1000)

        enquiry.classification = cls_result
        enquiry.extracted = ext_result

        # Retrieve detailed stats from provider if available
        stats = getattr(self.llm, "last_call_stats", {})
        provider_name = stats.get("provider") or os.getenv("LLM_PROVIDER", "fake")
        model_name = stats.get("model") or os.getenv("LLM_STRONG_MODEL", "default")
        latency_ms = stats.get("latency_ms") or pipeline_latency_ms
        input_tokens = stats.get("input_tokens", 0)
        output_tokens = stats.get("output_tokens", 0)
        error_code = stats.get("error_code")

        # Save AI Run
        async with get_db_session() as session:
            db_ai = AIRunModel(
                enquiry_id=enquiry_id,
                task="classify_and_extract",
                provider=provider_name,
                model=model_name,
                prompt_version="v1.0",
                output_json={"classification": cls_result.model_dump(), "extracted": ext_result.model_dump()},
                validation_status="VALID",
                confidence=cls_result.confidence,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_code=error_code,
            )
            session.add(db_ai)

            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type="AI_UNDERSTANDING_COMPLETED",
                actor_type="LLM",
                actor_id=provider_name,
                model_provider=provider_name,
                model_version=model_name,
                outcome="SUCCESS",
                metadata_json={
                    "category": cls_result.category,
                    "confidence": cls_result.confidence,
                    "latency_ms": latency_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "error_code": error_code,
                },
            )
            await session.commit()

        await self._record_step(enquiry_id, "AI_UNDERSTANDING", "COMPLETED")

        # Step 3: Validation & Missing Information
        await self._record_step(enquiry_id, "VALIDATION", "IN_PROGRESS")
        missing_items = await self.llm.detect_missing_information(enquiry, ext_result)
        enquiry.missing_information = missing_items

        if cls_result.confidence < float(os.getenv("LLM_CONFIDENCE_THRESHOLD", "0.80")):
            await self._update_status(enquiry_id, WorkflowStatus.AI_REVIEW)
            await self._create_review_task(enquiry_id, "AI_REVIEW", "LOW_CONFIDENCE_CLASSIFICATION", "NORMAL", None)
            await self._record_step(enquiry_id, "VALIDATION", "LOW_CONFIDENCE_ROUTED_TO_REVIEW")
            return

        await self._update_status(enquiry_id, WorkflowStatus.VALIDATED)
        await self._record_step(enquiry_id, "VALIDATION", "COMPLETED")

        # Special Case: Spam Quarantine (Section 19: E004)
        if cls_result.category == "spam/unwanted":
            await self._update_status(enquiry_id, WorkflowStatus.QUARANTINED)
            async with get_db_session() as session:
                await AuditService.record_event(
                    session=session,
                    enquiry_id=enquiry_id,
                    event_type="ENQUIRY_QUARANTINED",
                    actor_type="SYSTEM",
                    actor_id="spam_filter",
                    outcome="QUARANTINED",
                    metadata_json={"reason": cls_result.reason_code},
                )
                await session.commit()
            await self._record_step(enquiry_id, "SPAM_QUARANTINE", "COMPLETED")
            return

        # Step 4: CRM Resolution (Section 13)
        await self._record_step(enquiry_id, "CRM_RESOLUTION", "IN_PROGRESS")
        await self._update_status(enquiry_id, WorkflowStatus.CRM_RESOLUTION)

        crm_res = await self.identity_resolver.resolve(enquiry, ext_result)
        enquiry.crm_resolution = crm_res

        async with get_db_session() as session:
            db_crm_res = CRMResolutionModel(
                enquiry_id=enquiry_id,
                status=crm_res.status.value,
                method=crm_res.method.value,
                selected_customer_id=crm_res.selected_customer_id,
                candidate_json=[c.model_dump() for c in crm_res.candidates],
                conflict_flags_json=crm_res.conflict_flags,
            )
            session.add(db_crm_res)
            
            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type="CRM_RESOLUTION_COMPLETED",
                actor_type="SYSTEM",
                actor_id="identity_resolver",
                outcome=crm_res.status.value,
                metadata_json={
                    "method": crm_res.method.value,
                    "selected_customer_id": crm_res.selected_customer_id,
                    "candidates_count": len(crm_res.candidates),
                    "conflicts": crm_res.conflict_flags,
                },
            )
            await session.commit()

        # If CRM status is AMBIGUOUS (e.g. E002, E010), route to CRM_REVIEW without auto-merge!
        if crm_res.status.value == "AMBIGUOUS":
            await self._update_status(enquiry_id, WorkflowStatus.CRM_REVIEW)
            reason_code = "IDENTITY_CONFLICT" if "conflict" in str(crm_res.conflict_flags).lower() else "AMBIGUOUS_CRM_CANDIDATES"
            await self._create_review_task(
                enquiry_id=enquiry_id,
                task_type="CRM_REVIEW" if reason_code == "AMBIGUOUS_CRM_CANDIDATES" else "IDENTITY_REVIEW",
                reason_code=reason_code,
                priority="HIGH",
                assigned_to=None,
            )
            await self._record_step(enquiry_id, "CRM_RESOLUTION", "ROUTED_TO_CRM_REVIEW")
            return

        await self._record_step(enquiry_id, "CRM_RESOLUTION", "COMPLETED")

        # Step 5: Grounded Drafting & Routing
        await self._record_step(enquiry_id, "DRAFTING", "IN_PROGRESS")
        await self._update_status(enquiry_id, WorkflowStatus.DRAFTED)

        selected_cand = crm_res.candidates[0] if crm_res.candidates else None
        draft = await self.llm.draft_response(enquiry, selected_cand)
        enquiry.draft = draft

        routing = StaffRouter.route_enquiry(enquiry)
        enquiry.routing = routing

        tier, requires_approval, policy_reason = AutonomyPolicyEngine.evaluate_action_policy(enquiry, draft, ext_result)
        draft.requires_approval = requires_approval

        async with get_db_session() as session:
            db_draft = DraftModel(
                enquiry_id=enquiry_id,
                draft_type=draft.draft_type,
                content=draft.content,
                grounding_refs_json=draft.grounding_refs,
                requires_approval=requires_approval,
                status="PENDING_APPROVAL" if requires_approval else "AUTONOMOUS",
            )
            session.add(db_draft)

            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type="DRAFT_GENERATED",
                actor_type="SYSTEM",
                actor_id="draft_engine",
                outcome="SUCCESS",
                metadata_json={
                    "draft_type": draft.draft_type,
                    "grounding_refs": draft.grounding_refs,
                    "requires_approval": requires_approval,
                    "policy_tier": tier,
                    "assigned_owner": routing.assigned_owner,
                },
            )
            await session.commit()

        await self._record_step(enquiry_id, "DRAFTING", "COMPLETED")

        # Step 6: Autonomy Gating / Approval Queue
        if requires_approval or routing.requires_review:
            await self._update_status(enquiry_id, WorkflowStatus.APPROVAL)
            task_type = "APPROVAL"
            if cls_result.category == "technical engineering":
                task_type = "TECHNICAL_REVIEW"
            elif cls_result.category == "non-sales/non-support":
                task_type = "NON_SALES_REVIEW"
            elif cls_result.category == "customer support/accounts":
                task_type = "ACCOUNTS_REVIEW"

            await self._create_review_task(
                enquiry_id=enquiry_id,
                task_type=task_type,
                reason_code=policy_reason,
                priority="URGENT" if tier == 5 else "NORMAL",
                assigned_to=routing.assigned_owner if routing.assigned_owner != "OWNER_UNCONFIGURED" else None,
            )
            await self._record_step(enquiry_id, "APPROVAL_GATE", "PENDING_HUMAN_APPROVAL")
            logger.info(f"Enquiry {enquiry_id} requires human approval: {policy_reason}. Routed to {routing.assigned_owner}")
            return

        # Low risk autonomous execution (e.g. Internal incident dispatch E011)
        await self._update_status(enquiry_id, WorkflowStatus.EXECUTE)
        await self._record_step(enquiry_id, "EXECUTION", "IN_PROGRESS")

        # Create Outbox Event
        async with get_db_session() as session:
            outbox = OutboxEventModel(
                event_type="DISPATCH_INTERNAL_ALERT" if cls_result.category == "internal systems incident" else "DISPATCH_COMMUNICATION",
                aggregate_id=enquiry_id,
                payload={"draft": draft.model_dump(), "routing": routing.model_dump()},
                published_at=datetime.now(timezone.utc),
            )
            session.add(outbox)

            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type="ACTION_EXECUTED_AUTONOMOUSLY",
                actor_type="SYSTEM",
                actor_id="workflow_engine",
                outcome="SUCCESS",
                metadata_json={"tier": tier, "routing": routing.assigned_owner},
            )
            await session.commit()

        await self._update_status(enquiry_id, WorkflowStatus.COMPLETED)
        await self._record_step(enquiry_id, "EXECUTION", "COMPLETED")
        logger.info(f"Workflow execution completed autonomously for enquiry: {enquiry_id}")

    async def execute_review_decision(
        self,
        review_task_id: str,
        decision: str,
        actor_id: str,
        actor_role: str,
        reason: str,
        edited_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes human approval decision (Section 16).
        Enforces server-side authorization: actor must have 'admin' or 'reviewer' role.
        """
        if actor_role not in ["admin", "reviewer"]:
            raise PermissionError(f"Actor {actor_id} with role '{actor_role}' is not authorized to approve tasks.")

        async with get_db_session() as session:
            stmt = select(ReviewTaskModel).where(ReviewTaskModel.id == review_task_id)
            task = (await session.execute(stmt)).scalar_one_or_none()
            if not task:
                raise ValueError(f"Review task {review_task_id} not found.")

            if task.status != "OPEN":
                return {"status": "TASK_ALREADY_RESOLVED", "task_id": review_task_id}

            enquiry_id = task.enquiry_id
            now = datetime.now(timezone.utc)
            task.status = "RESOLVED" if decision in ["APPROVE", "EDIT_AND_APPROVE"] else "REJECTED"
            task.resolved_at = now

            # Fetch draft
            draft_stmt = select(DraftModel).where(DraftModel.enquiry_id == enquiry_id).order_by(DraftModel.created_at.desc())
            draft_row = (await session.execute(draft_stmt)).scalars().first()

            if draft_row:
                if decision == "EDIT_AND_APPROVE" and edited_content:
                    draft_row.content = edited_content
                draft_row.status = "APPROVED" if decision in ["APPROVE", "EDIT_AND_APPROVE"] else "REJECTED"

            # Create outbox side effect if approved
            if decision in ["APPROVE", "EDIT_AND_APPROVE"]:
                outbox = OutboxEventModel(
                    event_type="DISPATCH_OUTBOUND_COMMUNICATION",
                    aggregate_id=enquiry_id,
                    payload={"content": draft_row.content if draft_row else "", "decision": decision, "approved_by": actor_id},
                    published_at=now,
                )
                session.add(outbox)

            # Record Audit Event
            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type=f"REVIEW_DECISION_{decision}",
                actor_type="HUMAN",
                actor_id=actor_id,
                outcome=task.status,
                metadata_json={
                    "review_task_id": review_task_id,
                    "task_type": task.task_type,
                    "reason": reason,
                    "edited": bool(edited_content),
                },
                decision_reference=review_task_id,
            )

            # Update enquiry workflow status
            enquiry_stmt = select(EnquiryModel).where(EnquiryModel.id == enquiry_id)
            enq = (await session.execute(enquiry_stmt)).scalar_one()
            enq.workflow_status = WorkflowStatus.COMPLETED.value if decision != "REJECT" else "REJECTED"
            enq.updated_at = now

            await session.commit()

        logger.info(f"Review task {review_task_id} {decision} by {actor_id}")
        return {"status": "SUCCESS", "decision": decision, "enquiry_id": enquiry_id}

    async def _update_status(self, enquiry_id: str, status: WorkflowStatus) -> None:
        async with get_db_session() as session:
            stmt = (
                update(EnquiryModel)
                .where(EnquiryModel.id == enquiry_id)
                .values(workflow_status=status.value, updated_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

    async def _record_step(self, enquiry_id: str, step_name: str, status: str, error_code: Optional[str] = None) -> None:
        async with get_db_session() as session:
            step = WorkflowStepModel(
                enquiry_id=enquiry_id,
                step_name=step_name,
                status=status,
                attempt=1,
                error_code=error_code,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc) if "COMPLETED" in status or "FAILED" in status else None,
            )
            session.add(step)
            await session.commit()

    async def _create_review_task(
        self,
        enquiry_id: str,
        task_type: str,
        reason_code: str,
        priority: str,
        assigned_to: Optional[str],
    ) -> None:
        async with get_db_session() as session:
            task = ReviewTaskModel(
                enquiry_id=enquiry_id,
                task_type=task_type,
                reason_code=reason_code,
                priority=priority,
                assigned_to=assigned_to,
                status="OPEN",
            )
            session.add(task)
            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type="REVIEW_TASK_CREATED",
                actor_type="SYSTEM",
                actor_id="policy_engine",
                outcome="PENDING_REVIEW",
                metadata_json={
                    "task_type": task_type,
                    "reason_code": reason_code,
                    "priority": priority,
                    "assigned_to": assigned_to,
                },
            )
            await session.commit()
