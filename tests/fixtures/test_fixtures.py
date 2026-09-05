import pytest
import uuid
import httpx
from apps.api.main import app

@pytest.mark.asyncio
async def test_all_e001_to_e012_acceptance():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # E001: Hume Logistics
        r1 = await client.post("/api/v1/ingest/email", json={
            "message_id": f"accept-e001-{uuid.uuid4().hex[:6]}",
            "from": "Amelia Grant <amelia.grant@humelogistics.example>",
            "subject": "Solar and battery across our three Victorian sites",
            "body": "Warehouses in Truganina, Dandenong and Epping. Consumption 2.1 GWh/yr. Please call 0400 111 020.",
            "attachments": ["01_hume_energy_bill.txt"]
        })
        e1 = (await client.get(f"/api/v1/enquiries/{r1.json()["enquiry_id"]}")).json()
        assert e1["crm_resolution"]["selected_customer_id"] == "C001"
        assert e1["crm_resolution"]["status"] == "MATCHED"

        # E002: Hume Logistic duplicate / ambiguity
        r2 = await client.post("/api/v1/ingest/web-form", json={
            "submission_id": f"accept-e002-{uuid.uuid4().hex[:6]}",
            "fields": {"company_name": "Hume Logistic", "phone": "0400 111 020", "email": "a.grant@humelogistics.example"},
            "message": "Company: Hume Logistic. Three sites in Melbourne, solar proposal.",
            "attachments": []
        })
        e2 = (await client.get(f"/api/v1/enquiries/{r2.json()["enquiry_id"]}")).json()
        assert e2["crm_resolution"]["status"] == "AMBIGUOUS"
        assert e2["workflow_status"] == "CRM_REVIEW"

        # E003: Greenfields discrepancy
        r3 = await client.post("/api/v1/ingest/email", json={
            "message_id": f"accept-e003-{uuid.uuid4().hex[:6]}",
            "from": "Rohan Lee <rohan@greenfieldsfoods.example>",
            "subject": "Invoice 1847 does not match PO",
            "body": "Invoice 1847 is $2,640 higher than PO. Check before Friday please.",
            "attachments": ["03_greenfields_invoice_query.txt"]
        })
        e3 = (await client.get(f"/api/v1/enquiries/{r3.json()["enquiry_id"]}")).json()
        assert e3["crm_resolution"]["selected_customer_id"] == "C003"
        assert "$2,640" in e3["ai_understanding"]["extracted"]["intent_summary"]

        # E004: Spam
        r4 = await client.post("/api/v1/ingest/email", json={
            "message_id": f"accept-e004-{uuid.uuid4().hex[:6]}",
            "from": "sales@megaleadlists.example",
            "subject": "Buy 50,000 Australian CEO leads today",
            "body": "Special price crypto payment instructions.",
            "attachments": []
        })
        e4 = (await client.get(f"/api/v1/enquiries/{r4.json()["enquiry_id"]}")).json()
        assert e4["workflow_status"] == "QUARANTINED"

        # E005: Northbank College
        r5 = await client.post("/api/v1/ingest/email", json={
            "message_id": f"accept-e005-{uuid.uuid4().hex[:6]}",
            "from": "Melissa Tran <melissa.tran@northbankcollege.example>",
            "subject": "Government school lighting upgrade",
            "body": "1,100 fluorescent fittings, LED upgrade, no electricity bill with me.",
            "attachments": ["02_northbank_site_notes.txt"]
        })
        e5 = (await client.get(f"/api/v1/enquiries/{r5.json()["enquiry_id"]}")).json()
        assert e5["crm_resolution"]["selected_customer_id"] == "C004"
        assert e5["draft"]["draft_type"] == "clarification"

        # E006: Technical harmonics
        r6 = await client.post("/api/v1/ingest/email", json={
            "message_id": f"accept-e006-{uuid.uuid4().hex[:6]}",
            "from": "engineering@solarray.example",
            "subject": "Harmonics question on proposed battery inverter",
            "body": "PCS specification on a 500 kW battery project. Acceptable THD limits?",
            "attachments": []
        })
        e6 = (await client.get(f"/api/v1/enquiries/{r6.json()["enquiry_id"]}")).json()
        assert e6["review_tasks"][0]["task_type"] == "TECHNICAL_REVIEW"

        # E008: Solara Installations crew confirmation
        r8 = await client.post("/api/v1/ingest/email", json={
            "message_id": f"accept-e008-{uuid.uuid4().hex[:6]}",
            "from": "Daniel Wu <daniel@solarainstall.example>",
            "subject": "Crew confirmation Ballarat solar",
            "body": "Confirm four-person crew Ballarat week beginning 14 September.",
            "attachments": []
        })
        e8 = (await client.get(f"/api/v1/enquiries/{r8.json()["enquiry_id"]}")).json()
        assert e8["crm_resolution"]["selected_customer_id"] == "C005"
        assert e8["draft"]["requires_approval"] is True

        # E010: Identity conflict
        r10 = await client.post("/api/v1/ingest/web-form", json={
            "submission_id": f"accept-e010-{uuid.uuid4().hex[:6]}",
            "fields": {"contact_name": "Sam", "phone": "0411 999 102", "email": "sam.new@warehouse.test"},
            "message": "Corrects phone number to 0411 999 102 from 0411 999 120.",
            "attachments": []
        })
        e10 = (await client.get(f"/api/v1/enquiries/{r10.json()["enquiry_id"]}")).json()
        assert e10["crm_resolution"]["status"] == "AMBIGUOUS"
        assert len(e10["crm_resolution"]["conflict_flags"]) > 0

        # E011: Internal incident
        r11 = await client.post("/api/v1/ingest/messaging", json={
            "message_id": f"accept-e011-{uuid.uuid4().hex[:6]}",
            "sender": {"name": "Alerts", "email": "alerts@internal.beda.example"},
            "text": "HubSpot sync failed at 02:14 because OAuth token expired. 146 records unsynchronised.",
            "attachments": []
        })
        e11 = (await client.get(f"/api/v1/enquiries/{r11.json()["enquiry_id"]}")).json()
        assert e11["workflow_status"] == "COMPLETED"
        assert e11["draft"]["draft_type"] == "internal_alert"

        # E012: Small cafe landlord dependency
        r12 = await client.post("/api/v1/ingest/web-form", json={
            "submission_id": f"accept-e012-{uuid.uuid4().hex[:6]}",
            "fields": {"company_name": "Small Cafe"},
            "message": "70 square metre cafe, $900/month electricity, landlord has not agreed to roof works.",
            "attachments": []
        })
        e12 = (await client.get(f"/api/v1/enquiries/{r12.json()["enquiry_id"]}")).json()
        assert e12["draft"]["requires_approval"] is True
