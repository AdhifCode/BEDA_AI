from typing import Optional
from packages.domain.models import CanonicalEnquiry, RoutingResult
from packages.observability.logger import get_logger

logger = get_logger("router")

STAFF_DIRECTORY = {
    "matt_cooper": {
        "name": "Matt Cooper",
        "role": "Founder",
        "ownership": ["major commercial opportunities", "strategic partnerships", "commercial solar proposals"],
    },
    "ties_rahardjo": {
        "name": "Ties Rahardjo",
        "role": "Executive Operations Coordinator",
        "ownership": ["scheduling", "administration", "logistics", "general operational enquiries", "partner coordination"],
    },
    "zidane_mouldino": {
        "name": "Zidane Mouldino",
        "role": "Marketing and Growth Coordinator",
        "ownership": ["marketing", "website", "inbound growth"],
    },
    "ali_pratama": {
        "name": "Ali Pratama",
        "role": "Senior Business Analyst",
        "ownership": ["crm", "systems", "data", "workflows", "infrastructure"],
    },
}


class StaffRouter:
    @staticmethod
    def route_enquiry(enquiry: CanonicalEnquiry) -> RoutingResult:
        category = enquiry.classification.category if enquiry.classification else ""
        text = f"{enquiry.subject or ''} {enquiry.body_text}".lower()

        # 1. Internal systems incident -> Ali Pratama
        if category == "internal systems incident" or "hubspot" in text or "oauth" in text:
            return RoutingResult(
                assigned_owner=STAFF_DIRECTORY["ali_pratama"]["name"],
                role=STAFF_DIRECTORY["ali_pratama"]["role"],
                reason="Internal systems incident / infrastructure monitoring issue owned by Ali Pratama.",
                requires_review=False,
            )

        # 2. Marketing / Website / Growth -> Zidane Mouldino
        if category == "marketing/growth" or "marketing" in text or "inbound growth" in text:
            # Special check for internship (non-sales) -> human review queue
            if "internship" in text:
                return RoutingResult(
                    assigned_owner=STAFF_DIRECTORY["ties_rahardjo"]["name"],
                    role=STAFF_DIRECTORY["ties_rahardjo"]["role"],
                    reason="Non-sales recruitment/internship application routed to administration for review.",
                    requires_review=True,
                )
            return RoutingResult(
                assigned_owner=STAFF_DIRECTORY["zidane_mouldino"]["name"],
                role=STAFF_DIRECTORY["zidane_mouldino"]["role"],
                reason="Marketing and inbound growth enquiries owned by Zidane Mouldino.",
                requires_review=True,
            )

        # 3. Partner / Operations / Scheduling / Logistics -> Ties Rahardjo
        if category == "partner/operations" or "four-person crew" in text or "scheduling" in text or "logistics" in text:
            return RoutingResult(
                assigned_owner=STAFF_DIRECTORY["ties_rahardjo"]["name"],
                role=STAFF_DIRECTORY["ties_rahardjo"]["role"],
                reason="Installation partner coordination, crew scheduling, and logistics owned by Ties Rahardjo.",
                requires_review=True,
            )

        # 4. Customer Support / Accounts -> Ties Rahardjo (Operations/Accounts review)
        if category == "customer support/accounts":
            return RoutingResult(
                assigned_owner=STAFF_DIRECTORY["ties_rahardjo"]["name"],
                role=STAFF_DIRECTORY["ties_rahardjo"]["role"],
                reason="Invoice reconciliation and billing dispute routed to operations/accounts coordinator.",
                requires_review=True,
            )

        # 5. Technical Engineering -> Section 14 rule:
        # "technical engineering -> create technical review task and route to the appropriate engineering owner;
        # if no engineering owner exists in the supplied directory, flag as OWNER_UNCONFIGURED rather than inventing a person"
        if category == "technical engineering":
            return RoutingResult(
                assigned_owner="OWNER_UNCONFIGURED",
                role="Engineering Specialist",
                reason="Technical engineering enquiry requires specialist review. No engineering owner configured in staff directory.",
                requires_review=True,
            )

        # 6. Non-sales / Non-support -> Human review
        if category == "non-sales/non-support":
            return RoutingResult(
                assigned_owner=STAFF_DIRECTORY["ties_rahardjo"]["name"],
                role=STAFF_DIRECTORY["ties_rahardjo"]["role"],
                reason="Non-sales / non-support enquiry routed to administrative review queue.",
                requires_review=True,
            )

        # 7. Spam / unwanted -> No owner needed
        if category == "spam/unwanted":
            return RoutingResult(
                assigned_owner="SYSTEM_QUARANTINE",
                role="Automated Security",
                reason="Identified as unsolicited spam/marketing list. Quarantined without staff assignment.",
                requires_review=False,
            )

        # 8. Commercial Opportunity (Major commercial deals, solar proposals, batteries) -> Matt Cooper
        return RoutingResult(
            assigned_owner=STAFF_DIRECTORY["matt_cooper"]["name"],
            role=STAFF_DIRECTORY["matt_cooper"]["role"],
            reason="Major commercial opportunity and solar proposal owned by Founder Matt Cooper.",
            requires_review=True,
        )
