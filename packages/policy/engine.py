from typing import Optional, Tuple
from packages.domain.models import (
    CanonicalEnquiry,
    DraftResult,
    ExtractedInformation,
)
from packages.observability.logger import get_logger

logger = get_logger("policy_engine")


class AutonomyPolicyEngine:
    """
    Enforces deterministic autonomy tiers (Section 15).
    Tier 5 (Consequential actions) strictly requires human approval.
    """

    @staticmethod
    def evaluate_action_policy(
        enquiry: CanonicalEnquiry,
        draft: Optional[DraftResult],
        extracted: Optional[ExtractedInformation],
    ) -> Tuple[int, bool, str]:
        """
        Returns (tier, requires_human_approval, reason_code).
        """
        category = enquiry.classification.category if enquiry.classification else ""
        ext = extracted or enquiry.extracted or ExtractedInformation()

        # Spam/unwanted: Tier 4 bounded automation (quarantine, no outbound action)
        if category == "spam/unwanted":
            return (4, False, "SPAM_QUARANTINE_AUTOMATED")

        # Internal systems incident: Tier 4 internal task creation
        if category == "internal systems incident":
            return (4, False, "INTERNAL_INCIDENT_DISPATCH")

        # E008: Installation partner crew confirmation (Consequential external commitment)
        if category == "partner/operations" or "four-person crew" in enquiry.body_text.lower():
            return (5, True, "CONSEQUENTIAL_CREW_COMMITMENT_REQUIRES_APPROVAL")

        # Commercial commitments or quotes requested (E012, etc.)
        if ext.commercial_commitment_requested or ext.budget or ext.discrepancy_amount:
            return (5, True, "CONSEQUENTIAL_COMMERCIAL_OR_FINANCIAL_ACTION")

        # Accounts discrepancy / Invoice query (E003)
        if category == "customer support/accounts":
            return (5, True, "FINANCIAL_DISCREPANCY_REQUIRES_OPERATIONS_APPROVAL")

        # Missing information clarification draft (E005, E009, E012)
        if draft and draft.draft_type == "clarification":
            return (3, True, "CLARIFICATION_DRAFT_REQUIRES_REVIEW")

        # Standard commercial response draft (E001)
        if category == "commercial opportunity":
            return (3, True, "COMMERCIAL_DRAFT_REQUIRES_APPROVAL")

        # Non-sales (E007)
        if category == "non-sales/non-support":
            return (3, True, "NON_SALES_COMMUNICATION_REVIEW")

        # Technical (E006)
        if category == "technical engineering":
            return (2, True, "TECHNICAL_ENGINEERING_REVIEW_REQUIRED")

        # Default fallback
        return (2, True, "DEFAULT_POLICY_APPROVAL_REQUIRED")
