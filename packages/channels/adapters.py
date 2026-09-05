import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field, ConfigDict

from packages.domain.models import (
    AttachmentInfo,
    CanonicalEnquiry,
    SenderInfo,
    SourceChannel,
    WorkflowStatus,
)
from packages.validation.normalizer import (
    normalize_email,
    normalize_phone,
    normalize_whitespace,
)


def compute_idempotency_key(
    channel: str,
    source_message_id: Optional[str],
    sender_email: Optional[str],
    sender_phone: Optional[str],
    body_text: str,
) -> str:
    if source_message_id and source_message_id.strip():
        return f"{channel}:{source_message_id.strip()}"
    raw = f"{channel}:{sender_email or ''}:{sender_phone or ''}:{normalize_whitespace(body_text)}"
    return f"{channel}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def resolve_attachment_content(filename: str, provided_text: Optional[str] = None) -> str:
    if provided_text:
        return provided_text
    # Check fixtures/documents/
    doc_path = os.path.join("fixtures", "documents", filename)
    if os.path.exists(doc_path):
        with open(doc_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


class EmailPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    message_id: Optional[str] = None
    from_address: str = Field(alias="from")
    subject: Optional[str] = None
    body: str
    attachments: List[str] = Field(default_factory=list)


class WebFormPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    submission_id: Optional[str] = None
    fields: Dict[str, Any] = Field(default_factory=dict)
    message: str
    attachments: List[str] = Field(default_factory=list)


class MessagingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    message_id: Optional[str] = None
    sender: Dict[str, Any] = Field(default_factory=dict)
    text: str
    attachments: List[str] = Field(default_factory=list)


class ChannelAdapter:
    @staticmethod
    def from_email(payload: EmailPayload) -> CanonicalEnquiry:
        # Parse from string e.g. "Amelia Grant <amelia.grant@humelogistics.example>"
        from_str = payload.from_address
        sender_name = None
        sender_email = None
        if "<" in from_str and ">" in from_str:
            sender_name = from_str.split("<")[0].strip().strip('"')
            sender_email = from_str.split("<")[1].split(">")[0].strip()
        else:
            sender_email = from_str.strip()
            
        sender_email = normalize_email(sender_email)
        cleaned_body = normalize_whitespace(payload.body)
        idempotency_key = compute_idempotency_key(
            SourceChannel.EMAIL.value,
            payload.message_id,
            sender_email,
            None,
            cleaned_body,
        )

        attachments: List[AttachmentInfo] = []
        for att in payload.attachments:
            text = resolve_attachment_content(att)
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
            attachments.append(
                AttachmentInfo(
                    filename=att,
                    content_type="text/plain",
                    storage_ref=f"fixtures/documents/{att}",
                    sha256=sha,
                    extracted_text=text,
                )
            )

        return CanonicalEnquiry(
            idempotency_key=idempotency_key,
            source_channel=SourceChannel.EMAIL,
            source_message_id=payload.message_id,
            sender=SenderInfo(name=sender_name, email=sender_email),
            subject=payload.subject,
            body_text=cleaned_body,
            attachments=attachments,
            workflow_status=WorkflowStatus.RECEIVED,
        )

    @staticmethod
    def from_web_form(payload: WebFormPayload) -> CanonicalEnquiry:
        fields = payload.fields
        name = fields.get("contact_name") or fields.get("name")
        email = normalize_email(fields.get("email"))
        phone = normalize_phone(fields.get("phone"))
        company = fields.get("company_name") or fields.get("company")
        
        body_text = payload.message
        if company:
            body_text = f"Company: {company}\n{body_text}"

        cleaned_body = normalize_whitespace(body_text)
        idempotency_key = compute_idempotency_key(
            SourceChannel.WEB_FORM.value,
            payload.submission_id,
            email,
            phone,
            cleaned_body,
        )

        attachments: List[AttachmentInfo] = []
        for att in payload.attachments:
            text = resolve_attachment_content(att)
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
            attachments.append(
                AttachmentInfo(
                    filename=att,
                    content_type="text/plain",
                    storage_ref=f"fixtures/documents/{att}",
                    sha256=sha,
                    extracted_text=text,
                )
            )

        return CanonicalEnquiry(
            idempotency_key=idempotency_key,
            source_channel=SourceChannel.WEB_FORM,
            source_message_id=payload.submission_id,
            sender=SenderInfo(name=name, email=email, phone=phone),
            subject=fields.get("subject", "Website enquiry"),
            body_text=cleaned_body,
            attachments=attachments,
            workflow_status=WorkflowStatus.RECEIVED,
        )

    @staticmethod
    def from_messaging(payload: MessagingPayload) -> CanonicalEnquiry:
        sender_data = payload.sender
        name = sender_data.get("name")
        phone = normalize_phone(sender_data.get("phone"))
        email = normalize_email(sender_data.get("email"))

        cleaned_body = normalize_whitespace(payload.text)
        idempotency_key = compute_idempotency_key(
            SourceChannel.MESSAGING.value,
            payload.message_id,
            email,
            phone,
            cleaned_body,
        )

        attachments: List[AttachmentInfo] = []
        for att in payload.attachments:
            text = resolve_attachment_content(att)
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
            attachments.append(
                AttachmentInfo(
                    filename=att,
                    content_type="text/plain",
                    storage_ref=f"fixtures/documents/{att}",
                    sha256=sha,
                    extracted_text=text,
                )
            )

        return CanonicalEnquiry(
            idempotency_key=idempotency_key,
            source_channel=SourceChannel.MESSAGING,
            source_message_id=payload.message_id,
            sender=SenderInfo(name=name, email=email, phone=phone),
            subject="Messaging enquiry",
            body_text=cleaned_body,
            attachments=attachments,
            workflow_status=WorkflowStatus.RECEIVED,
        )
