from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, ConfigDict


class SourceChannel(str, Enum):
    EMAIL = "email"
    WEB_FORM = "web_form"
    MESSAGING = "messaging"
    INTERNAL = "internal"


class WorkflowStatus(str, Enum):
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    UNDERSTANDING = "UNDERSTANDING"
    VALIDATED = "VALIDATED"
    AI_REVIEW = "AI_REVIEW"
    ENRICHMENT = "ENRICHMENT"
    CRM_RESOLUTION = "CRM_RESOLUTION"
    CRM_REVIEW = "CRM_REVIEW"
    CRM_CREATE_PENDING = "CRM_CREATE_PENDING"
    DRAFTED = "DRAFTED"
    APPROVAL = "APPROVAL"
    ROUTED = "ROUTED"
    EXECUTE = "EXECUTE"
    COMPLETED = "COMPLETED"
    DEAD_LETTER = "DEAD_LETTER"
    QUARANTINED = "QUARANTINED"


class CRMResolutionStatus(str, Enum):
    MATCHED = "MATCHED"
    NEW = "NEW"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


class CRMResolutionMethod(str, Enum):
    EXACT_EMAIL = "exact_email"
    CUSTOMER_ID = "customer_id"
    PHONE = "phone"
    COMPANY_DOMAIN = "company_domain"
    FUZZY = "fuzzy"
    MANUAL = "manual"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    EDIT_AND_APPROVE = "EDIT_AND_APPROVE"


class SenderInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class AttachmentInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid4()))
    filename: str
    content_type: str = "text/plain"
    storage_ref: Optional[str] = None
    sha256: Optional[str] = None
    sanitized: bool = True
    extracted_text: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str
    confidence: float = 1.0
    reason_code: str
    provenance: List[str] = Field(default_factory=list)


class ExtractedInformation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    location: Optional[str] = None
    intent_summary: str = ""
    requested_products_or_services: List[str] = Field(default_factory=list)
    annual_or_monthly_energy_usage: Optional[str] = None
    site_count: Optional[int] = None
    budget: Optional[str] = None
    timeframe: Optional[str] = None
    project_size: Optional[str] = None
    commercial_commitment_requested: bool = False
    discrepancy_amount: Optional[str] = None
    key_constraints: List[str] = Field(default_factory=list)
    provenance: List[str] = Field(default_factory=list)


class MissingInfoItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    field_name: str
    description: str
    required_for_category: str
    reason: str


class CRMCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    customer_id: str
    company_name: str
    contact_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    match_score: float = 1.0
    match_reasons: List[str] = Field(default_factory=list)


class CRMResolutionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: CRMResolutionStatus
    method: CRMResolutionMethod
    selected_customer_id: Optional[str] = None
    candidates: List[CRMCandidate] = Field(default_factory=list)
    conflict_flags: List[str] = Field(default_factory=list)
    decision_actor: Optional[str] = None
    decided_at: Optional[datetime] = None


class DraftResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid4()))
    draft_type: str  # response, clarification, acknowledgment, internal_alert
    content: str
    grounding_refs: List[str] = Field(default_factory=list)
    requires_approval: bool = False
    status: str = "PENDING_APPROVAL"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RoutingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    assigned_owner: str
    role: str
    reason: str
    requires_review: bool = False


class CanonicalEnquiry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enquiry_id: str = Field(default_factory=lambda: str(uuid4()))
    idempotency_key: str
    source_channel: SourceChannel
    source_message_id: Optional[str] = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: SenderInfo = Field(default_factory=SenderInfo)
    subject: Optional[str] = None
    body_text: str
    attachments: List[AttachmentInfo] = Field(default_factory=list)
    classification: Optional[ClassificationResult] = None
    extracted: Optional[ExtractedInformation] = None
    missing_information: List[MissingInfoItem] = Field(default_factory=list)
    enrichment: Optional[Dict[str, Any]] = None
    crm_resolution: Optional[CRMResolutionResult] = None
    draft: Optional[DraftResult] = None
    routing: Optional[RoutingResult] = None
    workflow_status: WorkflowStatus = WorkflowStatus.RECEIVED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid4()))
    enquiry_id: str
    task_type: str  # APPROVAL, CRM_REVIEW, AI_REVIEW, TECHNICAL_REVIEW, NON_SALES_REVIEW, ACCOUNTS_REVIEW, IDENTITY_REVIEW
    reason_code: str
    priority: str = "NORMAL"  # LOW, NORMAL, HIGH, URGENT
    assigned_to: Optional[str] = None
    status: str = "OPEN"  # OPEN, RESOLVED, REJECTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid4()))
    enquiry_id: str
    event_type: str
    actor_type: str  # SYSTEM, LLM, HUMAN
    actor_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_provider: Optional[str] = None
    model_version: Optional[str] = None
    input_reference: Optional[str] = None
    decision_reference: Optional[str] = None
    outcome: str
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    previous_event_hash: Optional[str] = None
    event_hash: str = ""
