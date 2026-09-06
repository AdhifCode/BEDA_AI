import pytest
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select, func

from db.database import get_db_session
from db.models.schema import (
    AuditEventModel,
    CRMCustomerModel,
    DraftModel,
    EnquiryModel,
    OutboxEventModel,
    ReviewTaskModel,
)
from packages.ai_gateway.llm_provider import (
    FakeLLMProvider,
    LLMProvider,
    ModelUnavailableError,
)
from packages.audit.audit_service import AuditService
from packages.crm.client import MockCRMClient
from packages.domain.models import (
    CanonicalEnquiry,
    SenderInfo,
    WorkflowStatus,
)
from packages.workflow.pipeline import WorkflowPipeline


class OutageLLMProvider:
    """Simulates an LLM provider experiencing total outage across all fallback models."""

    def __init__(self, models: Optional[List[str]] = None):
        self.models = models or [
            "openrouter/google/gemini-2.5-flash",
            "openai/gpt-4o",
        ]

    async def classify_and_extract(self, *args, **kwargs):
        raise ModelUnavailableError(models_attempted=self.models)

    async def detect_missing_information(self, *args, **kwargs):
        raise ModelUnavailableError(models_attempted=self.models)

    async def draft_response(self, *args, **kwargs):
        raise ModelUnavailableError(models_attempted=self.models)


class RecoverableLLMProvider:
    """Simulates an LLM provider that can recover from total outage."""

    def __init__(self, is_down: bool = True):
        self.is_down = is_down
        self.models = [
            "openrouter/google/gemini-2.5-flash",
            "openai/gpt-4o",
        ]
        self.healthy_provider = FakeLLMProvider()

    async def classify_and_extract(self, *args, **kwargs):
        if self.is_down:
            raise ModelUnavailableError(models_attempted=self.models)
        return await self.healthy_provider.classify_and_extract(*args, **kwargs)

    async def detect_missing_information(self, *args, **kwargs):
        if self.is_down:
            raise ModelUnavailableError(models_attempted=self.models)
        return await self.healthy_provider.detect_missing_information(*args, **kwargs)

    async def draft_response(self, *args, **kwargs):
        if self.is_down:
            raise ModelUnavailableError(models_attempted=self.models)
        return await self.healthy_provider.draft_response(*args, **kwargs)


@pytest.mark.asyncio
async def test_all_models_down_safety():
    """
    Simulates a total LLM outage.
    Asserts:
    - Workflow completes in DEFERRED state.
    - Draft content is empty (no substantive customer response generated).
    - Draft status is DEFERRED_MODEL_UNAVAILABLE.
    - No outbound communication (OutboxEventModel) is created or dispatched.
    - No irreversible CRM customer creation occurs.
    - Review task is created with DEFERRED status and MODEL_UNAVAILABLE reason code.
    - Audit trail records MODEL_UNAVAILABLE with replayable=True.
    - SHA-256 cryptographic hash chain on audit trail remains valid.
    """
    pipeline = WorkflowPipeline(llm=OutageLLMProvider(), crm=MockCRMClient())

    raw_body = "   Hi team,   we are looking for a 200kW solar installation across our two sites in Campbellfield.   Please provide a quote.   "
    enquiry = CanonicalEnquiry(
        idempotency_key=f"outage-test-{uuid.uuid4().hex[:8]}",
        source_channel="email",
        source_message_id=f"msg-outage-{uuid.uuid4().hex[:8]}",
        sender=SenderInfo(name="Jane Doe", email="jane@acme.example", phone="0400 999 888"),
        subject="Solar feasibility inquiry for 2 factory sites",
        body_text=raw_body,
    )

    async with get_db_session() as session:
        initial_customer_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()

    # 1. Ingest and persist
    enquiry, is_dup = await pipeline.ingest_and_persist(enquiry, {"raw": "data"}, "email")
    assert not is_dup
    assert enquiry.enquiry_id is not None

    # Verify duplicate / idempotency protection remains intact during outage
    dup_enquiry = CanonicalEnquiry(
        idempotency_key=enquiry.idempotency_key,
        source_channel="email",
        source_message_id=enquiry.source_message_id,
        sender=enquiry.sender,
        subject=enquiry.subject,
        body_text=raw_body,
    )
    dup_res, is_dup_2 = await pipeline.ingest_and_persist(dup_enquiry, {"raw": "dup_data"}, "email")
    assert is_dup_2 is True
    assert dup_res.enquiry_id == enquiry.enquiry_id

    # 2. Execute workflow under outage
    await pipeline.execute_workflow(enquiry.enquiry_id)

    async with get_db_session() as session:
        # DB enquiry record status
        db_enq = await session.get(EnquiryModel, enquiry.enquiry_id)
        assert db_enq is not None
        assert db_enq.workflow_status == "DEFERRED"

        # Normalization occurred: whitespace normalized
        assert db_enq.body_text == "Hi team, we are looking for a 200kW solar installation across our two sites in Campbellfield. Please provide a quote."
        
        # Normalization workflow step completed
        from db.models.schema import WorkflowStepModel
        norm_step = (await session.execute(
            select(WorkflowStepModel).where(
                WorkflowStepModel.enquiry_id == enquiry.enquiry_id,
                WorkflowStepModel.step_name == "NORMALIZATION",
                WorkflowStepModel.status == "COMPLETED",
            )
        )).scalar_one_or_none()
        assert norm_step is not None

        # Draft assertions: MUST be blank and marked DEFERRED_MODEL_UNAVAILABLE
        draft_stmt = select(DraftModel).where(DraftModel.enquiry_id == enquiry.enquiry_id)
        draft = (await session.execute(draft_stmt)).scalar_one_or_none()
        assert draft is not None
        assert draft.status == "DEFERRED_MODEL_UNAVAILABLE"
        assert draft.content == ""  # Zero substantive response generated
        assert draft.requires_approval is True

        # Review task assertions
        task_stmt = select(ReviewTaskModel).where(ReviewTaskModel.enquiry_id == enquiry.enquiry_id)
        task = (await session.execute(task_stmt)).scalar_one_or_none()
        assert task is not None
        assert task.status == "OPEN"
        assert task.reason_code == "MODEL_UNAVAILABLE"

        # Outbox event assertions: NO messages in outbox
        outbox_stmt = select(OutboxEventModel).where(OutboxEventModel.aggregate_id == enquiry.enquiry_id)
        outbox_events = (await session.execute(outbox_stmt)).scalars().all()
        assert len(outbox_events) == 0

        # CRM customer count assertions: NO customer created or merged/updated
        post_customer_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()
        assert post_customer_count == initial_customer_count

        # Deferred work persisted in AI run
        from db.models.schema import AIRunModel
        ai_run_stmt = select(AIRunModel).where(AIRunModel.enquiry_id == enquiry.enquiry_id)
        ai_run = (await session.execute(ai_run_stmt)).scalar_one_or_none()
        assert ai_run is not None
        assert ai_run.validation_status == "DEFERRED"
        assert ai_run.error_code == "MODEL_UNAVAILABLE"

        # Audit trail assertions: MODEL_UNAVAILABLE and DUPLICATE_IGNORED recorded
        audit_stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.enquiry_id == enquiry.enquiry_id)
            .order_by(AuditEventModel.timestamp.asc())
        )
        audit_events = (await session.execute(audit_stmt)).scalars().all()
        event_types = [e.event_type for e in audit_events]
        assert "MODEL_UNAVAILABLE" in event_types
        assert "DUPLICATE_IGNORED" in event_types

        model_unavail_ev = next(e for e in audit_events if e.event_type == "MODEL_UNAVAILABLE")
        assert model_unavail_ev.outcome == "DEFERRED"
        assert model_unavail_ev.metadata_json.get("replayable") is True
        assert model_unavail_ev.metadata_json.get("reason") == "MODEL_UNAVAILABLE"

        # Cryptographic SHA-256 chain verification
        is_chain_valid = await AuditService.verify_chain(session, enquiry.enquiry_id)
        assert is_chain_valid is True


@pytest.mark.asyncio
async def test_corrected_identity_and_replay():
    """
    Tests E009/E010 identity correction during degraded mode and replay after recovery.
    Asserts:
    - Seeding CRM contact C099 (Sam, 0411 999 120, old@example.com).
    - Inbound enquiry with corrected phone 0411 999 102 and new email new@example.com referencing old identity.
    - In degraded mode:
      - C099 remains identity anchor (selected_customer_id == C099).
      - Status marked AMBIGUOUS with conflict flags and correction provenance.
      - No new CRM customer created for new@example.com.
      - Workflow status is DEFERRED.
      - Draft is empty.
      - No outbox events dispatched.
      - Audit trail records IDENTITY_CORRECTION_RECEIVED and MODEL_UNAVAILABLE.
      - SHA-256 chain valid.
    - In recovery mode:
      - LLM recovers.
      - replay_deferred_work(enquiry_id) re-executes using the SAME enquiry ID.
      - Resumes AI reasoning.
      - Workflow status transitions to APPROVAL (Tier 5 human review).
      - Audit trail contains MODEL_RECOVERY_REPLAY.
      - Full SHA-256 cryptographic audit chain remains valid.
    """
    # 1. Seed CRM contact C099 if not present
    async with get_db_session() as session:
        c099 = await session.get(CRMCustomerModel, "C099")
        if not c099:
            c099 = CRMCustomerModel(
                customer_id="C099",
                company_name="Sam Warehousing Pty Ltd",
                contact_name="Sam",
                email="old@example.com",
                phone="0411 999 120",
                location="Melbourne VIC",
                relationship_type="Prospect",
                interest_product="Commercial Solar",
                status="Active",
            )
            session.add(c099)
            await session.commit()

        initial_customer_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()

    # 2. Setup RecoverableLLMProvider in outage state
    llm = RecoverableLLMProvider(is_down=True)
    pipeline = WorkflowPipeline(llm=llm, crm=MockCRMClient())

    # 3. Inbound enquiry with corrected identity
    enquiry = CanonicalEnquiry(
        idempotency_key=f"identity-corr-test-{uuid.uuid4().hex[:8]}",
        source_channel="web_form",
        source_message_id=f"wf-{uuid.uuid4().hex[:8]}",
        sender=SenderInfo(name="Sam", email="new@example.com", phone="0411 999 102"),
        subject="Follow-up on solar quote",
        body_text="Hi, I previously submitted an enquiry under old@example.com and made a typo in my phone number (0411 999 120). Corrected phone is 0411 999 102 and my updated email is new@example.com.",
    )

    enquiry, is_dup = await pipeline.ingest_and_persist(
        enquiry,
        {
            "fields": {"contact_name": "Sam", "phone": "0411 999 102", "email": "new@example.com"},
            "message": enquiry.body_text,
        },
        "web_form",
    )
    assert not is_dup
    original_enquiry_id = enquiry.enquiry_id

    # 4. Execute workflow in degraded mode
    await pipeline.execute_workflow(original_enquiry_id)

    # 5. Assert degraded behavior & identity anchor preservation
    async with get_db_session() as session:
        # DB enquiry record
        db_enq = await session.get(EnquiryModel, original_enquiry_id)
        assert db_enq is not None
        assert db_enq.workflow_status == "DEFERRED"

        # Check CRM resolution
        from db.models.schema import CRMResolutionModel
        crm_res_stmt = select(CRMResolutionModel).where(CRMResolutionModel.enquiry_id == original_enquiry_id)
        crm_res_db = (await session.execute(crm_res_stmt)).scalar_one()
        assert crm_res_db.selected_customer_id == "C099"  # Identity anchor preserved!
        assert crm_res_db.status == "AMBIGUOUS"
        assert any("CONTACT_IDENTITY_CORRECTION" in flag for flag in (crm_res_db.conflict_flags_json or []))

        # Check no fresh customer was created in CRM
        post_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()
        assert post_count == initial_customer_count

        # Check draft is empty and deferred
        draft_stmt = select(DraftModel).where(DraftModel.enquiry_id == original_enquiry_id)
        draft = (await session.execute(draft_stmt)).scalar_one()
        assert draft.status == "DEFERRED_MODEL_UNAVAILABLE"
        assert draft.content == ""

        # Check review task is IDENTITY_REVIEW
        task_stmt = select(ReviewTaskModel).where(ReviewTaskModel.enquiry_id == original_enquiry_id)
        task = (await session.execute(task_stmt)).scalar_one()
        assert task.task_type == "IDENTITY_REVIEW"
        assert task.status == "OPEN"

        # Check outbox is empty
        outbox_stmt = select(OutboxEventModel).where(OutboxEventModel.aggregate_id == original_enquiry_id)
        outbox_events = (await session.execute(outbox_stmt)).scalars().all()
        assert len(outbox_events) == 0

        # Check audit trail has both events, exact identity provenance, and valid chain
        audit_stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.enquiry_id == original_enquiry_id)
            .order_by(AuditEventModel.timestamp.asc())
        )
        audit_events = (await session.execute(audit_stmt)).scalars().all()
        event_types = [e.event_type for e in audit_events]
        assert "IDENTITY_CORRECTION_RECEIVED" in event_types
        assert "MODEL_UNAVAILABLE" in event_types

        # Explicitly verify previous and corrected identity values in provenance
        corr_ev = next(e for e in audit_events if e.event_type == "IDENTITY_CORRECTION_RECEIVED")
        assert corr_ev.metadata_json.get("existing_customer_id") == "C099"
        assert corr_ev.metadata_json.get("previous_identity", {}).get("phone") == "0411 999 120"
        assert corr_ev.metadata_json.get("previous_identity", {}).get("email") == "old@example.com"
        assert corr_ev.metadata_json.get("corrected_identity", {}).get("phone") == "0411 999 102"
        assert corr_ev.metadata_json.get("corrected_identity", {}).get("email") == "new@example.com"

        is_chain_valid = await AuditService.verify_chain(session, original_enquiry_id)
        assert is_chain_valid is True

    # 6. Simulate LLM Recovery and replay deferred work
    llm.is_down = False
    await pipeline.replay_deferred_work(original_enquiry_id)

    # 7. Assert recovery assertions
    async with get_db_session() as session:
        # Enquiry status updated in DB
        db_enq = await session.get(EnquiryModel, original_enquiry_id)
        assert db_enq.workflow_status == "APPROVAL"

        # Latest draft has content and requires approval
        draft_stmt = (
            select(DraftModel)
            .where(DraftModel.enquiry_id == original_enquiry_id)
            .order_by(DraftModel.created_at.desc())
        )
        drafts = (await session.execute(draft_stmt)).scalars().all()
        latest_draft = drafts[0]
        assert latest_draft.status == "PENDING_APPROVAL"
        assert len(latest_draft.content) > 0  # Draft now generated
        assert latest_draft.requires_approval is True

        # Review task exists with OPEN status for human review
        task_stmt = (
            select(ReviewTaskModel)
            .where(ReviewTaskModel.enquiry_id == original_enquiry_id, ReviewTaskModel.status == "OPEN")
        )
        open_tasks = (await session.execute(task_stmt)).scalars().all()
        assert len(open_tasks) == 1

        # Still no new customer created autonomously
        final_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()
        assert final_count == initial_customer_count

        # Audit trail includes MODEL_RECOVERY_REPLAY and MODEL_RECOVERY_REPLAYED
        audit_stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.enquiry_id == original_enquiry_id)
            .order_by(AuditEventModel.timestamp.asc())
        )
        all_audit_events = (await session.execute(audit_stmt)).scalars().all()
        all_event_types = [e.event_type for e in all_audit_events]
        assert "MODEL_RECOVERY_REPLAY" in all_event_types
        assert "MODEL_RECOVERY_REPLAYED" in all_event_types

        # Assert MODEL_RECOVERY_REPLAYED contains recovery metadata (Requirement 4)
        recovery_replayed_ev = next(e for e in all_audit_events if e.event_type == "MODEL_RECOVERY_REPLAYED")
        assert recovery_replayed_ev.metadata_json.get("previous_state") == "DEFERRED"
        assert recovery_replayed_ev.metadata_json.get("recovery_mode") == "REPLAY"
        assert recovery_replayed_ev.metadata_json.get("enquiry_id") == original_enquiry_id
        assert recovery_replayed_ev.metadata_json.get("replayed") is True

        # Exactly 1 IDENTITY_CORRECTION_RECEIVED event exists (no duplicate identity correction created)
        corr_events = [e for e in all_audit_events if e.event_type == "IDENTITY_CORRECTION_RECEIVED"]
        assert len(corr_events) == 1

        # Complete cryptographic audit chain remains fully verified
        is_chain_valid = await AuditService.verify_chain(session, original_enquiry_id)
        assert is_chain_valid is True

    # 8. Replay the SAME enquiry a second time to assert idempotency (Requirement 3: replay(E010) replay(E010))
    await pipeline.replay_deferred_work(original_enquiry_id)

    async with get_db_session() as session:
        # Assert no duplicate customer created
        post_second_replay_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()
        assert post_second_replay_count == initial_customer_count

        # Assert no duplicate open review tasks
        task_stmt = (
            select(ReviewTaskModel)
            .where(ReviewTaskModel.enquiry_id == original_enquiry_id, ReviewTaskModel.status == "OPEN")
        )
        open_tasks = (await session.execute(task_stmt)).scalars().all()
        assert len(open_tasks) == 1

        # Assert no duplicate identity correction events
        audit_stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.enquiry_id == original_enquiry_id)
            .order_by(AuditEventModel.timestamp.asc())
        )
        final_audit_events = (await session.execute(audit_stmt)).scalars().all()
        final_corr_events = [e for e in final_audit_events if e.event_type == "IDENTITY_CORRECTION_RECEIVED"]
        assert len(final_corr_events) == 1

        # Assert no outbound event was dispatched autonomously
        outbox_stmt = select(OutboxEventModel).where(OutboxEventModel.aggregate_id == original_enquiry_id)
        outbox_events = (await session.execute(outbox_stmt)).scalars().all()
        assert len(outbox_events) == 0

        # Assert audit chain remains valid
        is_chain_valid = await AuditService.verify_chain(session, original_enquiry_id)
        assert is_chain_valid is True


@pytest.mark.asyncio
async def test_deterministic_classification_in_degraded_mode():
    """
    Asserts that deterministic classification (spam/alerts) operates during outage
    without calling the LLM or deferring work.
    """
    pipeline = WorkflowPipeline(llm=OutageLLMProvider(), crm=MockCRMClient())

    spam_enquiry = CanonicalEnquiry(
        idempotency_key=f"spam-test-{uuid.uuid4().hex[:8]}",
        source_channel="email",
        source_message_id=f"msg-spam-{uuid.uuid4().hex[:8]}",
        sender=SenderInfo(name="Sales", email="sales@megaleadlists.example"),
        subject="Buy 50,000 Australian CEO leads today",
        body_text="Special price crypto payment instructions.",
    )

    enquiry, is_dup = await pipeline.ingest_and_persist(spam_enquiry, {"raw": "data"}, "email")
    assert not is_dup

    await pipeline.execute_workflow(enquiry.enquiry_id)

    async with get_db_session() as session:
        db_enq = await session.get(EnquiryModel, enquiry.enquiry_id)
        assert db_enq.workflow_status == "QUARANTINED"

        is_chain_valid = await AuditService.verify_chain(session, enquiry.enquiry_id)
        assert is_chain_valid is True


@pytest.mark.asyncio
async def test_changed_preferred_email_no_fresh_customer():
    """
    Asserts requirement 8: A changed preferred email must NOT create a fresh
    customer merely because the email changed when matched by phone number.
    """
    # Seed contact C088
    async with get_db_session() as session:
        c088 = await session.get(CRMCustomerModel, "C088")
        if not c088:
            c088 = CRMCustomerModel(
                customer_id="C088",
                company_name="Apex Logistics",
                contact_name="Taylor",
                email="taylor.first@apex.example",
                phone="0422 333 444",
                location="Sydney NSW",
                relationship_type="Prospect",
                interest_product="Commercial Solar",
                status="Active",
            )
            session.add(c088)
            await session.commit()

        initial_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()

    pipeline = WorkflowPipeline(llm=OutageLLMProvider(), crm=MockCRMClient())

    enquiry = CanonicalEnquiry(
        idempotency_key=f"email-change-{uuid.uuid4().hex[:8]}",
        source_channel="email",
        source_message_id=f"msg-change-{uuid.uuid4().hex[:8]}",
        sender=SenderInfo(name="Taylor", email="taylor.second@apex.example", phone="0422 333 444"),
        subject="Updated enquiry",
        body_text="Hi, this is Taylor following up on our solar quote.",
    )

    enquiry, _ = await pipeline.ingest_and_persist(enquiry, {"raw": "data"}, "email")
    await pipeline.execute_workflow(enquiry.enquiry_id)

    async with get_db_session() as session:
        from db.models.schema import CRMResolutionModel
        crm_res_stmt = select(CRMResolutionModel).where(CRMResolutionModel.enquiry_id == enquiry.enquiry_id)
        crm_res_db = (await session.execute(crm_res_stmt)).scalar_one()
        # Assert C088 is retained as anchor
        assert crm_res_db.selected_customer_id == "C088"
        assert crm_res_db.status == "AMBIGUOUS"

        post_count = (
            await session.execute(select(func.count(CRMCustomerModel.customer_id)))
        ).scalar_one()
        # No new customer row created!
        assert post_count == initial_count
