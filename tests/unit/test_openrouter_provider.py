import json
import pytest
import httpx
from packages.domain.models import CanonicalEnquiry, ExtractedInformation, CRMCandidate, SourceChannel
from packages.ai_gateway.llm_provider import (
    OpenAICompatibleProvider,
    _extract_and_parse_json,
    get_llm_provider,
)


def test_extract_and_parse_json_raw():
    payload = '{"category": "commercial opportunity", "confidence": 0.95}'
    result = _extract_and_parse_json(payload)
    assert result["category"] == "commercial opportunity"
    assert result["confidence"] == 0.95


def test_extract_and_parse_json_markdown_block():
    payload = """Here is the extracted data:
```json
{
  "category": "technical engineering",
  "confidence": 0.90
}
```
Please let me know if you need anything else!"""
    result = _extract_and_parse_json(payload)
    assert result["category"] == "technical engineering"
    assert result["confidence"] == 0.90


def test_extract_and_parse_json_markdown_no_lang():
    payload = """```
{
  "category": "partner/operations",
  "confidence": 0.88
}
```"""
    result = _extract_and_parse_json(payload)
    assert result["category"] == "partner/operations"


def test_extract_and_parse_json_embedded_braces():
    payload = "Leading text before JSON: {\"status\": \"ok\", \"count\": 3} trailing text after."
    result = _extract_and_parse_json(payload)
    assert result["status"] == "ok"
    assert result["count"] == 3


def test_extract_and_parse_json_array():
    payload = '[{"field_name": "electricity_bill", "reason": "Required for tariff assessment"}]'
    result = _extract_and_parse_json(payload)
    assert isinstance(result, list)
    assert result[0]["field_name"] == "electricity_bill"


def test_extract_and_parse_json_invalid():
    with pytest.raises(ValueError):
        _extract_and_parse_json("This is completely unparseable text without any json.")


def test_get_llm_provider_openrouter(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "sk-or-v1-mock-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_STRONG_MODEL", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_MODEL", raising=False)

    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.model == "meta-llama/llama-3.3-70b-instruct:free"
    assert provider.extra_headers.get("X-Title") == "BEDA Enquiry Intelligence"
    assert "HTTP-Referer" in provider.extra_headers


def test_get_llm_provider_tiered_models(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "sk-or-v1-mock-key")
    monkeypatch.setenv("LLM_PRIMARY_MODEL", "z-ai/glm-5.2:free")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "google/gemma-4-26b-a4b-it:free")
    monkeypatch.setenv("LLM_ESCALATION_MODEL", "google/gemma-4-31b-it:free")

    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.primary_model == "z-ai/glm-5.2:free"
    assert provider.fallback_model == "google/gemma-4-26b-a4b-it:free"
    assert provider.escalation_model == "google/gemma-4-31b-it:free"
    assert provider._get_model_tiers() == [
        "z-ai/glm-5.2:free",
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
    ]


@pytest.mark.asyncio
async def test_openai_compatible_provider_mock_classify():
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "classification": {
                            "category": "commercial opportunity",
                            "confidence": 0.94,
                            "reason_code": "SOLAR_COMMERCIAL_PROPOSAL",
                            "provenance": ["commercial warehouse solar"],
                        },
                        "extracted": {
                            "contact_name": "Alex",
                            "company_name": "Metro Storage",
                            "location": "Brisbane",
                            "intent_summary": "Looking for rooftop solar",
                        },
                    })
                }
            }
        ]
    }

    provider = OpenAICompatibleProvider(
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        model="meta-llama/llama-3.3-70b-instruct:free",
    )

    async def mock_post(messages, json_mode=True):
        return mock_response["choices"][0]["message"]["content"]

    provider._post_chat_completion = mock_post

    enquiry = CanonicalEnquiry(
        idempotency_key="mock-key-1",
        source_channel=SourceChannel.EMAIL,
        subject="Rooftop solar inquiry",
        body_text="Hi, we run Metro Storage in Brisbane and need solar.",
    )

    cls_res, ext_res = await provider.classify_and_extract(enquiry)
    assert cls_res.category == "commercial opportunity"
    assert cls_res.confidence == 0.94
    assert ext_res.company_name == "Metro Storage"


@pytest.mark.asyncio
async def test_openai_compatible_provider_fallback_on_error():
    provider = OpenAICompatibleProvider(
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        model="meta-llama/llama-3.3-70b-instruct:free",
    )

    async def failing_post(messages, json_mode=True):
        raise RuntimeError("OpenRouter 429 Rate limit exceeded")

    provider._post_chat_completion = failing_post

    # Fixture E004 is Spam in FakeLLMProvider
    enquiry = CanonicalEnquiry(
        idempotency_key="mock-key-spam",
        source_channel=SourceChannel.EMAIL,
        subject="Buy 50,000 Australian CEO leads today",
        body_text="Special price crypto payment instructions.",
        sender={"email": "sales@megaleadlists.example"},
    )

    cls_res, ext_res = await provider.classify_and_extract(enquiry)
    # Must gracefully fall back to deterministic FakeLLMProvider result
    assert cls_res.category == "spam/unwanted"
    assert cls_res.confidence == 0.99


@pytest.mark.asyncio
async def test_openai_compatible_provider_mock_detect_missing():
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps([
                        {
                            "field_name": "electricity_bill",
                            "description": "12-month interval data",
                            "required_for_category": "commercial opportunity",
                            "reason": "Required to engineer system sizing",
                        }
                    ])
                }
            }
        ]
    }

    provider = OpenAICompatibleProvider(
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        model="meta-llama/llama-3.3-70b-instruct:free",
    )

    async def mock_post(messages, json_mode=True):
        return mock_response["choices"][0]["message"]["content"]

    provider._post_chat_completion = mock_post

    enquiry = CanonicalEnquiry(
        idempotency_key="mock-key-miss",
        source_channel=SourceChannel.EMAIL,
        subject="Commercial solar proposal",
        body_text="Looking for solar proposal.",
    )
    extracted = ExtractedInformation(intent_summary="Solar proposal requested")

    missing = await provider.detect_missing_information(enquiry, extracted)
    assert len(missing) == 1
    assert missing[0].field_name == "electricity_bill"
    assert missing[0].reason == "Required to engineer system sizing"


@pytest.mark.asyncio
async def test_openai_compatible_provider_mock_draft_response():
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "draft_type": "response",
                        "content": "Hi Alex,\n\nThank you for reaching out to BEDA regarding rooftop solar for Metro Storage in Brisbane.",
                        "grounding_refs": ["Metro Storage Brisbane inquiry"],
                        "requires_approval": True,
                    })
                }
            }
        ]
    }

    provider = OpenAICompatibleProvider(
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        model="meta-llama/llama-3.3-70b-instruct:free",
    )

    async def mock_post(messages, json_mode=True):
        return mock_response["choices"][0]["message"]["content"]

    provider._post_chat_completion = mock_post

    enquiry = CanonicalEnquiry(
        idempotency_key="mock-key-draft",
        source_channel=SourceChannel.EMAIL,
        subject="Rooftop solar inquiry",
        body_text="Hi, we run Metro Storage in Brisbane.",
    )
    crm_cand = CRMCandidate(
        customer_id="C001",
        company_name="Metro Storage",
        contact_name="Alex",
    )

    draft = await provider.draft_response(enquiry, crm_cand)
    assert draft.draft_type == "response"
    assert "Metro Storage" in draft.content
    assert draft.requires_approval is True


@pytest.mark.asyncio
async def test_openai_compatible_provider_backup_model_fallback():
    provider = OpenAICompatibleProvider(
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        primary_model="z-ai/glm-5.2:free",
        fallback_model="google/gemma-4-26b-a4b-it:free",
        escalation_model="google/gemma-4-31b-it:free",
    )

    models_called = []

    async def mock_post(messages, json_mode=True, model_override=None):
        models_called.append(model_override)
        if model_override == "z-ai/glm-5.2:free":
            raise RuntimeError("Primary model rate limit 429")
        return json.dumps({
            "classification": {
                "category": "commercial opportunity",
                "confidence": 0.91,
                "reason_code": "FALLBACK_MODEL_SUCCESS",
                "provenance": ["backup model"],
            },
            "extracted": {
                "intent_summary": "Recovered by backup model",
            },
        })

    provider._post_chat_completion = mock_post

    enquiry = CanonicalEnquiry(
        idempotency_key="mock-key-failover",
        source_channel=SourceChannel.EMAIL,
        subject="Solar proposal inquiry",
        body_text="Looking for commercial solar.",
    )

    cls_res, ext_res = await provider.classify_and_extract(enquiry)
    assert cls_res.category == "commercial opportunity"
    assert ext_res.intent_summary == "Recovered by backup model"
    assert models_called == ["z-ai/glm-5.2:free", "google/gemma-4-26b-a4b-it:free"]
    assert provider.last_call_stats["model"] == "google/gemma-4-26b-a4b-it:free"
    assert provider.last_call_stats["is_fallback"] is True


@pytest.mark.asyncio
async def test_openai_compatible_provider_escalation_on_low_confidence():
    provider = OpenAICompatibleProvider(
        api_key="sk-or-v1-test",
        base_url="https://openrouter.ai/api/v1",
        primary_model="z-ai/glm-5.2:free",
        fallback_model="google/gemma-4-26b-a4b-it:free",
        escalation_model="google/gemma-4-31b-it:free",
    )

    models_called = []

    async def mock_post(messages, json_mode=True, model_override=None):
        models_called.append(model_override)
        if model_override == "z-ai/glm-5.2:free":
            # Return low confidence (0.65 < 0.80) to trigger escalation
            return json.dumps({
                "classification": {
                    "category": "commercial opportunity",
                    "confidence": 0.65,
                    "reason_code": "AMBIGUOUS",
                    "provenance": [],
                },
                "extracted": {
                    "intent_summary": "Low confidence extraction",
                },
            })
        elif model_override == "google/gemma-4-26b-a4b-it:free":
            # Escalation to fallback returns higher confidence
            return json.dumps({
                "classification": {
                    "category": "commercial opportunity",
                    "confidence": 0.95,
                    "reason_code": "ESCALATED_HIGH_CONFIDENCE",
                    "provenance": ["escalated model"],
                },
                "extracted": {
                    "intent_summary": "High confidence extraction from backup model",
                },
            })

    provider._post_chat_completion = mock_post

    enquiry = CanonicalEnquiry(
        idempotency_key="mock-key-escalate",
        source_channel=SourceChannel.EMAIL,
        subject="Complex energy query",
        body_text="Ambiguous energy proposal request.",
    )

    cls_res, ext_res = await provider.classify_and_extract(enquiry)
    assert cls_res.confidence == 0.95
    assert ext_res.intent_summary == "High confidence extraction from backup model"
    assert "google/gemma-4-26b-a4b-it:free" in models_called

