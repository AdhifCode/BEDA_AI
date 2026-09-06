# Runbook: Database Restore from Backup

## 1. Detection
- Catastrophic database corruption or hardware instance loss.

## 2. Immediate Containment
- Stop API and Worker services to prevent inconsistent writes.

## 3. Diagnosis
- Identify latest valid snapshot from RDS / backup storage.

## 4. Recovery
- Restore snapshot to new RDS instance or restore SQLite database file.
- Point `DATABASE_URL` to restored database.
- Restart API and Worker services.

## 5. Verification
- Run `python scripts/smoke_test.py`.
- Run audit verification script: `AuditService.verify_chain()` on sample enquiries.

## 6. Audit Requirements
- Document restore point in time and verify hash chain integrity.
