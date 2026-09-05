# Runbook: Dead-Letter Queue (DLQ) Replay

## 1. Detection
- Review task of type `DEAD_LETTER` created.
- Alert: `EnquiryMaxRetriesExceeded`.

## 2. Immediate Containment
- Poison message is safely isolated in `DEAD_LETTER` state without blocking subsequent enquiries.

## 3. Diagnosis
- Retrieve enquiry detail: `GET /api/v1/enquiries/{id}`.
- Inspect `workflow_steps` to pinpoint exact step and error code.

## 4. Recovery
- Correct underlying data issue or deploy patch.
- Trigger retry: `POST /api/v1/enquiries/{id}/retry`.

## 5. Verification
- Confirm enquiry transitions from `DEAD_LETTER` to `COMPLETED` or `APPROVAL`.

## 6. Audit Requirements
- System appends `DLQ_REPLAY_SUCCESS` to audit log with human reviewer actor ID.
