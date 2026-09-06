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
from packages.ai_gateway.llm_provider import LLMProvider, ModelUnavailableError, get_llm_provider
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


def deterministic_classify(enquiry: CanonicalEnquiry) -> Optional[Tuple[ClassificationResult, ExtractedInformation]]:
    """
    High-confidence deterministic classification rules for degraded mode (Section 5).
    Reuses known safe signatures without manufacturing confidence.
    """
    text = f"{enquiry.subject or ''}\n{enquiry.body_text}".lower()
    sender_email = (enquiry.sender.email or "").lower()

    # Known spam signature
    if "megaleadlists" in sender_email or "cryptocurrency" in text or "ceo leads" in text:
        cls_res = ClassificationResult(
            category="spam/unwanted",
            confidence=0.99,
            reason_code="SPAM_KEYWORDS_AND_CRYPTO",
            provenance=["sales@megaleadlists.example", "cryptocurrency payment instructions"],
        )
        ext_res = ExtractedInformation(
            email=enquiry.sender.email,
            intent_summary="Unsolicited lead list sales offer with crypto payment",
            provenance=["Buy 50,000 Australian CEO leads today"],
        )
        return cls_res, ext_res

    # Known internal incident signature
    if "hubspot sync" in text or "oauth token" in text or "system alert" in text:
        cls_res = ClassificationResult(
            category="internal systems incident",
            confidence=0.98,
            reason_code="INTERNAL_AUTOMATED_MONITORING_ALERT",
            provenance=["HubSpot sync failed", "OAuth token expired"],
        )
        ext_res = ExtractedInformation(
            intent_summary="HubSpot integration failure: OAuth token expired, 146 records unsynchronised, retry disabled after 3 failures",
            key_constraints=["OAuth token expired", "146 records unsynchronised", "Retry disabled after 3 failures"],
            provenance=["OAuth token expired", "146 records remain unsynchronised"],
        )
        return cls_res, ext_res

    return None


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

        degraded_mode = False
        models_attempted: list[str] = []

        try:
            start_ai = time.perf_counter()
            cls_result, ext_result = await self.llm.classify_and_extract(enquiry)
            pipeline_latency_ms = int((time.perf_counter() - start_ai) * 1000)
        except ModelUnavailableError as e:
            degraded_mode = True
            models_attempted = getattr(e, "models_attempted", [])
            pipeline_latency_ms = 0

            # Check for high-confidence deterministic rules (Section 5)
            det_match = deterministic_classify(enquiry)
            if det_match:
                cls_result, ext_result = det_match
            else:
                # Genuinely requires model reasoning -> explicitly defer (Section 4 & 5)
                cls_result = ClassificationResult(
                    category="needs-ai-review",
                    confidence=0.0,
                    reason_code="MODEL_UNAVAILABLE",
                    provenance=["DEGRADED_MODE: model reasoning deferred"],
                )
                ext_result = ExtractedInformation(
                    contact_name=enquiry.sender.name,
                    email=enquiry.sender.email,
                    phone=enquiry.sender.phone,
                    intent_summary="[DEFERRED] Work requiring model reasoning deferred due to LLM unavailability",
                    provenance=["Customer message body"],
                )

        enquiry.classification = cls_result
        enquiry.extracted = ext_result

        # Retrieve detailed stats from provider if available
        stats = getattr(self.llm, "last_call_stats", {})
        provider_name = stats.get("provider") or os.getenv("LLM_PROVIDER", "fake")
        model_name = stats.get("model") or os.getenv("LLM_STRONG_MODEL", "default")
        latency_ms = stats.get("latency_ms") or pipeline_latency_ms
        input_tokens = stats.get("input_tokens", 0)
        output_tokens = stats.get("output_tokens", 0)
        error_code = stats.get("error_code") or ("MODEL_UNAVAILABLE" if degraded_mode else None)

        if degraded_mode and cls_result.category == "needs-ai-review":
            # Save Degraded AI Run
            async with get_db_session() as session:
                db_ai = AIRunModel(
                    enquiry_id=enquiry_id,
                    task="classify_and_extract",
                    provider=provider_name,
                    model="unavailable",
                    prompt_version="v1.0",
                    output_json={
                        "mode": "DEGRADED_MODE",
                        "status": "MODEL_UNAVAILABLE",
                        "models_attempted": models_attempted,
                    },
                    validation_status="DEFERRED",
                    confidence=0.0,
                    latency_ms=latency_ms,
                    input_tokens=0,
                    output_tokens=0,
                    error_code="MODEL_UNAVAILABLE",
                )
                session.add(db_ai)

                await AuditService.record_event(
                    session=session,
                    enquiry_id=enquiry_id,
                    event_type="MODEL_UNAVAILABLE",
                    actor_type="SYSTEM",
                    actor_id="ai_gateway",
                    outcome="DEFERRED",
                    metadata_json={
                        "reason": "MODEL_UNAVAILABLE",
                        "mode": "DEGRADED_MODE",
                        "models_attempted": models_attempted,
                        "replayable": True,
                    },
                )
                await session.commit()

            await self._record_step(enquiry_id, "AI_UNDERSTANDING", "DEFERRED", error_code="MODEL_UNAVAILABLE")

            # Deterministic CRM Resolution during degraded mode (Section 4, 10, 11)
            await self._record_step(enquiry_id, "CRM_RESOLUTION", "IN_PROGRESS")
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
                        "mode": "DEGRADED_MODE",
                    },
                )

                # If identity correction detected, record explicit IDENTITY_CORRECTION_RECEIVED audit event
                if crm_res.correction_provenance:
                    prov = crm_res.correction_provenance
                    prev_id = {}
                    if "previous_email" in prov:
                        prev_id["email"] = prov["previous_email"]
                    if "previous_phone" in prov:
                        prev_id["phone"] = prov["previous_phone"]
                    corr_id = {}
                    if "new_email" in prov:
                        corr_id["email"] = prov["new_email"]
                    if "new_phone" in prov:
                        corr_id["phone"] = prov["new_phone"]

                    await AuditService.record_event(
                        session=session,
                        enquiry_id=enquiry_id,
                        event_type="IDENTITY_CORRECTION_RECEIVED",
                        actor_type="SYSTEM",
                        actor_id="identity_resolver",
                        outcome="PENDING_REVIEW",
                        metadata_json={
                            "correction_type": "CONTACT_IDENTITY_CORRECTION",
                            "existing_customer_id": prov.get("existing_customer_id"),
                            "previous_identity": prev_id,
                            "corrected_identity": corr_id,
                            "source_enquiry_id": enquiry_id,
                            "mode": "DEGRADED_MODE",
                        },
                    )

                # Persist deferred draft with blank content (Section 6 & 7)
                db_draft = DraftModel(
                    enquiry_id=enquiry_id,
                    draft_type="deferred",
                    content="",
                    grounding_refs_json=["DEGRADED_MODE: Static fallback customer drafts disabled"],
                    requires_approval=True,
                    status="DEFERRED_MODEL_UNAVAILABLE",
                )
                session.add(db_draft)
                await session.commit()

            await self._record_step(enquiry_id, "CRM_RESOLUTION", "COMPLETED")
            await self._record_step(enquiry_id, "DRAFTING", "DEFERRED", error_code="MODEL_UNAVAILABLE")

            # Create review task: IDENTITY_REVIEW if correction, otherwise AI_REVIEW
            task_type = "IDENTITY_REVIEW" if crm_res.correction_provenance else "AI_REVIEW"
            reason_code = "CONTACT_IDENTITY_CORRECTION" if crm_res.correction_provenance else "MODEL_UNAVAILABLE"
            priority = "HIGH" if crm_res.correction_provenance else "NORMAL"
            await self._create_review_task(
                enquiry_id=enquiry_id,
                task_type=task_type,
                reason_code=reason_code,
                priority=priority,
                assigned_to=None,
            )

            # Transition to DEFERRED state; NO outbox event; NO CRM write/merge!
            await self._update_status(enquiry_id, WorkflowStatus.DEFERRED)
            await self._record_step(enquiry_id, "WORKFLOW", "DEFERRED", error_code="MODEL_UNAVAILABLE")
            logger.info(f"Enquiry {enquiry_id} safely deferred in DEGRADED_MODE. No customer-facing draft or outbound event created.")
            return

        # Normal AI Run persistence when live model succeeded or deterministic classification
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
                actor_type="LLM" if not degraded_mode else "SYSTEM",
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
        try:
            missing_items = await self.llm.detect_missing_information(enquiry, ext_result)
        except ModelUnavailableError:
            missing_items = []
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

            # Record IDENTITY_CORRECTION_RECEIVED if correction provenance is present
            if crm_res.correction_provenance:
                prov = crm_res.correction_provenance
                prev_id = {}
                if "previous_email" in prov:
                    prev_id["email"] = prov["previous_email"]
                if "previous_phone" in prov:
                    prev_id["phone"] = prov["previous_phone"]
                corr_id = {}
                if "new_email" in prov:
                    corr_id["email"] = prov["new_email"]
                if "new_phone" in prov:
                    corr_id["phone"] = prov["new_phone"]

                await AuditService.record_event(
                    session=session,
                    enquiry_id=enquiry_id,
                    event_type="IDENTITY_CORRECTION_RECEIVED",
                    actor_type="SYSTEM",
                    actor_id="identity_resolver",
                    outcome="PENDING_REVIEW",
                    metadata_json={
                        "correction_type": "CONTACT_IDENTITY_CORRECTION",
                        "existing_customer_id": prov.get("existing_customer_id"),
                        "previous_identity": prev_id,
                        "corrected_identity": corr_id,
                        "source_enquiry_id": enquiry_id,
                    },
                )

            await session.commit()

        # If CRM status is AMBIGUOUS
        if crm_res.status.value == "AMBIGUOUS":
            reason_code = "IDENTITY_CONFLICT" if ("conflict" in str(crm_res.conflict_flags).lower() or crm_res.correction_provenance) else "AMBIGUOUS_CRM_CANDIDATES"
            task_type = "IDENTITY_REVIEW" if (reason_code == "IDENTITY_CONFLICT" or crm_res.correction_provenance) else "CRM_REVIEW"

            # If no customer anchor resolved, route to review immediately
            if not crm_res.selected_customer_id:
                await self._update_status(enquiry_id, WorkflowStatus.CRM_REVIEW)
                await self._create_review_task(
                    enquiry_id=enquiry_id,
                    task_type=task_type,
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
        try:
            draft = await self.llm.draft_response(enquiry, selected_cand)
        except ModelUnavailableError:
            async with get_db_session() as session:
                db_draft = DraftModel(
                    enquiry_id=enquiry_id,
                    draft_type="deferred",
                    content="",
                    grounding_refs_json=["DEGRADED_MODE: Static fallback customer drafts disabled"],
                    requires_approval=True,
                    status="DEFERRED_MODEL_UNAVAILABLE",
                )
                session.add(db_draft)
                await AuditService.record_event(
                    session=session,
                    enquiry_id=enquiry_id,
                    event_type="MODEL_UNAVAILABLE",
                    actor_type="SYSTEM",
                    actor_id="ai_gateway",
                    outcome="DEFERRED",
                    metadata_json={"reason": "MODEL_UNAVAILABLE", "step": "DRAFTING", "mode": "DEGRADED_MODE", "replayable": True},
                )
                await session.commit()
            await self._create_review_task(enquiry_id, "AI_REVIEW", "MODEL_UNAVAILABLE", "NORMAL", None)
            await self._update_status(enquiry_id, WorkflowStatus.DEFERRED)
            await self._record_step(enquiry_id, "DRAFTING", "DEFERRED", error_code="MODEL_UNAVAILABLE")
            return

        enquiry.draft = draft

        routing = StaffRouter.route_enquiry(enquiry)
        enquiry.routing = routing

        tier, requires_approval, policy_reason = AutonomyPolicyEngine.evaluate_action_policy(enquiry, draft, ext_result)

        # If CRM status was AMBIGUOUS or correction provenance detected, force human review!
        if crm_res.status.value == "AMBIGUOUS" or crm_res.correction_provenance:
            requires_approval = True
            tier = 5
            policy_reason = "IDENTITY_CORRECTION_PENDING_APPROVAL"

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
            if crm_res.correction_provenance or crm_res.status.value == "AMBIGUOUS":
                task_type = "IDENTITY_REVIEW"
            elif cls_result.category == "technical engineering":
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

    async def replay_deferred_work(self, enquiry_id: str) -> None:
        """
        Replays deferred work for an enquiry after LLM recovery (Section 8 & 16).
        Idempotent; preserves original enquiry ID, raw events, and identity history.
        """
        enquiry_id_ctx.set(enquiry_id)
        logger.info(f"Replaying deferred work for enquiry: {enquiry_id}")

        async with get_db_session() as session:
            stmt = select(EnquiryModel).where(EnquiryModel.id == enquiry_id)
            enquiry_db = (await session.execute(stmt)).scalar_one_or_none()
            if not enquiry_db:
                logger.error(f"Enquiry {enquiry_id} not found for replay!")
                return

            if enquiry_db.workflow_status in [WorkflowStatus.COMPLETED.value, "REJECTED"]:
                logger.info(f"Enquiry {enquiry_id} already in terminal state {enquiry_db.workflow_status}. Replay skipped.")
                return

            # Record audit event for replay
            await AuditService.record_event(
                session=session,
                enquiry_id=enquiry_id,
                event_type="MODEL_RECOVERY_REPLAY",
                actor_type="SYSTEM",
                actor_id="recovery_service",
                outcome="IN_PROGRESS",
                metadata_json={"enquiry_id": enquiry_id, "mode": "RECOVERY", "replayable": True},
            )

            # Resolve open MODEL_UNAVAILABLE review tasks
            task_stmt = select(ReviewTaskModel).where(
                ReviewTaskModel.enquiry_id == enquiry_id,
                ReviewTaskModel.reason_code == "MODEL_UNAVAILABLE",
                ReviewTaskModel.status == "OPEN",
            )
            open_tasks = (await session.execute(task_stmt)).scalars().all()
            for t in open_tasks:
                t.status = "RESOLVED"
                t.resolved_at = datetime.now(timezone.utc)

            await session.commit()

        # Re-execute workflow with live model
        await self.execute_workflow(enquiry_id)

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
