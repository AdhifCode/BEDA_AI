# Runbook: Duplicate Event Storm

## 1. Detection
- High frequency of identical `idempotency_key` inserts.
- Spikes in `DUPLICATE_IGNORED` audit events.

## 2. Immediate Containment
- Database unique constraint on `idempotency_key` blocks duplicate row creation atomically.
- Zero outbound side effects are triggered for duplicates.

## 3. Diagnosis
- Identify originating channel IP or webhook caller.
- Check webhook retry loop on upstream channel provider.

## 4. Recovery
- Rate limit sender IP at API Gateway / WAF level if abusive.

## 5. Verification
- Verify existing enquiry records are uncorrupted and count matches expectations.

## 6. Audit Requirements
- Duplicate ignored events are recorded and hash chained without side effect dispatch.
