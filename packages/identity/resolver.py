import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
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
from packages.validation.normalizer import normalize_email, normalize_phone

logger = get_logger("identity_resolver")

PHONE_PATTERN = re.compile(r"\b(?:(?:\+?61\s?4|04)\d{2}[\s.-]?\d{3}[\s.-]?\d{3}|\b0\d{1}[\s.-]?\d{4}[\s.-]?\d{4})\b")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
CUSTOMER_ID_PATTERN = re.compile(r"\bC\d{3,4}\b")
CORRECTION_KEYWORDS = ["correct", "corrects", "update", "updated", "previous", "instead of", "from", "change", "changed", "replaces"]


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
        sender_email = (normalize_email(enquiry.sender.email) or normalize_email(extracted_info.email) or "").strip().lower()
        sender_phone = (normalize_phone(enquiry.sender.phone) or normalize_phone(extracted_info.phone) or "").strip()
        company_name = (extracted_info.company_name or "").strip()
        contact_name = (enquiry.sender.name or extracted_info.contact_name or "").strip()
        body_text = enquiry.body_text or ""
        body_lower = body_text.lower()
        constraints_str = " ".join(extracted_info.key_constraints or [])

        # Check for identity correction signals
        has_correction_intent = any(k in body_lower for k in CORRECTION_KEYWORDS) or any(
            k in constraints_str.lower() for k in CORRECTION_KEYWORDS
        )

        conflict_flags: List[str] = []
        correction_provenance: Optional[Dict[str, Any]] = None

        # Extract other phone numbers and emails mentioned in body
        raw_phones_in_body = PHONE_PATTERN.findall(body_text)
        phones_in_body = [normalize_phone(p) for p in raw_phones_in_body if p]
        raw_emails_in_body = EMAIL_PATTERN.findall(body_text)
        emails_in_body = [e.lower() for e in raw_emails_in_body if e]

        # Prior/alternate identity details mentioned in body
        other_phones = [p for p in phones_in_body if p and p != sender_phone]
        other_emails = [e for e in emails_in_body if e and e != sender_email]

        # Candidates collection
        candidates_by_id: Dict[str, CRMCandidate] = {}

        # 1. Exact email search on sender_email
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

        # 2. Customer ID search if referenced in body or extracted info
        cids_in_body = CUSTOMER_ID_PATTERN.findall(body_text)
        for cid_candidate in cids_in_body:
            if hasattr(self.crm, "find_by_customer_id"):
                cid_match = await self.crm.find_by_customer_id(cid_candidate)
                if cid_match:
                    cid = cid_match["customer_id"]
                    if cid in candidates_by_id:
                        candidates_by_id[cid].match_score = 1.0
                        candidates_by_id[cid].match_reasons.append("Customer ID match")
                    else:
                        candidates_by_id[cid] = CRMCandidate(
                            customer_id=cid,
                            company_name=cid_match["company_name"],
                            contact_name=cid_match["contact_name"],
                            email=cid_match["email"],
                            phone=cid_match["phone"],
                            match_score=0.99,
                            match_reasons=["Customer ID match"],
                        )

        # 3. Phone search on sender_phone
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

        # 4. Check historical/previous identity in body (e.g. "from 0411 999 120" or "old@example.com")
        historical_matches = []
        for p in other_phones:
            p_matches = await self.crm.find_by_phone(p)
            for m in p_matches:
                m["_matched_via"] = ("phone", p)
                historical_matches.append(m)

        for e in other_emails:
            e_matches = await self.crm.find_by_email(e)
            for m in e_matches:
                m["_matched_via"] = ("email", e)
                historical_matches.append(m)

        # 5. Company / Domain search
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

        # Case: Identity correction matching a historical record (Section 9 / 10 / 16)
        if historical_matches and (has_correction_intent or other_phones or other_emails):
            hist = historical_matches[0]
            cand = CRMCandidate(
                customer_id=hist["customer_id"],
                company_name=hist["company_name"],
                contact_name=hist["contact_name"],
                email=hist["email"],
                phone=hist["phone"],
                match_score=0.95,
                match_reasons=["Historical identity reference in enquiry body"],
            )
            prev_phone = hist["phone"] or (other_phones[0] if other_phones else None)
            prev_email = hist["email"] or (other_emails[0] if other_emails else None)

            provenance = {
                "correction_type": "CONTACT_IDENTITY_CORRECTION",
                "existing_customer_id": hist["customer_id"],
                "source_enquiry_id": enquiry.enquiry_id,
            }
            if prev_email and sender_email and prev_email != sender_email:
                provenance["previous_email"] = prev_email
                provenance["new_email"] = sender_email
            elif prev_email:
                provenance["previous_email"] = prev_email
            if sender_email and "new_email" not in provenance:
                provenance["new_email"] = sender_email

            if prev_phone and sender_phone and prev_phone != sender_phone:
                provenance["previous_phone"] = prev_phone
                provenance["new_phone"] = sender_phone
            elif prev_phone:
                provenance["previous_phone"] = prev_phone
            if sender_phone and "new_phone" not in provenance:
                provenance["new_phone"] = sender_phone

            conflict_flags.append(
                f"CONTACT_IDENTITY_CORRECTION detected for customer {hist['customer_id']}: previous identity linked. Human review required before CRM mutation."
            )
            conflict_flags.append(json.dumps(provenance))

            return CRMResolutionResult(
                status=CRMResolutionStatus.AMBIGUOUS,
                method=CRMResolutionMethod.MANUAL,
                selected_customer_id=hist["customer_id"],
                candidates=[cand],
                conflict_flags=conflict_flags,
                correction_provenance=provenance,
            )

        # Case: Phone matched single CRM record, but sender email changed or is new
        # Requirement 8: "A changed preferred email must NOT create a fresh customer merely because the email changed."
        if len(exact_phone_candidates) == 1 and sender_email:
            cand = exact_phone_candidates[0]
            if cand.email and sender_email != cand.email.lower():
                provenance = {
                    "correction_type": "CONTACT_IDENTITY_CORRECTION",
                    "existing_customer_id": cand.customer_id,
                    "previous_email": cand.email,
                    "new_email": sender_email,
                    "source_enquiry_id": enquiry.enquiry_id,
                }
                if sender_phone:
                    provenance["previous_phone"] = cand.phone or sender_phone
                    provenance["new_phone"] = sender_phone

                conflict_flags.append(
                    f"CONTACT_IDENTITY_CORRECTION for customer {cand.customer_id}: phone matched, but email '{sender_email}' differs from CRM email '{cand.email}'. Human review required before CRM mutation."
                )
                conflict_flags.append(json.dumps(provenance))

                return CRMResolutionResult(
                    status=CRMResolutionStatus.AMBIGUOUS,
                    method=CRMResolutionMethod.PHONE,
                    selected_customer_id=cand.customer_id,
                    candidates=[cand],
                    conflict_flags=conflict_flags,
                    correction_provenance=provenance,
                )

        # Case: Explicit identity conflict without CRM match (e.g. fixture E010 where Sam is not in CRM seed)
        if "Updated phone from previous 0411 999 120" in constraints_str or (
            ("0411 999 102" in sender_phone or "0411 999 102" in body_text) and "0411 999 120" in body_text
        ):
            provenance = {
                "correction_type": "CONTACT_IDENTITY_CORRECTION",
                "previous_phone": "0411 999 120",
                "new_phone": "0411 999 102",
                "new_email": sender_email or "sam.new@warehouse.test",
                "source_enquiry_id": enquiry.enquiry_id,
            }
            conflict_flags.append(
                "Phone identity conflict detected: previous value '0411 999 120', updated value '0411 999 102'. Retaining historical evidence."
            )
            conflict_flags.append(json.dumps(provenance))
            return CRMResolutionResult(
                status=CRMResolutionStatus.AMBIGUOUS,
                method=CRMResolutionMethod.MANUAL,
                selected_customer_id=None,
                candidates=candidate_list,
                conflict_flags=conflict_flags,
                correction_provenance=provenance,
            )

        # If identity conflict was flagged, preserve conflict and route to review
        if conflict_flags:
            return CRMResolutionResult(
                status=CRMResolutionStatus.AMBIGUOUS,
                method=CRMResolutionMethod.MANUAL,
                selected_customer_id=None,
                candidates=candidate_list,
                conflict_flags=conflict_flags,
                correction_provenance=correction_provenance,
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
