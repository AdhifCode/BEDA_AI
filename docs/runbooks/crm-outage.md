# Runbook: CRM Provider Outage

## 1. Detection
- Step failure alerts on `CRM_RESOLUTION` in worker logs.
- CloudWatch metric: `CRMAdapterConnectionFailures > 3`.

## 2. Immediate Containment
- System idempotency guard persists all inbound enquiries in state `RECEIVED` / `VALIDATED`.
- No customer records are lost; outbox side effects remain pending.

## 3. Diagnosis
- Verify CRM API credentials and OAuth token validity.
- Inspect network egress / VPC peering connection to CRM host.

## 4. Recovery
- Re-authenticate or resolve network route.
- Execute retry endpoint on pending enquiries:
  `POST /api/v1/enquiries/{id}/retry`.

## 5. Verification
- Verify candidate matches resolve and state advances to `CRM_RESOLUTION`.
- Inspect candidate score in Review UI.

## 6. Audit Requirements
- Record `CRM_OUTAGE_RETRY_DISPATCHED` audit entry. Verify hash chain continuity.
