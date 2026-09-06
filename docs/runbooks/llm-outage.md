# Runbook: LLM Provider Outage

## 1. Detection
- CloudWatch Alarm: `LLMRequestErrorRateHigh` (> 5% 5xx or timeouts for 5 minutes).
- Sentry alerts on `httpx.TimeoutException` or `httpx.HTTPStatusError` in `packages/ai_gateway/`.
- Worker logs report repeated step retries on `AI_UNDERSTANDING`.

## 2. Immediate Containment
- Switch runtime LLM provider to fallback / deterministic gateway:
  `kubectl set env deployment/beda-worker LLM_PROVIDER=fake` or update AWS Parameter Store `/beda/llm_provider` to backup vendor.
- Enquiries accumulate safely in Redis Streams durable queue without loss of customer messages.

## 3. Diagnosis
- Check upstream vendor status page (e.g. OpenAI status, Google Cloud Vertex status).
- Verify API key quota and rate limits.
- Test endpoint reachability: `curl -I $LLM_BASE_URL/health`.

## 4. Recovery
- Once upstream recovers, restore primary provider: `LLM_PROVIDER=openai`.
- Trigger queue consumer to process backlogged messages.

## 5. Verification
- Submit smoke test enquiry: `python scripts/smoke_test.py`.
- Confirm `workflow_status` reaches `VALIDATED` or `APPROVAL`.

## 6. Audit Requirements
- System records `AI_OUTAGE_FALLBACK_ENGAGED` in the audit event log with timestamp and actor `SYSTEM`.
