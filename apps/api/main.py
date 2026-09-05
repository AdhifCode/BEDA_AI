import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, HTTPException, Header, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, desc

from db.database import get_db_session
from db.models.schema import (
    AIRunModel,
    AttachmentModel,
    AuditEventModel,
    CRMResolutionModel,
    DraftModel,
    EnquiryModel,
    ReviewTaskModel,
    WorkflowStepModel,
)
from packages.audit.audit_service import AuditService
from packages.channels.adapters import (
    ChannelAdapter,
    EmailPayload,
    MessagingPayload,
    WebFormPayload,
)
from packages.domain.models import ReviewDecision
from packages.observability.logger import get_logger, request_id_ctx
from packages.workflow.pipeline import WorkflowPipeline
from packages.workflow.queue import get_queue

logger = get_logger("api")

app = FastAPI(
    title="BEDA Enquiry Intelligence & CRM Automation API",
    version="1.0.0",
    description="Bounded-autonomy workflow application for BEDA enquiries",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline = WorkflowPipeline()
queue = get_queue()


# Middleware to set request ID
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or request.headers.get("x-correlation-id") or os.urandom(8).hex()
    request_id_ctx.set(req_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


# Response Schemas
class IngestionResponse(BaseModel):
    enquiry_id: str
    status: str
    duplicate: bool


class DecisionPayload(BaseModel):
    decision: ReviewDecision
    edited_content: Optional[str] = None
    actor_id: str = "matt-cooper"
    actor_role: str = "admin"
    reason: str = "Approved by operations reviewer"


# Health Endpoints
@app.get("/health/live", tags=["Health"])
async def health_live():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/health/ready", tags=["Health"])
async def health_ready():
    # Verify DB connectivity
    try:
        async with get_db_session() as session:
            await session.execute(select(1))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check ready failed: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))


# Ingestion Endpoints
@app.post("/api/v1/ingest/email", response_model=IngestionResponse, tags=["Ingestion"])
async def ingest_email(payload: EmailPayload):
    enquiry = ChannelAdapter.from_email(payload)
    enquiry, is_dup = await pipeline.ingest_and_persist(enquiry, payload.model_dump(), "email")
    if not is_dup:
        await queue.enqueue(enquiry.enquiry_id)
        # Process inline if in development / test mode
        if os.getenv("PROCESS_INLINE", "true").lower() == "true":
            await pipeline.execute_workflow(enquiry.enquiry_id)
    return IngestionResponse(
        enquiry_id=enquiry.enquiry_id,
        status=enquiry.workflow_status.value,
        duplicate=is_dup,
    )


@app.post("/api/v1/ingest/web-form", response_model=IngestionResponse, tags=["Ingestion"])
async def ingest_web_form(payload: WebFormPayload):
    enquiry = ChannelAdapter.from_web_form(payload)
    enquiry, is_dup = await pipeline.ingest_and_persist(enquiry, payload.model_dump(), "web_form")
    if not is_dup:
        await queue.enqueue(enquiry.enquiry_id)
        if os.getenv("PROCESS_INLINE", "true").lower() == "true":
            await pipeline.execute_workflow(enquiry.enquiry_id)
    return IngestionResponse(
        enquiry_id=enquiry.enquiry_id,
        status=enquiry.workflow_status.value,
        duplicate=is_dup,
    )


@app.post("/api/v1/ingest/messaging", response_model=IngestionResponse, tags=["Ingestion"])
async def ingest_messaging(payload: MessagingPayload):
    enquiry = ChannelAdapter.from_messaging(payload)
    enquiry, is_dup = await pipeline.ingest_and_persist(enquiry, payload.model_dump(), "messaging")
    if not is_dup:
        await queue.enqueue(enquiry.enquiry_id)
        if os.getenv("PROCESS_INLINE", "true").lower() == "true":
            await pipeline.execute_workflow(enquiry.enquiry_id)
    return IngestionResponse(
        enquiry_id=enquiry.enquiry_id,
        status=enquiry.workflow_status.value,
        duplicate=is_dup,
    )


# Enquiry Queries
@app.get("/api/v1/enquiries", tags=["Enquiries"])
async def list_enquiries(
    status: Optional[str] = None,
    channel: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    async with get_db_session() as session:
        stmt = select(EnquiryModel).order_by(desc(EnquiryModel.created_at)).offset(offset).limit(limit)
        if status:
            stmt = stmt.where(EnquiryModel.workflow_status == status)
        if channel:
            stmt = stmt.where(EnquiryModel.source_channel == channel)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "enquiry_id": r.id,
                "idempotency_key": r.idempotency_key,
                "source_channel": r.source_channel,
                "received_at": r.received_at,
                "sender_name": r.sender_name,
                "sender_email": r.sender_email,
                "sender_phone": r.sender_phone,
                "subject": r.subject,
                "workflow_status": r.workflow_status,
                "created_at": r.created_at,
            }
            for r in rows
        ]


@app.get("/api/v1/enquiries/{enquiry_id}", tags=["Enquiries"])
async def get_enquiry(enquiry_id: str):
    async with get_db_session() as session:
        stmt = select(EnquiryModel).where(EnquiryModel.id == enquiry_id)
        enq = (await session.execute(stmt)).scalar_one_or_none()
        if not enq:
            raise HTTPException(status_code=404, detail="Enquiry not found")

        # Load related data
        ai_run = (await session.execute(select(AIRunModel).where(AIRunModel.enquiry_id == enquiry_id).order_by(desc(AIRunModel.created_at)))).scalars().first()
        crm_res = (await session.execute(select(CRMResolutionModel).where(CRMResolutionModel.enquiry_id == enquiry_id))).scalars().first()
        draft = (await session.execute(select(DraftModel).where(DraftModel.enquiry_id == enquiry_id).order_by(desc(DraftModel.created_at)))).scalars().first()
        tasks = (await session.execute(select(ReviewTaskModel).where(ReviewTaskModel.enquiry_id == enquiry_id))).scalars().all()
        attachments = (await session.execute(select(AttachmentModel).where(AttachmentModel.enquiry_id == enquiry_id))).scalars().all()
        steps = (await session.execute(select(WorkflowStepModel).where(WorkflowStepModel.enquiry_id == enquiry_id).order_by(WorkflowStepModel.started_at))).scalars().all()

        return {
            "enquiry_id": enq.id,
            "idempotency_key": enq.idempotency_key,
            "source_channel": enq.source_channel,
            "source_message_id": enq.source_message_id,
            "received_at": enq.received_at,
            "sender": {"name": enq.sender_name, "email": enq.sender_email, "phone": enq.sender_phone},
            "subject": enq.subject,
            "body_text": enq.body_text,
            "workflow_status": enq.workflow_status,
            "created_at": enq.created_at,
            "updated_at": enq.updated_at,
            "ai_understanding": {
                **(ai_run.output_json if ai_run and ai_run.output_json else {}),
                "provider": ai_run.provider if ai_run else None,
                "model": ai_run.model if ai_run else None,
                "latency_ms": ai_run.latency_ms if ai_run else 0,
                "input_tokens": ai_run.input_tokens if ai_run else 0,
                "output_tokens": ai_run.output_tokens if ai_run else 0,
                "error_code": ai_run.error_code if ai_run else None,
            } if ai_run else None,
            "crm_resolution": {
                "status": crm_res.status,
                "method": crm_res.method,
                "selected_customer_id": crm_res.selected_customer_id,
                "candidates": crm_res.candidate_json,
                "conflict_flags": crm_res.conflict_flags_json,
            } if crm_res else None,
            "draft": {
                "id": draft.id,
                "draft_type": draft.draft_type,
                "content": draft.content,
                "grounding_refs": draft.grounding_refs_json,
                "requires_approval": draft.requires_approval,
                "status": draft.status,
            } if draft else None,
            "review_tasks": [
                {
                    "id": t.id,
                    "task_type": t.task_type,
                    "reason_code": t.reason_code,
                    "priority": t.priority,
                    "assigned_to": t.assigned_to,
                    "status": t.status,
                    "created_at": t.created_at,
                    "resolved_at": t.resolved_at,
                }
                for t in tasks
            ],
            "attachments": [
                {"id": a.id, "filename": a.filename, "content_type": a.content_type, "sha256": a.sha256}
                for a in attachments
            ],
            "timeline": [
                {"step_name": s.step_name, "status": s.status, "started_at": s.started_at, "completed_at": s.completed_at}
                for s in steps
            ],
        }


# Review & Approval Endpoints
@app.get("/api/v1/reviews", tags=["Reviews"])
async def list_reviews(status: str = "OPEN"):
    async with get_db_session() as session:
        stmt = (
            select(ReviewTaskModel, EnquiryModel)
            .join(EnquiryModel, ReviewTaskModel.enquiry_id == EnquiryModel.id)
            .order_by(desc(ReviewTaskModel.created_at))
        )
        if status:
            stmt = stmt.where(ReviewTaskModel.status == status)
        results = (await session.execute(stmt)).all()
        return [
            {
                "review_id": task.id,
                "enquiry_id": enq.id,
                "task_type": task.task_type,
                "reason_code": task.reason_code,
                "priority": task.priority,
                "assigned_to": task.assigned_to,
                "status": task.status,
                "created_at": task.created_at,
                "enquiry": {
                    "source_channel": enq.source_channel,
                    "sender_name": enq.sender_name,
                    "sender_email": enq.sender_email,
                    "subject": enq.subject,
                    "workflow_status": enq.workflow_status,
                },
            }
            for task, enq in results
        ]


@app.get("/api/v1/reviews/{review_id}", tags=["Reviews"])
async def get_review_detail(review_id: str):
    async with get_db_session() as session:
        stmt = select(ReviewTaskModel).where(ReviewTaskModel.id == review_id)
        task = (await session.execute(stmt)).scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Review task not found")
        enquiry_detail = await get_enquiry(task.enquiry_id)
        return {"task": task.__dict__, "enquiry": enquiry_detail}


@app.post("/api/v1/reviews/{review_id}/decision", tags=["Reviews"])
async def submit_review_decision(review_id: str, payload: DecisionPayload):
    try:
        res = await pipeline.execute_review_decision(
            review_task_id=review_id,
            decision=payload.decision.value,
            actor_id=payload.actor_id,
            actor_role=payload.actor_role,
            reason=payload.reason,
            edited_content=payload.edited_content,
        )
        return res
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))


# Retry Endpoint
@app.post("/api/v1/enquiries/{enquiry_id}/retry", tags=["Enquiries"])
async def retry_enquiry(enquiry_id: str):
    await pipeline.execute_workflow(enquiry_id)
    return {"status": "RETRY_TRIGGERED", "enquiry_id": enquiry_id}


# Audit Endpoint
@app.get("/api/v1/enquiries/{enquiry_id}/audit", tags=["Audit"])
async def get_enquiry_audit(enquiry_id: str):
    async with get_db_session() as session:
        stmt = select(AuditEventModel).where(AuditEventModel.enquiry_id == enquiry_id).order_by(AuditEventModel.timestamp.asc())
        events = (await session.execute(stmt)).scalars().all()
        chain_valid = await AuditService.verify_chain(session, enquiry_id)
        return {
            "enquiry_id": enquiry_id,
            "chain_valid": chain_valid,
            "total_events": len(events),
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "actor_type": e.actor_type,
                    "actor_id": e.actor_id,
                    "timestamp": e.timestamp,
                    "outcome": e.outcome,
                    "metadata": e.metadata_json,
                    "previous_event_hash": e.previous_event_hash,
                    "event_hash": e.event_hash,
                }
                for e in events
            ],
        }
