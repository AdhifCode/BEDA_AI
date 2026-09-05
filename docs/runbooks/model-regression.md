# Runbook: Model / Prompt Regression

## 1. Detection
- Drop in classification confidence below threshold (`0.80`).
- Spike in `AI_REVIEW` review tasks.
- Fixture acceptance test regression.

## 2. Immediate Containment
- Revert prompt version or switch to fallback model via environment variables.

## 3. Diagnosis
- Run evaluation dataset: `pytest tests/evaluation/`.
- Inspect model token outputs and grounding violations.

## 4. Recovery
- Update prompt template in `prompts/` to clarify ambiguous classification boundaries.
- Re-run regression tests against `data.txt` fixture set.

## 5. Verification
- Confirm 100% pass rate on `tests/fixtures/test_fixtures.py`.

## 6. Audit Requirements
- Document prompt version change in version control and audit logs.
