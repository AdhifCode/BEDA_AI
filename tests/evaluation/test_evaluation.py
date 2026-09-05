import pytest
from packages.domain.models import CanonicalEnquiry, SenderInfo, SourceChannel
from packages.ai_gateway.llm_provider import FakeLLMProvider

@pytest.mark.asyncio
async def test_no_hallucination_on_missing_fields():
    provider = FakeLLMProvider()
    enq5 = CanonicalEnquiry(
        idempotency_key="eval-e005",
        source_channel=SourceChannel.EMAIL,
        sender=SenderInfo(email="melissa.tran@northbankcollege.example", name="Melissa Tran"),
        subject="Government school lighting upgrade",
        body_text="We have approximately 1,100 fluorescent fittings and want to understand whether an LED upgrade could access any government incentives. I do not have our latest electricity bill with me.",
    )
    cls, ext = await provider.classify_and_extract(enq5)
    enq5.classification = cls
    enq5.extracted = ext

    assert ext.budget is None
    assert ext.annual_or_monthly_energy_usage is None

    missing = await provider.detect_missing_information(enq5, ext)
    field_names = [m.field_name for m in missing]
    assert "electricity_bill" in field_names
    assert "fixture_schedule" in field_names

    draft = await provider.draft_response(enq5, None)
    assert "guaranteed" not in draft.content.lower()
    assert "$0" not in draft.content
