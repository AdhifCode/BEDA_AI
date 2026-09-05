# Runbook: Prompt Injection Attempt Defense

## 1. Detection
- Inbound enquiry contains delimiter override attempts (e.g. `<<<END_CUSTOMER_DATA>>>`, `IGNORE ALL INSTRUCTIONS`).
- Security log warning from `packages/validation/prompt_safety.py`.

## 2. Immediate Containment
- Architecture strictly treats customer content as DATA inside explicit delimiters.
- LLM is constrained by deterministic code: cannot execute tools or write to DB directly.
- Consequential actions (Tier 5) are physically gated behind human approval.

## 3. Diagnosis
- Review message text in Review UI.
- Verify that classification and extraction remained bounded.

## 4. Recovery
- If flagged as malicious abuse, tag enquiry as `QUARANTINED` or reject review task.

## 5. Verification
- Verify no unauthorized side effects occurred in `outbox_events`.

## 6. Audit Requirements
- Record `PROMPT_INJECTION_FLAGGED` in audit log with raw payload hash.
