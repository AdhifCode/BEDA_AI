import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from packages.ai_gateway.llm_provider import get_llm_provider
from packages.domain.models import CanonicalEnquiry, SenderInfo, SourceChannel, WorkflowStatus

async def main():
    provider = get_llm_provider()
    provider_name = getattr(provider, "provider_name", type(provider).__name__)
    primary_model = getattr(provider, "primary_model", getattr(provider, "model", "default"))
    fallback_model = getattr(provider, "fallback_model", "None")
    escalation_model = getattr(provider, "escalation_model", "None")
    base_url = getattr(provider, "base_url", "N/A")
    api_key = getattr(provider, "api_key", None)
    has_key = bool(api_key and len(api_key) > 4)

    print("=" * 70)
    print("BEDA AI GATEWAY — LLM CONFIGURATION & MULTI-MODEL BACKUP TEST")
    print("=" * 70)
    print(f"  • LLM Provider:     {provider_name}")
    print(f"  • Base URL:         {base_url}")
    print(f"  • Primary Model:    {primary_model}")
    print(f"  • Fallback Model:   {fallback_model}")
    print(f"  • Escalation Model: {escalation_model}")
    print(f"  • API Key Set:      {'Yes (' + api_key[:8] + '...)' if has_key else 'No / None'}")
    print("=" * 70 + "\n")

    sample_enquiry = CanonicalEnquiry(
        enquiry_id="test-enquiry-001",
        idempotency_key="email:test-msg-001",
        source_channel=SourceChannel.EMAIL,
        sender=SenderInfo(name="Amelia Grant", email="amelia.grant@humelogistics.example", phone="0400 111 020"),
        subject="Solar and battery enquiry across three Victorian sites",
        body_text="We operate logistics warehouses in Truganina, Dandenong, and Epping. Combined electricity consumption is about 2.1 GWh per year. We are considering commercial solar, batteries, and LED lighting upgrades. Please call me on 0400 111 020.",
        workflow_status=WorkflowStatus.RECEIVED,
    )

    print("[1/3] Calling classify_and_extract()...")
    classification, extracted = await provider.classify_and_extract(sample_enquiry)
    stats = getattr(provider, "last_call_stats", {})

    print(f"  ✓ Category:    {classification.category}")
    print(f"  ✓ Confidence:  {classification.confidence:.2f}")
    print(f"  ✓ Intent:      {extracted.intent_summary}")
    print(f"  ✓ Energy Use:  {extracted.annual_or_monthly_energy_usage or 'Not specified'}")
    print(f"  ✓ Products:    {extracted.requested_products_or_services or []}")
    print(f"  ✓ Location:    {extracted.location or 'Not specified'}")
    print(f"  ✓ Active Model:{stats.get('model', 'N/A')}")
    print(f"  ✓ Attempted:   {stats.get('models_attempted', [])}")
    print(f"  ✓ Call Stats:  Latency={stats.get('latency_ms', 0)}ms, Fallback={stats.get('is_fallback', False)}")
    if stats.get("error_code"):
        print(f"  ℹ️ Status Note:  {stats.get('error_code')}")

    print("\n[2/3] Calling detect_missing_information()...")
    missing = await provider.detect_missing_information(sample_enquiry, extracted)
    print(f"  ✓ Identified {len(missing)} missing items:")
    for item in missing:
        print(f"    - {item.field_name}: {item.description}")

    print("\n[3/3] Calling draft_response()...")
    sample_enquiry.classification = classification
    sample_enquiry.extracted = extracted
    sample_enquiry.missing_information = missing
    draft = await provider.draft_response(sample_enquiry, crm_context=None)
    print(f"  ✓ Draft Type:        {draft.draft_type}")
    print(f"  ✓ Requires Approval: {draft.requires_approval}")
    print(f"  ✓ Draft Preview:\n    " + draft.content.replace('\n', '\n    '))

    print("\n" + "=" * 70)
    if stats.get("is_fallback"):
        print("RESULT: Deterministic fallback was used (e.g. offline/no key).")
    else:
        print("RESULT: SUCCESS! Live LLM response received successfully.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
