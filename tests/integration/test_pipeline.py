import pytest
import uuid
import httpx
from apps.api.main import app

@pytest.mark.asyncio
async def test_full_ingestion_and_approval_flow():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Ingest
        msg_id = f"integ-msg-{uuid.uuid4().hex[:8]}"
        payload = {
            "message_id": msg_id,
            "from": "Rohan Lee <rohan@greenfieldsfoods.example>",
            "subject": "Invoice 1847 discrepancy query",
            "body": "Hi, invoice 1847 is $2,640 higher than PO. Please check before Friday.",
            "attachments": [],
        }
        res = await client.post("/api/v1/ingest/email", json=payload)
        assert res.status_code == 200
        enq_id = res.json()["enquiry_id"]

        # Duplicate check
        dup_res = await client.post("/api/v1/ingest/email", json=payload)
        assert dup_res.status_code == 200
        assert dup_res.json()["duplicate"] is True
        assert dup_res.json()["enquiry_id"] == enq_id

        # Query enquiry
        enq_res = await client.get(f"/api/v1/enquiries/{enq_id}")
        assert enq_res.status_code == 200
        data = enq_res.json()
        assert data["crm_resolution"]["selected_customer_id"] == "C003"
        assert data["workflow_status"] == "APPROVAL"

        # Check reviews
        rev_res = await client.get("/api/v1/reviews?status=OPEN")
        reviews = rev_res.json()
        task = next(t for t in reviews if t["enquiry_id"] == enq_id)

        # Approve
        dec_res = await client.post(
            f"/api/v1/reviews/{task["review_id"]}/decision",
            json={"decision": "APPROVE", "actor_id": "ties-rahardjo", "actor_role": "reviewer", "reason": "Verified against PO"},
        )
        assert dec_res.status_code == 200

        # Verify audit chain
        audit_res = await client.get(f"/api/v1/enquiries/{enq_id}/audit")
        assert audit_res.json()["chain_valid"] is True
