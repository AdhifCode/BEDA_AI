import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    Index,
)
from sqlalchemy.orm import relationship
from db.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class EnquiryModel(Base):
    __tablename__ = "enquiries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = Column(String(255), unique=True, nullable=False, index=True)
    source_channel = Column(String(50), nullable=False)
    source_message_id = Column(String(255), nullable=True)
    received_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    sender_name = Column(String(255), nullable=True)
    sender_email = Column(String(255), nullable=True, index=True)
    sender_phone = Column(String(50), nullable=True, index=True)
    subject = Column(Text, nullable=True)
    body_text = Column(Text, nullable=False)
    workflow_status = Column(String(50), nullable=False, default="RECEIVED", index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    version = Column(Integer, default=1, nullable=False)

    # Relationships
    raw_events = relationship("RawEventModel", back_populates="enquiry", cascade="all, delete-orphan")
    attachments = relationship("AttachmentModel", back_populates="enquiry", cascade="all, delete-orphan")
    ai_runs = relationship("AIRunModel", back_populates="enquiry", cascade="all, delete-orphan")
    crm_resolutions = relationship("CRMResolutionModel", back_populates="enquiry", cascade="all, delete-orphan")
    drafts = relationship("DraftModel", back_populates="enquiry", cascade="all, delete-orphan")
    review_tasks = relationship("ReviewTaskModel", back_populates="enquiry", cascade="all, delete-orphan")
    workflow_steps = relationship("WorkflowStepModel", back_populates="enquiry", cascade="all, delete-orphan")
    audit_events = relationship("AuditEventModel", back_populates="enquiry", cascade="all, delete-orphan")


class RawEventModel(Base):
    __tablename__ = "raw_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False, index=True)
    received_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    source_channel = Column(String(50), nullable=False)
    retention_class = Column(String(50), default="standard", nullable=False)

    enquiry = relationship("EnquiryModel", back_populates="raw_events")


class AttachmentModel(Base):
    __tablename__ = "attachments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), default="text/plain", nullable=False)
    storage_ref = Column(String(500), nullable=True)
    sha256 = Column(String(64), nullable=True)
    sanitized = Column(Boolean, default=True, nullable=False)
    extracted_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    enquiry = relationship("EnquiryModel", back_populates="attachments")


class AIRunModel(Base):
    __tablename__ = "ai_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    task = Column(String(100), nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    input_reference = Column(Text, nullable=True)
    output_json = Column(JSON, nullable=False)
    validation_status = Column(String(50), default="VALID", nullable=False)
    confidence = Column(Float, default=1.0, nullable=False)
    latency_ms = Column(Integer, default=0, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    error_code = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    enquiry = relationship("EnquiryModel", back_populates="ai_runs")


class CRMResolutionModel(Base):
    __tablename__ = "crm_resolutions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(50), nullable=False)  # MATCHED | NEW | AMBIGUOUS | NONE
    method = Column(String(50), nullable=False)
    selected_customer_id = Column(String(50), nullable=True)
    candidate_json = Column(JSON, nullable=False)
    conflict_flags_json = Column(JSON, nullable=True)
    decision_actor = Column(String(100), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    enquiry = relationship("EnquiryModel", back_populates="crm_resolutions")


class DraftModel(Base):
    __tablename__ = "drafts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    draft_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    grounding_refs_json = Column(JSON, nullable=False)
    requires_approval = Column(Boolean, default=True, nullable=False)
    status = Column(String(50), default="PENDING", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    enquiry = relationship("EnquiryModel", back_populates="drafts")


class ReviewTaskModel(Base):
    __tablename__ = "review_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    task_type = Column(String(50), nullable=False)
    reason_code = Column(String(100), nullable=False)
    priority = Column(String(20), default="NORMAL", nullable=False)
    assigned_to = Column(String(100), nullable=True)
    status = Column(String(50), default="OPEN", nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    enquiry = relationship("EnquiryModel", back_populates="review_tasks")


class WorkflowStepModel(Base):
    __tablename__ = "workflow_steps"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    step_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    attempt = Column(Integer, default=1, nullable=False)
    input_ref = Column(Text, nullable=True)
    output_ref = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    enquiry = relationship("EnquiryModel", back_populates="workflow_steps")


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String(100), nullable=False)
    aggregate_id = Column(String(100), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enquiry_id = Column(String(36), ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    actor_type = Column(String(50), nullable=False)
    actor_id = Column(String(100), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    model_provider = Column(String(50), nullable=True)
    model_version = Column(String(50), nullable=True)
    input_reference = Column(Text, nullable=True)
    decision_reference = Column(Text, nullable=True)
    outcome = Column(String(50), nullable=False)
    metadata_json = Column(JSON, nullable=False)
    previous_event_hash = Column(String(64), nullable=True)
    event_hash = Column(String(64), nullable=False)

    enquiry = relationship("EnquiryModel", back_populates="audit_events")


class UserModel(Base):
    __tablename__ = "users"

    id = Column(String(100), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # admin, reviewer, operator, audit_reader
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class CRMCustomerModel(Base):
    __tablename__ = "crm_customers"

    customer_id = Column(String(50), primary_key=True)
    company_name = Column(String(255), nullable=False)
    contact_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(50), nullable=True, index=True)
    location = Column(String(255), nullable=True)
    relationship_type = Column(String(50), nullable=False)  # Prospect, Client, Lead, Partner
    interest_product = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)  # Open, Active, New
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
