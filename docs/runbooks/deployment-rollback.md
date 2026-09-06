# Runbook: Deployment Rollback

## 1. Detection
- Post-deployment smoke test failure or error spike (> 1% 5xx).

## 2. Immediate Containment
- Rollback container image tag to previous stable build:
  `aws ecs update-service --cluster beda --service beda-api --task-definition beda-api:PREVIOUS`.

## 3. Diagnosis
- Inspect logs of failed container version.
- Review recent commit diff and migration compatibility.

## 4. Recovery
- All migrations are strictly backward compatible; previous application version starts cleanly.

## 5. Verification
- `GET /health/ready` returns 200 OK.
- Run `python scripts/smoke_test.py`.

## 6. Audit Requirements
- Record deployment rollback event in deployment tracking system.
