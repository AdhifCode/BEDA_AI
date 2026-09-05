import asyncio
import os
import sys
import uuid
import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


async def main():
    print("\n" + "=" * 80)
    print("RUNNING BEDA API SMOKE TESTS")
    print("=" * 80 + "\n")

    transport = httpx.ASGITransport(app=None)
    # If running against in-memory ASGI app
    from apps.api.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Health checks
        print("[1/7] Testing health live endpoint...")
        res = await client.get("/health/live")
        assert res.status_code == 200, f"Health live failed: {res.text}"
        print("  ✓ Health live OK:", res.json())

        print("[2/7] Testing health ready endpoint...")
        res = await client.get("/health/ready")
        assert res.status_code == 200, f"Health ready failed: {res.text}"
        print("  ✓ Health ready OK:", res.json())

        # 2. Ingestion
        print("[3/7] Testing email ingestion endpoint...")
        payload = {
            "message_id": f"smoke-test-msg-{uuid.uuid4().hex[:8]}",
            "from": "Amelia Grant <amelia.grant@humelogistics.example>",
            "subject": "Smoke Test Commercial Solar",
            "body": "We operate warehouses in Truganina, Dandenong and Epping. Combined electricity consumption is about 2.1 GWh per year.",
            "attachments": [],
        }
        res = await client.post("/api/v1/ingest/email", json=payload)
        assert res.status_code == 200, f"Ingest failed: {res.text}"
        data = res.json()
        enquiry_id = data["enquiry_id"]
        assert not data["duplicate"], "First delivery should not be duplicate"
        print(f"  ✓ Ingestion OK (enquiry_id: {enquiry_id}, duplicate: {data['duplicate']})")

        # 3. Duplicate Delivery (Idempotency)
        print("[4/7] Testing duplicate idempotency detection...")
        dup_res = await client.post("/api/v1/ingest/email", json=payload)
        assert dup_res.status_code == 200
        dup_data = dup_res.json()
        assert dup_data["duplicate"], "Duplicate delivery MUST be flagged as duplicate"
        assert dup_data["enquiry_id"] == enquiry_id, "Duplicate must return existing enquiry ID"
        print(f"  ✓ Idempotency OK (duplicate detected, same ID {enquiry_id})")

        # 4. Enquiry query
        print("[5/7] Querying enquiry details...")
        enq_res = await client.get(f"/api/v1/enquiries/{enquiry_id}")
        assert enq_res.status_code == 200
        enq_data = enq_res.json()
        assert enq_data["crm_resolution"]["selected_customer_id"] == "C001"
        assert enq_data["draft"]["requires_approval"] is True
        print(f"  ✓ Enquiry query OK (CRM match: C001, status: {enq_data['workflow_status']})")

        # 5. Reviews and RBAC
        print("[6/7] Testing review task & RBAC authorization...")
        rev_res = await client.get("/api/v1/reviews?status=OPEN")
        assert rev_res.status_code == 200
        reviews = rev_res.json()
        target_task = next((t for t in reviews if t["enquiry_id"] == enquiry_id), None)
        assert target_task is not None, "Open review task must exist for consequential enquiry"
        review_id = target_task["review_id"]

        # Test unauthorized actor (audit_reader cannot approve)
        unauth_res = await client.post(
            f"/api/v1/reviews/{review_id}/decision",
            json={"decision": "APPROVE", "actor_id": "auditor-01", "actor_role": "audit_reader", "reason": "Test"},
        )
        assert unauth_res.status_code == 403, "audit_reader must be forbidden from approving tasks"
        print("  ✓ RBAC defense OK (audit_reader received 403 Forbidden)")

        # Test authorized approval
        auth_res = await client.post(
            f"/api/v1/reviews/{review_id}/decision",
            json={"decision": "APPROVE", "actor_id": "matt-cooper", "actor_role": "admin", "reason": "Approved for dispatch"},
        )
        assert auth_res.status_code == 200
        print(f"  ✓ Review decision approved OK by Matt Cooper")

        # 6. Audit Trail Cryptographic Verification
        print("[7/7] Testing audit trail & cryptographic verification...")
        audit_res = await client.get(f"/api/v1/enquiries/{enquiry_id}/audit")
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["chain_valid"] is True, "Audit hash chain MUST be valid"
        assert audit_data["total_events"] >= 4
        print(f"  ✓ Audit chain OK: verified {audit_data['total_events']} cryptographically linked events")

    print("\n" + "=" * 80)
    print("ALL API SMOKE TESTS PASSED SUCCESSFULLY!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
