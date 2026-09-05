import pytest
from packages.domain.models import (
    CanonicalEnquiry,
    ClassificationResult,
    DraftResult,
    ExtractedInformation,
    SenderInfo,
    SourceChannel,
)
from packages.policy.engine import AutonomyPolicyEngine
from packages.policy.router import StaffRouter

def test_tier5_consequential_crew_commitment_requires_approval():
    enquiry = CanonicalEnquiry(
        idempotency_key="test-1",
        source_channel=SourceChannel.EMAIL,
        classification=ClassificationResult(category="partner/operations", confidence=0.95, reason_code="CREW"),
        body_text="Please confirm four-person crew for Ballarat.",
    )
    draft = DraftResult(draft_type="response", content="Confirmed crew.", requires_approval=True)
    extracted = ExtractedInformation(commercial_commitment_requested=True)
    tier, req_approval, code = AutonomyPolicyEngine.evaluate_action_policy(enquiry, draft, extracted)
    assert tier == 5
    assert req_approval is True
    assert "APPROVAL" in code

def test_spam_automated_quarantine():
    enquiry = CanonicalEnquiry(
        idempotency_key="test-2",
        source_channel=SourceChannel.EMAIL,
        classification=ClassificationResult(category="spam/unwanted", confidence=0.99, reason_code="SPAM"),
        body_text="Buy 50,000 CEO leads now crypto.",
    )
    tier, req_approval, code = AutonomyPolicyEngine.evaluate_action_policy(enquiry, None, None)
    assert tier == 4
    assert req_approval is False
    assert "QUARANTINE" in code

def test_staff_routing():
    # Major commercial -> Matt Cooper
    e1 = CanonicalEnquiry(
        idempotency_key="e1",
        source_channel=SourceChannel.EMAIL,
        classification=ClassificationResult(category="commercial opportunity", confidence=0.95, reason_code="COMM"),
        body_text="Want commercial solar proposal for warehouses.",
    )
    r1 = StaffRouter.route_enquiry(e1)
    assert r1.assigned_owner == "Matt Cooper"

    # Internal systems -> Ali Pratama
    e2 = CanonicalEnquiry(
        idempotency_key="e2",
        source_channel=SourceChannel.MESSAGING,
        classification=ClassificationResult(category="internal systems incident", confidence=0.98, reason_code="ALERT"),
        body_text="HubSpot OAuth token expired.",
    )
    r2 = StaffRouter.route_enquiry(e2)
    assert r2.assigned_owner == "Ali Pratama"

    # Technical engineering -> OWNER_UNCONFIGURED (no invented person)
    e3 = CanonicalEnquiry(
        idempotency_key="e3",
        source_channel=SourceChannel.EMAIL,
        classification=ClassificationResult(category="technical engineering", confidence=0.95, reason_code="ENG"),
        body_text="Harmonics question on proposed battery inverter.",
    )
    r3 = StaffRouter.route_enquiry(e3)
    assert r3.assigned_owner == "OWNER_UNCONFIGURED"
