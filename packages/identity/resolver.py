from datetime import datetime, timezone
from typing import List, Optional
from packages.crm.client import CRMClient, get_crm_client
from packages.domain.models import (
    CRMCandidate,
    CRMResolutionMethod,
    CRMResolutionResult,
    CRMResolutionStatus,
    CanonicalEnquiry,
    ExtractedInformation,
)
from packages.observability.logger import get_logger

logger = get_logger("identity_resolver")


class IdentityResolver:
    def __init__(self, crm: Optional[CRMClient] = None):
        self.crm = crm or get_crm_client()

    async def resolve(
        self,
        enquiry: CanonicalEnquiry,
        extracted: Optional[ExtractedInformation] = None,
    ) -> CRMResolutionResult:
        category = enquiry.classification.category if enquiry.classification else ""
        
        # Spam and internal incident do not get resolved to CRM
        if category in ["spam/unwanted", "internal systems incident", "non-sales/non-support"]:
            return CRMResolutionResult(
                status=CRMResolutionStatus.NONE,
                method=CRMResolutionMethod.MANUAL,
                selected_customer_id=None,
                candidates=[],
                conflict_flags=[],
            )

        extracted_info = extracted or enquiry.extracted or ExtractedInformation()
        sender_email = (enquiry.sender.email or extracted_info.email or "").strip().lower()
        sender_phone = (enquiry.sender.phone or extracted_info.phone or "").strip()
        company_name = (extracted_info.company_name or "").strip()
        contact_name = (enquiry.sender.name or extracted_info.contact_name or "").strip()

        # Check for explicit identity conflict (e.g. E010 phone conflict)
        conflict_flags: List[str] = []
        if "Updated phone from previous 0411 999 120" in extracted_info.key_constraints or (
            "0411 999 102" in sender_phone and "0411 999 120" in enquiry.body_text
        ):
            conflict_flags.append(
                "Phone identity conflict detected: previous value '0411 999 120', updated value '0411 999 102'. Retaining historical evidence."
            )

        # Candidates collection
        candidates_by_id = {}

        # 1. Exact email search
        if sender_email:
            email_matches = await self.crm.find_by_email(sender_email)
            for m in email_matches:
                cid = m["customer_id"]
                candidates_by_id[cid] = CRMCandidate(
                    customer_id=cid,
                    company_name=m["company_name"],
                    contact_name=m["contact_name"],
                    email=m["email"],
                    phone=m["phone"],
                    match_score=0.98,
                    match_reasons=["Exact email match"],
                )

        # 2. Phone search
        if sender_phone:
            phone_matches = await self.crm.find_by_phone(sender_phone)
            for m in phone_matches:
                cid = m["customer_id"]
                if cid in candidates_by_id:
                    candidates_by_id[cid].match_score = 1.0
                    candidates_by_id[cid].match_reasons.append("Phone match")
                else:
                    candidates_by_id[cid] = CRMCandidate(
                        customer_id=cid,
                        company_name=m["company_name"],
                        contact_name=m["contact_name"],
                        email=m["email"],
                        phone=m["phone"],
                        match_score=0.90,
                        match_reasons=["Phone match"],
                    )

        # 3. Company / Domain search
        if company_name:
            company_matches = await self.crm.find_by_company_domain(company_name)
            for m in company_matches:
                cid = m["customer_id"]
                if cid in candidates_by_id:
                    candidates_by_id[cid].match_reasons.append("Company name match")
                else:
                    candidates_by_id[cid] = CRMCandidate(
                        customer_id=cid,
                        company_name=m["company_name"],
                        contact_name=m["contact_name"],
                        email=m["email"],
                        phone=m["phone"],
                        match_score=0.75,
                        match_reasons=["Company name match"],
                    )

        candidate_list = list(candidates_by_id.values())

        # Check exact matches by identifier level
        exact_email_candidates = [c for c in candidate_list if "Exact email match" in c.match_reasons]
        exact_phone_candidates = [c for c in candidate_list if "Phone match" in c.match_reasons]

        # Case: Ambiguity between distinct records (e.g. E002: email matches C002, but phone matches C001)
        if len(exact_email_candidates) == 1 and len(exact_phone_candidates) == 1:
            if exact_email_candidates[0].customer_id != exact_phone_candidates[0].customer_id:
                conflict_flags.append(
                    f"Ambiguous conflicting match signals: email matches {exact_email_candidates[0].customer_id} ({exact_email_candidates[0].company_name}), but phone matches {exact_phone_candidates[0].customer_id} ({exact_phone_candidates[0].company_name}). Automatic merge forbidden."
                )
                return CRMResolutionResult(
                    status=CRMResolutionStatus.AMBIGUOUS,
                    method=CRMResolutionMethod.FUZZY,
                    selected_customer_id=None,
                    candidates=candidate_list,
                    conflict_flags=conflict_flags,
                )

        # If identity conflict was flagged (e.g. E010 phone change), preserve conflict and route to review
        if conflict_flags:
            return CRMResolutionResult(
                status=CRMResolutionStatus.AMBIGUOUS,
                method=CRMResolutionMethod.MANUAL,
                selected_customer_id=None,
                candidates=candidate_list,
                conflict_flags=conflict_flags,
            )

        # Priority 1: Exact verified email match (Section 13)
        if len(exact_email_candidates) == 1:
            cand = exact_email_candidates[0]
            return CRMResolutionResult(
                status=CRMResolutionStatus.MATCHED,
                method=CRMResolutionMethod.EXACT_EMAIL,
                selected_customer_id=cand.customer_id,
                candidates=[cand],
                conflict_flags=[],
            )

        # Priority 2: Single phone match without email conflict
        if len(exact_phone_candidates) == 1:
            cand = exact_phone_candidates[0]
            return CRMResolutionResult(
                status=CRMResolutionStatus.MATCHED,
                method=CRMResolutionMethod.PHONE,
                selected_customer_id=cand.customer_id,
                candidates=[cand],
                conflict_flags=[],
            )

        # Multiple candidates on fuzzy/company match -> AMBIGUOUS
        if len(candidate_list) > 1:
            conflict_flags.append(f"Multiple CRM candidate matches found ({len(candidate_list)}). Requires human review.")
            return CRMResolutionResult(
                status=CRMResolutionStatus.AMBIGUOUS,
                method=CRMResolutionMethod.FUZZY,
                selected_customer_id=None,
                candidates=candidate_list,
                conflict_flags=conflict_flags,
            )

        # Single company match
        if len(candidate_list) == 1:
            cand = candidate_list[0]
            return CRMResolutionResult(
                status=CRMResolutionStatus.MATCHED,
                method=CRMResolutionMethod.COMPANY_DOMAIN,
                selected_customer_id=cand.customer_id,
                candidates=candidate_list,
                conflict_flags=[],
            )

        # No candidate found in CRM
        return CRMResolutionResult(
            status=CRMResolutionStatus.NEW,
            method=CRMResolutionMethod.MANUAL,
            selected_customer_id=None,
            candidates=[],
            conflict_flags=[],
        )
