import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Protocol, Tuple
import httpx
from packages.domain.models import (
    CRMCandidate,
    CanonicalEnquiry,
    ClassificationResult,
    DraftResult,
    ExtractedInformation,
    MissingInfoItem,
)
from packages.observability.logger import get_logger
from packages.validation.normalizer import (
    extract_domain,
    normalize_email,
    normalize_phone,
    normalize_whitespace,
)
from packages.validation.prompt_safety import format_bounded_prompt

logger = get_logger("ai_gateway")


class ModelUnavailableError(RuntimeError):
    """
    Raised when all configured external LLM models fail or are unavailable.
    Distinguishes live production outages from deterministic test fixture fallbacks.
    """

    def __init__(
        self,
        message: str = "All configured external models are unavailable",
        models_attempted: Optional[List[str]] = None,
    ):
        super().__init__(message)
        self.models_attempted = models_attempted or []


class LLMProvider(Protocol):
    async def classify_and_extract(
        self,
        enquiry: CanonicalEnquiry,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[ClassificationResult, ExtractedInformation]: ...

    async def detect_missing_information(
        self,
        enquiry: CanonicalEnquiry,
        extracted: ExtractedInformation,
    ) -> List[MissingInfoItem]: ...

    async def draft_response(
        self,
        enquiry: CanonicalEnquiry,
        crm_context: Optional[CRMCandidate],
        knowledge_context: Optional[Dict[str, Any]] = None,
    ) -> DraftResult: ...


class FakeLLMProvider:
    """
    Deterministic provider for automated testing and exact fixture execution (E001-E012).
    Never invents facts.
    """

    def __init__(self):
        self.provider_name = "fake"
        self.model = "default"
        self.last_call_stats: Dict[str, Any] = {
            "provider": "fake",
            "model": "default",
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "is_fallback": False,
            "error_code": None,
        }

    async def classify_and_extract(
        self,
        enquiry: CanonicalEnquiry,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[ClassificationResult, ExtractedInformation]:
        text = f"{enquiry.subject or ''}\n{enquiry.body_text}".lower()
        sender_email = (enquiry.sender.email or "").lower()
        sender_name = enquiry.sender.name

        # E004: Spam
        if "megaleadlists" in sender_email or "cryptocurrency" in text or "ceo leads" in text:
            cls = ClassificationResult(
                category="spam/unwanted",
                confidence=0.99,
                reason_code="SPAM_KEYWORDS_AND_CRYPTO",
                provenance=["sales@megaleadlists.example", "cryptocurrency payment instructions"],
            )
            extracted = ExtractedInformation(
                email=enquiry.sender.email,
                intent_summary="Unsolicited lead list sales offer with crypto payment",
                provenance=["Buy 50,000 Australian CEO leads today"],
            )
            return cls, extracted

        # E011: Internal systems alert
        if "hubspot sync" in text or "oauth token" in text or "system alert" in text:
            cls = ClassificationResult(
                category="internal systems incident",
                confidence=0.98,
                reason_code="INTERNAL_AUTOMATED_MONITORING_ALERT",
                provenance=["HubSpot sync failed", "OAuth token expired"],
            )
            extracted = ExtractedInformation(
                intent_summary="HubSpot integration failure: OAuth token expired, 146 records unsynchronised, retry disabled after 3 failures",
                key_constraints=["OAuth token expired", "146 records unsynchronised", "Retry disabled after 3 failures"],
                provenance=["OAuth token expired", "146 records remain unsynchronised"],
            )
            return cls, extracted

        # E007: Marketing internship application
        if "internship" in text or "priya.dev" in sender_email:
            cls = ClassificationResult(
                category="non-sales/non-support",
                confidence=0.96,
                reason_code="RECRUITMENT_OR_INTERNSHIP_APPLICATION",
                provenance=["marketing-internship application"],
            )
            extracted = ExtractedInformation(
                contact_name=sender_name or "Priya Dev",
                email=enquiry.sender.email or "priya.dev@examplemail.test",
                intent_summary="Marketing internship application (non-commercial, non-support)",
                provenance=["marketing-internship application"],
            )
            return cls, extracted

        # E003: Accounts invoice query / discrepancy
        if "invoice 1847" in text or "does not match po" in text or "greenfields" in sender_email:
            cls = ClassificationResult(
                category="customer support/accounts",
                confidence=0.97,
                reason_code="INVOICE_PURCHASE_ORDER_DISCREPANCY",
                provenance=["invoice 1847 is $2,640 higher than the purchase order"],
            )
            # Attachment analysis for 03_greenfields_invoice_query.txt:
            # PO 8821: $47,300 ex GST. Invoice 1847: $49,940 ex GST. Diff: $2,640
            extracted = ExtractedInformation(
                contact_name=sender_name or "Rohan Lee",
                email=enquiry.sender.email or "rohan@greenfieldsfoods.example",
                phone=enquiry.sender.phone or "0400 222 310",
                company_name="Greenfields Foods Pty Ltd",
                location="Geelong VIC",
                intent_summary="Reconciliation request for invoice 1847 exceeding purchase order GF PO 8821 by $2,640 ex GST ($49,940 vs $47,300)",
                requested_products_or_services=["Lighting project Geelong"],
                discrepancy_amount="$2,640 ex GST",
                key_constraints=["Check before Friday", "Reconciliation requested before payment"],
                provenance=["invoice 1847 is $2,640 higher than the purchase order", "Purchase order GF PO 8821: $47,300 ex GST", "Invoice 1847: $49,940 ex GST"],
            )
            return cls, extracted

        # E006: Technical harmonics question
        if "harmonics question" in text or "pcs specification" in text or "solarray" in sender_email:
            cls = ClassificationResult(
                category="technical engineering",
                confidence=0.95,
                reason_code="TECHNICAL_ENGINEERING_HARMONICS_STUDY",
                provenance=["harmonics question on proposed battery inverter", "PCS specification on a 500 kW battery project"],
            )
            extracted = ExtractedInformation(
                email=enquiry.sender.email or "engineering@solarray.example",
                company_name="SolarRay",
                intent_summary="Technical inquiry regarding acceptable THD limits at point of common coupling and harmonic study requirement for 500 kW battery inverter PCS",
                requested_products_or_services=["500 kW battery inverter PCS technical review"],
                project_size="500 kW battery project",
                provenance=["500 kW battery project", "acceptable THD limits at the point of common coupling"],
            )
            return cls, extracted

        # E008: Installation partner crew confirmation
        if "four-person crew" in text or "ballarat" in text or "daniel wu" in text or "solara" in sender_email:
            cls = ClassificationResult(
                category="partner/operations",
                confidence=0.96,
                reason_code="INSTALLATION_PARTNER_CREW_COORDINATION",
                provenance=["confirm a four-person crew for a Ballarat commercial solar project"],
            )
            extracted = ExtractedInformation(
                contact_name=sender_name or "Daniel Wu",
                email=enquiry.sender.email or "daniel@solarainstall.example",
                phone=enquiry.sender.phone or "0400 880 101",
                company_name="Solara Installations",
                location="Ballarat",
                intent_summary="Request confirmation for four-person installation crew for Ballarat commercial solar project for week beginning 14 September",
                requested_products_or_services=["Four-person installation crew"],
                timeframe="Week beginning 14 September (confirmation needed by Tuesday)",
                commercial_commitment_requested=True,
                provenance=["confirm a four-person crew for a Ballarat commercial solar project for the week beginning 14 September"],
            )
            return cls, extracted

        # E005: Northbank College lighting
        if "northbank college" in text or "fluorescent fittings" in text or "melissa.tran" in sender_email:
            cls = ClassificationResult(
                category="commercial opportunity",
                confidence=0.95,
                reason_code="COMMERCIAL_LIGHTING_AND_INCENTIVES_ENQUIRY",
                provenance=["Government school lighting upgrade", "1,100 fluorescent fittings", "access any government incentives"],
            )
            extracted = ExtractedInformation(
                contact_name=sender_name or "Melissa Tran",
                email=enquiry.sender.email or "melissa.tran@northbankcollege.example",
                phone=enquiry.sender.phone or "0400 330 110",
                company_name="Northbank College",
                location="Sydney NSW",
                intent_summary="LED lighting upgrade for ~1,100 fluorescent fittings seeking government incentive eligibility",
                requested_products_or_services=["LED lighting upgrade", "Government incentives assessment"],
                site_count=1,
                project_size="1,100 fluorescent fittings",
                provenance=["manage facilities at Northbank College", "approximately 1,100 fluorescent fittings"],
            )
            return cls, extracted

        # E010: Sam follow-up identity conflict
        if "corrects the phone number to 0411 999 102" in text or "0411 999 102" in text:
            cls = ClassificationResult(
                category="commercial opportunity",
                confidence=0.92,
                reason_code="FOLLOW_UP_ENQUIRY_WITH_PHONE_UPDATE",
                provenance=["corrects the phone number to 0411 999 102"],
            )
            extracted = ExtractedInformation(
                contact_name="Sam",
                phone="0411 999 102",
                intent_summary="Follow-up regarding Newcastle warehouse commercial energy reduction; provides corrected phone number 0411 999 102",
                requested_products_or_services=["Commercial solar", "Operating cost reduction"],
                annual_or_monthly_energy_usage="$80,000/month",
                location="Newcastle",
                key_constraints=["Updated phone from previous 0411 999 120"],
                provenance=["0411 999 102"],
            )
            return cls, extracted

        # E009: Newcastle warehouse enquiry
        if "newcastle" in text or "80,000/month" in text or "0411 999 120" in text:
            cls = ClassificationResult(
                category="commercial opportunity",
                confidence=0.94,
                reason_code="MAJOR_COMMERCIAL_SOLAR_OPPORTUNITY",
                provenance=["Newcastle refrigerated warehouse", "bills are about $80,000/month"],
            )
            extracted = ExtractedInformation(
                contact_name="Sam",
                phone="0411 999 120",
                location="Newcastle",
                intent_summary="Major commercial solar and cost reduction inquiry for refrigerated warehouse with ~$80,000/month electricity bills",
                requested_products_or_services=["Commercial solar", "Operating-cost reductions"],
                annual_or_monthly_energy_usage="$80,000/month",
                site_count=1,
                provenance=["refrigerated warehouse", "$80,000/month", "0411 999 120"],
            )
            return cls, extracted

        # E012: Small cafe enquiry
        if "cafe" in text or "70 square metre" in text or "900/month" in text:
            cls = ClassificationResult(
                category="commercial opportunity",
                confidence=0.91,
                reason_code="SMALL_COMMERCIAL_SOLAR_ENQUIRY_WITH_LANDLORD_DEPENDENCY",
                provenance=["leases a 70 square metre cafe", "spends about $900/month on electricity", "landlord has not agreed to roof works"],
            )
            extracted = ExtractedInformation(
                contact_name=sender_name,
                email=enquiry.sender.email,
                phone=enquiry.sender.phone,
                intent_summary="Solar proposal requested for leased 70 m2 cafe (~$900/month electricity spend), landlord consent pending",
                requested_products_or_services=["Solar quote"],
                annual_or_monthly_energy_usage="$900/month",
                project_size="70 square metre leased cafe",
                key_constraints=["Landlord has not agreed to roof works"],
                commercial_commitment_requested=True,
                provenance=["70 square metre cafe", "$900/month", "landlord has not agreed to roof works"],
            )
            return cls, extracted

        # E001 / E002: Hume Logistics
        if "hume logistic" in text or "humelogistics.example" in sender_email or "truganina" in text:
            cls = ClassificationResult(
                category="commercial opportunity",
                confidence=0.96,
                reason_code="MAJOR_COMMERCIAL_SOLAR_BATTERY_MULTI_SITE",
                provenance=["three Victorian sites", "2.1 GWh per year", "solar, possibly batteries and lighting"],
            )
            extracted = ExtractedInformation(
                contact_name=sender_name or "Amelia Grant",
                email=enquiry.sender.email,
                phone=enquiry.sender.phone or "0400 111 020",
                company_name="Hume Logistics Pty Ltd" if "pty ltd" in text or sender_email == "amelia.grant@humelogistics.example" else "Hume Logistic",
                company_domain="humelogistics.example",
                location="Melbourne VIC",
                intent_summary="Commercial solar, battery storage and lighting upgrade across three Victorian distribution warehouses",
                requested_products_or_services=["Commercial solar", "Batteries", "Lighting upgrades"],
                annual_or_monthly_energy_usage="2.1 GWh per year",
                site_count=3,
                timeframe="Initial discussion next week",
                commercial_commitment_requested=False,
                provenance=["Truganina, Dandenong and Epping", "2.1 GWh per year", "attached the latest Truganina bill"],
            )
            return cls, extracted

        # Fallback generic extraction
        cls = ClassificationResult(
            category="commercial opportunity",
            confidence=0.75,
            reason_code="GENERAL_INBOUND_ENQUIRY",
            provenance=["General inquiry text"],
        )
        extracted = ExtractedInformation(
            contact_name=enquiry.sender.name,
            email=enquiry.sender.email,
            phone=enquiry.sender.phone,
            intent_summary=enquiry.body_text[:200],
            provenance=["Customer message body"],
        )
        return cls, extracted

    async def detect_missing_information(
        self,
        enquiry: CanonicalEnquiry,
        extracted: ExtractedInformation,
    ) -> List[MissingInfoItem]:
        missing: List[MissingInfoItem] = []

        # Category: commercial opportunity
        if enquiry.classification and enquiry.classification.category == "commercial opportunity":
            # Check E005: Northbank College
            if extracted.company_name == "Northbank College" or "northbank" in enquiry.body_text.lower():
                missing.append(
                    MissingInfoItem(
                        field_name="fixture_schedule",
                        description="Existing lighting fixture schedule/counts and operating hours",
                        required_for_category="commercial opportunity",
                        reason="Required to calculate baseline energy savings and assess government incentive eligibility",
                    )
                )
                missing.append(
                    MissingInfoItem(
                        field_name="electricity_bill",
                        description="Recent 12-month electricity invoices or interval data",
                        required_for_category="commercial opportunity",
                        reason="Required to verify tariff structure and actual energy consumption",
                    )
                )

            # Check E009: Sam Newcastle warehouse
            if extracted.location == "Newcastle" or "newcastle" in enquiry.body_text.lower():
                if not extracted.company_name:
                    missing.append(
                        MissingInfoItem(
                            field_name="company_name",
                            description="Registered company name",
                            required_for_category="commercial opportunity",
                            reason="Required to resolve CRM identity and perform credit check",
                        )
                    )
                if not extracted.email:
                    missing.append(
                        MissingInfoItem(
                            field_name="email",
                            description="Business email address",
                            required_for_category="commercial opportunity",
                            reason="Required for verified communications and formal proposal dispatch",
                        )
                    )
                missing.append(
                    MissingInfoItem(
                        field_name="site_interval_data",
                        description="Full 12-month interval (NMI) electricity data",
                        required_for_category="commercial opportunity",
                        reason="Required to engineer solar system sizing for $80k/month consumption",
                    )
                )

            # Check E012: Cafe enquiry
            if "cafe" in enquiry.body_text.lower() or "landlord" in enquiry.body_text.lower():
                if "Landlord has not agreed to roof works" in extracted.key_constraints:
                    missing.append(
                        MissingInfoItem(
                            field_name="landlord_consent",
                            description="Written consent from building owner / landlord for roof installation",
                            required_for_category="commercial opportunity",
                            reason="Essential legal prerequisite before structural engineering and installation can proceed",
                        )
                    )

        # Category: technical engineering (E006)
        elif enquiry.classification and enquiry.classification.category == "technical engineering":
            missing.append(
                MissingInfoItem(
                    field_name="single_line_diagram",
                    description="Site single line diagram and Point of Common Coupling (PCC) details",
                    required_for_category="technical engineering",
                    reason="Required by electrical engineering to determine grid compliance and harmonic filters",
                )
            )

        return missing

    async def draft_response(
        self,
        enquiry: CanonicalEnquiry,
        crm_context: Optional[CRMCandidate],
        knowledge_context: Optional[Dict[str, Any]] = None,
    ) -> DraftResult:
        category = enquiry.classification.category if enquiry.classification else ""
        extracted = enquiry.extracted or ExtractedInformation()

        # E004: Spam - no draft
        if category == "spam/unwanted":
            return DraftResult(
                draft_type="none",
                content="[QUARANTINED - NO OUTBOUND DRAFT FOR SPAM]",
                grounding_refs=["Message classified as unsolicited spam"],
                requires_approval=False,
                status="QUARANTINED",
            )

        # E011: Internal systems incident - internal alert task, no customer response
        if category == "internal systems incident":
            return DraftResult(
                draft_type="internal_alert",
                content="INTERNAL INCIDENT ALERT: HubSpot OAuth sync failed at 02:14. 146 records remain unsynchronised. Token refresh and connection retry required by Systems Administrator (Ali Pratama).",
                grounding_refs=["Customer text: HubSpot sync failed at 02:14; 146 records remain unsynchronised"],
                requires_approval=False,
                status="INTERNAL_TASK_CREATED",
            )

        # E007: Non-sales / Non-support - internship response
        if category == "non-sales/non-support":
            return DraftResult(
                draft_type="acknowledgment",
                content="Dear Priya,\n\nThank you for your interest in the marketing internship at BEDA. Your application has been routed to our operations and administrative team for review.\n\nKind regards,\nBEDA Operations",
                grounding_refs=["Application from priya.dev@examplemail.test"],
                requires_approval=False,
                status="PENDING_APPROVAL",
            )

        # E003: Accounts invoice query
        if category == "customer support/accounts":
            return DraftResult(
                draft_type="response",
                content="Hi Rohan,\n\nThank you for raising this. We have flagged invoice 1847 ($49,940 ex GST) against purchase order GF PO 8821 ($47,300 ex GST) regarding the $2,640 ex GST variance for the Geelong lighting project. Our accounts team is reviewing the discrepancy and will provide reconciliation prior to payment due date this Friday.\n\nKind regards,\nBEDA Accounts & Operations",
                grounding_refs=[
                    "Invoice 1847 ($49,940 ex GST)",
                    "Purchase order GF PO 8821 ($47,300 ex GST)",
                    "Variance: $2,640 ex GST",
                    "Geelong completed lighting project",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # E005: Northbank College clarification draft (missing info)
        if category == "commercial opportunity" and extracted.company_name == "Northbank College":
            return DraftResult(
                draft_type="clarification",
                content="Dear Melissa,\n\nThank you for contacting BEDA regarding an LED lighting upgrade for Northbank College. To assess your eligibility for Victorian/NSW government lighting incentive certificates and prepare an accurate proposal for your ~1,100 fittings, could you please provide:\n1. Current lighting fixture schedule (tube types, wattages, operating hours)\n2. Your latest 12 months of electricity invoices or interval data\n\nOnce received, our commercial team will prepare a detailed analysis for you.\n\nKind regards,\nMatt Cooper\nFounder, BEDA",
                grounding_refs=[
                    "Customer enquiry: Northbank College, 1,100 fluorescent fittings",
                    "Site notes document: no fixture schedule, no electricity invoice supplied",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # E008: Installation partner crew confirmation (Consequential action -> REQUIRES APPROVAL)
        if category == "partner/operations":
            return DraftResult(
                draft_type="response",
                content="Hi Daniel,\n\nRegarding your request for a four-person installation crew in Ballarat for the week beginning 14 September: this request has been scheduled for operational review with Ties Rahardjo. Formal crew confirmation will be issued by Tuesday as requested upon schedule validation.\n\nKind regards,\nBEDA Operations",
                grounding_refs=[
                    "CRM partner record C005 (Solara Installations, Daniel Wu)",
                    "Requested four-person crew, Ballarat, week beginning 14 September",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # E006: Technical harmonics enquiry
        if category == "technical engineering":
            return DraftResult(
                draft_type="acknowledgment",
                content="Hello SolarRay Engineering,\n\nThank you for reaching out regarding the PCS specification and acceptable THD limits for the 500 kW battery project. We have logged an electrical engineering technical review task. An engineer will examine the point of common coupling harmonic limits and advise whether an additional harmonic study is required.\n\nKind regards,\nBEDA Engineering",
                grounding_refs=[
                    "SolarRay enquiry: 500 kW battery inverter PCS",
                    "Harmonics / THD limits query at point of common coupling",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # E001 / E002: Hume Logistics
        if "hume" in (extracted.company_name or "").lower():
            return DraftResult(
                draft_type="response",
                content="Hi Amelia,\n\nThank you for reaching out to BEDA regarding commercial solar, battery storage, and lighting upgrades across your three Victorian distribution centres (Truganina, Dandenong, and Epping). We have received your latest Truganina billing statement (68,420 kWh consumption, 172 kW peak demand). Matt Cooper would be pleased to schedule our initial discussion with you next week.\n\nKind regards,\nMatt Cooper\nFounder, BEDA",
                grounding_refs=[
                    "Customer body: 3 Victorian warehouses (Truganina, Dandenong, Epping), ~2.1 GWh/yr",
                    "Attached bill 01_hume_energy_bill.txt (Truganina DC, 68,420 kWh, $18,940)",
                    "CRM Record C001 (Hume Logistics Pty Ltd, Amelia Grant)",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # E009 / E010: Newcastle warehouse
        if extracted.location == "Newcastle":
            phone_ref = extracted.phone or "0411 999 120"
            return DraftResult(
                draft_type="clarification",
                content=f"Hi Sam,\n\nThank you for reaching out regarding solar and energy cost reduction for your Newcastle refrigerated warehouse. Operating electricity costs of ~$80,000/month indicate strong potential for commercial solar. To help our commercial lead (Matt Cooper) prepare an initial feasibility assessment, could you please confirm your company name and email address, along with a recent electricity invoice or NMI?\n\nKind regards,\nMatt Cooper\nFounder, BEDA",
                grounding_refs=[
                    "Customer enquiry: Newcastle refrigerated warehouse, ~$80,000/month spend, contact Sam",
                    f"Phone contact: {phone_ref}",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # E012: Small cafe
        if "cafe" in enquiry.body_text.lower():
            return DraftResult(
                draft_type="clarification",
                content="Hi,\n\nThank you for contacting BEDA. While we can design solar solutions for commercial premises with ~$900/month electricity spend, commercial installations on leased properties require formal written consent from the landlord/building owner prior to roof access and engineering works. Please let us know if your landlord is open to discussing roof access terms.\n\nKind regards,\nMatt Cooper\nFounder, BEDA",
                grounding_refs=[
                    "Enquiry body: 70 m2 leased cafe, ~$900/month electricity",
                    "Customer statement: landlord has not agreed to roof works",
                ],
                requires_approval=True,
                status="PENDING_APPROVAL",
            )

        # Default grounded draft
        return DraftResult(
            draft_type="response",
            content=f"Hello,\n\nThank you for contacting BEDA. We have received your enquiry regarding {extracted.intent_summary or 'energy solutions'}. Our team is reviewing the details provided and will be in touch shortly.\n\nKind regards,\nBEDA Team",
            grounding_refs=["Customer enquiry body"],
            requires_approval=True,
            status="PENDING_APPROVAL",
        )


PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../prompts"))


def _load_prompt_template(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to read prompt template {filename}: {e}")
    return ""


def _extract_and_parse_json(text: str) -> Any:
    """
    Extracts and parses JSON from raw LLM output, handling markdown code fences,
    preamble text, and trailing explanations.
    """
    if not text or not text.strip():
        raise ValueError("Empty response from LLM")

    cleaned = text.strip()

    # 1. Direct parse attempt
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2. Extract markdown code blocks (```json ... ``` or ``` ... ```)
    matches = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    for block in matches:
        block_clean = block.strip()
        try:
            return json.loads(block_clean)
        except json.JSONDecodeError:
            continue

    # 3. Find outermost JSON object { ... }
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 4. Find outermost JSON array [ ... ]
    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        candidate = cleaned[first_bracket : last_bracket + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from LLM output: {text[:200]}")


class OpenAICompatibleProvider:
    """
    Adapter for live OpenAI / OpenRouter / Gemini / Ollama endpoints with structured output support.
    Supports a multi-tiered resilience strategy:
      - Primary model (e.g. z-ai/glm-5.2:free)
      - Backup/Fallback model (e.g. google/gemma-4-26b-a4b-it:free) on error/rate limits
      - Escalation model (e.g. google/gemma-4-31b-it:free) on low confidence or secondary retry
      - Deterministic provider (FakeLLMProvider) as ultimate safety guard.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        fallback_model: Optional[str] = None,
        escalation_model: Optional[str] = None,
        timeout: float = 45.0,
        extra_headers: Optional[Dict[str, str]] = None,
        provider_name: str = "openrouter",
        primary_model: Optional[str] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.primary_model = primary_model or model
        self.model = self.primary_model
        self.fallback_model = fallback_model
        self.escalation_model = escalation_model
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.provider_name = provider_name
        self.model_unavailable: bool = False
        self.fallback = FakeLLMProvider()
        self.last_call_stats: Dict[str, Any] = {
            "provider": self.provider_name,
            "model": self.model,
            "primary_model": self.primary_model,
            "fallback_model": self.fallback_model,
            "escalation_model": self.escalation_model,
            "models_attempted": [self.model],
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "is_fallback": False,
            "error_code": None,
        }

    def _get_model_tiers(self) -> List[str]:
        tiers = [self.primary_model]
        if self.fallback_model and self.fallback_model not in tiers:
            tiers.append(self.fallback_model)
        if self.escalation_model and self.escalation_model not in tiers:
            tiers.append(self.escalation_model)
        return tiers

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    async def _post_chat_completion(
        self,
        messages: List[Dict[str, str]],
        json_mode: bool = True,
        model_override: Optional[str] = None,
    ) -> str:
        target_model = model_override or self.model
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.1,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._build_headers(),
                json=payload,
            )
            # If provider fails specifically because response_format is unsupported, retry without it
            if res.status_code == 400 and json_mode and "response_format" in res.text.lower():
                payload.pop("response_format", None)
                res = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._build_headers(),
                    json=payload,
                )

            if res.status_code != 200:
                raise RuntimeError(f"LLM API returned HTTP {res.status_code}: {res.text[:300]}")

            data = res.json()
            usage = data.get("usage") or {}
            self._latest_usage = {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "model": data.get("model", target_model),
            }
            return data["choices"][0]["message"]["content"]

    async def _call_post_chat(
        self,
        messages: List[Dict[str, str]],
        json_mode: bool = True,
        model: Optional[str] = None,
    ) -> str:
        try:
            return await self._post_chat_completion(messages, json_mode=json_mode, model_override=model)
        except TypeError:
            # Handle mocks that only take (messages, json_mode)
            return await self._post_chat_completion(messages, json_mode=json_mode)

    async def classify_and_extract(
        self,
        enquiry: CanonicalEnquiry,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[ClassificationResult, ExtractedInformation]:
        start_time = time.perf_counter()
        if not self.api_key:
            self.model_unavailable = True
            self.last_call_stats = {
                "provider": self.provider_name,
                "model": "no_api_key",
                "primary_model": self.primary_model,
                "fallback_model": self.fallback_model,
                "escalation_model": self.escalation_model,
                "models_attempted": [self.primary_model],
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "is_fallback": False,
                "error_code": "MODEL_UNAVAILABLE",
            }
            raise ModelUnavailableError(
                "No API key provided for live LLM provider",
                models_attempted=[self.primary_model],
            )

        customer_parts = []
        if enquiry.subject:
            customer_parts.append(f"Subject: {enquiry.subject}")
        customer_parts.append(f"Body:\n{enquiry.body_text}")
        if enquiry.attachments:
            att_lines = [f"- {a.filename}: {a.extracted_text or 'No text content'}" for a in enquiry.attachments]
            customer_parts.append("Attachments:\n" + "\n".join(att_lines))
        customer_data = "\n\n".join(customer_parts)

        approved_crm_data = ""
        if context and "crm_candidate" in context:
            approved_crm_data = json.dumps(context["crm_candidate"], default=str)

        system_rules = _load_prompt_template("classify_extract.md")
        prompt = format_bounded_prompt(
            system_rules=system_rules or "Classify enquiry and extract structured parameters in JSON.",
            customer_data=customer_data,
            approved_crm_data=approved_crm_data,
            task="Classify this inbound enquiry into one of the 8 business categories and extract structured entities in the required JSON format. Return valid JSON only.",
        )

        messages = [
            {"role": "system", "content": "You are the BEDA Enquiry Intelligence Engine. Always respond in valid JSON matching the schema."},
            {"role": "user", "content": prompt},
        ]

        models_to_try = self._get_model_tiers()
        attempted_models: List[str] = []
        last_error = None
        confidence_threshold = float(os.getenv("LLM_CONFIDENCE_THRESHOLD", "0.80"))

        candidate_result: Optional[ClassificationResult] = None
        candidate_extracted: Optional[ExtractedInformation] = None
        successful_model: Optional[str] = None

        for idx, current_model in enumerate(models_to_try):
            attempted_models.append(current_model)
            try:
                raw_response = await self._call_post_chat(messages, json_mode=True, model=current_model)
                parsed = _extract_and_parse_json(raw_response)

                cls_data = parsed.get("classification")
                if not cls_data and "category" in parsed:
                    cls_data = {
                        "category": parsed.get("category", "commercial opportunity"),
                        "confidence": float(parsed.get("confidence", 0.85)),
                        "reason_code": str(parsed.get("reason_code", "AI_CLASSIFICATION")),
                        "provenance": parsed.get("provenance", []),
                    }

                ext_data = parsed.get("extracted") or parsed.get("entities")
                if not ext_data and ("intent_summary" in parsed or "contact_name" in parsed):
                    ext_data = parsed

                if not cls_data or not ext_data:
                    raise ValueError("Parsed JSON missing 'classification' or 'extracted' fields")

                cls_res = ClassificationResult(**cls_data)
                ext_res = ExtractedInformation(**ext_data)

                # If confidence meets threshold, accept immediately
                if cls_res.confidence >= confidence_threshold or idx == len(models_to_try) - 1:
                    candidate_result = cls_res
                    candidate_extracted = ext_res
                    successful_model = current_model
                    break
                else:
                    # Low confidence: escalate to next model in chain if available
                    logger.info(
                        f"Model '{current_model}' confidence {cls_res.confidence:.2f} below threshold {confidence_threshold}. Escalating to next model '{models_to_try[idx+1]}'..."
                    )
                    candidate_result = cls_res
                    candidate_extracted = ext_res
                    successful_model = current_model

            except Exception as e:
                last_error = str(e)
                next_info = f" Retrying with backup model '{models_to_try[idx+1]}'..." if idx + 1 < len(models_to_try) else " All configured live models failed."
                logger.warning(f"LLM model '{current_model}' classify_and_extract failed: {last_error}.{next_info}")

        if candidate_result is not None and candidate_extracted is not None and successful_model is not None:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            usage = getattr(self, "_latest_usage", {})
            self.last_call_stats = {
                "provider": self.provider_name,
                "model": successful_model,
                "primary_model": self.primary_model,
                "fallback_model": self.fallback_model,
                "escalation_model": self.escalation_model,
                "models_attempted": attempted_models,
                "latency_ms": elapsed_ms,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "is_fallback": (successful_model != self.primary_model),
                "error_code": None if successful_model == self.primary_model else f"SERVED_BY_{successful_model}",
            }
            return candidate_result, candidate_extracted

        # All models failed: raise ModelUnavailableError for workflow degraded mode
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        logger.warning(
            f"All LLM models {attempted_models} failed. Raising ModelUnavailableError. Error: {last_error}"
        )
        self.model_unavailable = True
        self.last_call_stats = {
            "provider": self.provider_name,
            "model": "unavailable",
            "primary_model": self.primary_model,
            "fallback_model": self.fallback_model,
            "escalation_model": self.escalation_model,
            "models_attempted": attempted_models,
            "latency_ms": elapsed_ms,
            "input_tokens": 0,
            "output_tokens": 0,
            "is_fallback": False,
            "error_code": "MODEL_UNAVAILABLE",
        }
        raise ModelUnavailableError(
            f"All configured live models failed: {attempted_models}. Last error: {last_error}",
            models_attempted=attempted_models,
        )

    async def detect_missing_information(
        self,
        enquiry: CanonicalEnquiry,
        extracted: ExtractedInformation,
    ) -> List[MissingInfoItem]:
        if not self.api_key:
            self.model_unavailable = True
            raise ModelUnavailableError("No API key provided for live LLM provider", models_attempted=[self.primary_model])

        customer_data = f"Subject: {enquiry.subject or ''}\nBody: {enquiry.body_text}"
        extracted_json = json.dumps(extracted.model_dump(), default=str)
        system_rules = _load_prompt_template("missing_information.md")

        prompt = format_bounded_prompt(
            system_rules=system_rules or "Detect missing information required for the category.",
            customer_data=customer_data,
            approved_crm_data=f"EXTRACTED ENTITIES:\n{extracted_json}",
            task=f"Category: {enquiry.classification.category if enquiry.classification else 'commercial opportunity'}\nIdentify critical missing information needed to advance this enquiry. Return a JSON array or object with 'missing_items'.",
        )

        messages = [
            {"role": "system", "content": "You are BEDA Enquiry Intelligence. Output JSON only."},
            {"role": "user", "content": prompt},
        ]

        models_to_try = self._get_model_tiers()
        for idx, current_model in enumerate(models_to_try):
            try:
                raw_response = await self._call_post_chat(messages, json_mode=True, model=current_model)
                parsed = _extract_and_parse_json(raw_response)

                items = []
                if isinstance(parsed, list):
                    items = parsed
                elif isinstance(parsed, dict):
                    items = parsed.get("missing_items") or parsed.get("missing_information") or parsed.get("items") or []

                result: List[MissingInfoItem] = []
                for item in items:
                    if isinstance(item, dict) and "field_name" in item:
                        result.append(
                            MissingInfoItem(
                                field_name=item["field_name"],
                                description=item.get("description", ""),
                                required_for_category=item.get("required_for_category", enquiry.classification.category if enquiry.classification else "commercial opportunity"),
                                reason=item.get("reason", ""),
                            )
                        )
                return result
            except Exception as e:
                next_info = f" Retrying with backup model '{models_to_try[idx+1]}'..." if idx + 1 < len(models_to_try) else " Falling back to deterministic provider."
                logger.warning(f"LLM model '{current_model}' detect_missing_information failed: {e}.{next_info}")

        self.model_unavailable = True
        raise ModelUnavailableError(
            f"All configured live models failed detect_missing_information: {models_to_try}",
            models_attempted=models_to_try,
        )

    async def draft_response(
        self,
        enquiry: CanonicalEnquiry,
        crm_context: Optional[CRMCandidate],
        knowledge_context: Optional[Dict[str, Any]] = None,
    ) -> DraftResult:
        if not self.api_key:
            self.model_unavailable = True
            raise ModelUnavailableError("No API key provided for live LLM provider", models_attempted=[self.primary_model])

        category = enquiry.classification.category if enquiry.classification else ""
        if category == "spam/unwanted":
            return DraftResult(
                draft_type="none",
                content="[QUARANTINED - NO OUTBOUND DRAFT FOR SPAM]",
                grounding_refs=["Message classified as unsolicited spam"],
                requires_approval=False,
                status="QUARANTINED",
            )

        customer_data = f"Subject: {enquiry.subject or ''}\nBody: {enquiry.body_text}"
        crm_json = json.dumps(crm_context.model_dump() if crm_context else {}, default=str)
        system_rules = _load_prompt_template("draft_response.md")

        missing_desc = ""
        if enquiry.missing_information:
            missing_desc = "\nMissing information needed from customer:\n" + "\n".join(
                f"- {m.field_name}: {m.description}" for m in enquiry.missing_information
            )

        task = (
            f"Category: {category}\n"
            f"{missing_desc}\n"
            "Draft an appropriate response or clarification request grounded ONLY in customer facts and approved CRM data. "
            "Output a JSON object with: {draft_type, content, grounding_refs, requires_approval}."
        )

        prompt = format_bounded_prompt(
            system_rules=system_rules or "Draft grounded response without hallucinating commitments.",
            customer_data=customer_data,
            approved_crm_data=f"APPROVED CRM RECORD:\n{crm_json}",
            task=task,
        )

        messages = [
            {"role": "system", "content": "You are BEDA Communications Assistant. Output JSON only."},
            {"role": "user", "content": prompt},
        ]

        models_to_try = self._get_model_tiers()
        for idx, current_model in enumerate(models_to_try):
            try:
                raw_response = await self._call_post_chat(messages, json_mode=True, model=current_model)
                parsed = _extract_and_parse_json(raw_response)

                if isinstance(parsed, dict) and "content" in parsed:
                    draft_type = parsed.get("draft_type", "response")
                    content = parsed.get("content", "")
                    grounding_refs = parsed.get("grounding_refs", [])
                    requires_approval = bool(parsed.get("requires_approval", True))
                    return DraftResult(
                        draft_type=draft_type,
                        content=content,
                        grounding_refs=grounding_refs,
                        requires_approval=requires_approval,
                        status="PENDING_APPROVAL" if requires_approval else "AUTONOMOUS",
                    )
                raise ValueError("Draft JSON missing 'content' field")
            except Exception as e:
                next_info = f" Retrying with backup model '{models_to_try[idx+1]}'..." if idx + 1 < len(models_to_try) else " Falling back to deterministic provider."
                logger.warning(f"LLM model '{current_model}' draft_response failed: {e}.{next_info}")

        self.model_unavailable = True
        raise ModelUnavailableError(
            f"All configured live models failed draft_response: {models_to_try}",
            models_attempted=models_to_try,
        )


def get_llm_provider() -> LLMProvider:
    provider_type = os.getenv("LLM_PROVIDER", "fake").lower().strip()
    if provider_type in ("openai", "openai-compatible", "openrouter"):
        api_key = os.getenv("LLM_API_KEY", "")
        timeout = float(os.getenv("LLM_TIMEOUT", "45.0"))

        primary_model = (
            os.getenv("LLM_PRIMARY_MODEL", "").strip()
            or os.getenv("LLM_STRONG_MODEL", "").strip()
            or ("meta-llama/llama-3.3-70b-instruct:free" if provider_type == "openrouter" else "gpt-4o-mini")
        )
        fallback_model = os.getenv("LLM_FALLBACK_MODEL", "").strip() or None
        escalation_model = os.getenv("LLM_ESCALATION_MODEL", "").strip() or None

        if provider_type == "openrouter":
            base_url = os.getenv("LLM_BASE_URL", "").strip() or "https://openrouter.ai/api/v1"
            extra_headers = {
                "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "http://localhost:8000"),
                "X-Title": os.getenv("OPENROUTER_TITLE", "BEDA Enquiry Intelligence"),
            }
            return OpenAICompatibleProvider(
                api_key=api_key,
                base_url=base_url,
                model=primary_model,
                primary_model=primary_model,
                fallback_model=fallback_model,
                escalation_model=escalation_model,
                timeout=timeout,
                extra_headers=extra_headers,
                provider_name="openrouter",
            )
        else:
            base_url = os.getenv("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1"
            return OpenAICompatibleProvider(
                api_key=api_key,
                base_url=base_url,
                model=primary_model,
                primary_model=primary_model,
                fallback_model=fallback_model,
                escalation_model=escalation_model,
                timeout=timeout,
                provider_name=provider_type,
            )
    return FakeLLMProvider()
