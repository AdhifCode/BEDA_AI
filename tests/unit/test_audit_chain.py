import pytest
from packages.audit.audit_service import AuditService

def test_calculate_event_hash_deterministic():
    h1 = AuditService.calculate_event_hash(
        previous_event_hash="GENESIS",
        enquiry_id="enq-100",
        event_type="ENQUIRY_INGESTED",
        actor_type="SYSTEM",
        actor_id="channel_adapter",
        timestamp_iso="2026-09-05T10:00:00.000000Z",
        outcome="SUCCESS",
        metadata_json={"channel": "email"},
    )
    h2 = AuditService.calculate_event_hash(
        previous_event_hash="GENESIS",
        enquiry_id="enq-100",
        event_type="ENQUIRY_INGESTED",
        actor_type="SYSTEM",
        actor_id="channel_adapter",
        timestamp_iso="2026-09-05T10:00:00.000000Z",
        outcome="SUCCESS",
        metadata_json={"channel": "email"},
    )
    assert h1 == h2
    assert len(h1) == 64

def test_tamper_detection():
    h_orig = AuditService.calculate_event_hash(
        previous_event_hash="GENESIS",
        enquiry_id="enq-100",
        event_type="ENQUIRY_INGESTED",
        actor_type="SYSTEM",
        actor_id="channel_adapter",
        timestamp_iso="2026-09-05T10:00:00.000000Z",
        outcome="SUCCESS",
        metadata_json={"amount": 100},
    )
    # Tampered metadata
    h_tampered = AuditService.calculate_event_hash(
        previous_event_hash="GENESIS",
        enquiry_id="enq-100",
        event_type="ENQUIRY_INGESTED",
        actor_type="SYSTEM",
        actor_id="channel_adapter",
        timestamp_iso="2026-09-05T10:00:00.000000Z",
        outcome="SUCCESS",
        metadata_json={"amount": 999999},
    )
    assert h_orig != h_tampered
