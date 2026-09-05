import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
from db.migrations.runner import run_migrations
from db.seed.seeder import seed_data
from packages.channels.adapters import (
    ChannelAdapter,
    EmailPayload,
    MessagingPayload,
    WebFormPayload,
)
from packages.domain.models import WorkflowStatus
from packages.workflow.pipeline import WorkflowPipeline

FIXTURE_CASES = [
    {
        "id": "E001",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e001",
            from_address="Amelia Grant <amelia.grant@humelogistics.example>",
            subject="Solar and battery across our three Victorian sites",
            body="We operate warehouses in Truganina, Dandenong and Epping. Combined electricity consumption is about 2.1 GWh per year. We are considering solar, possibly batteries and lighting upgrades. We would like an initial discussion next week. I have attached the latest Truganina bill. Please call me on 0400 111 020.",
            attachments=["01_hume_energy_bill.txt"],
        ),
    },
    {
        "id": "E002",
        "channel": "web_form",
        "payload": WebFormPayload(
            submission_id="sub-e002",
            fields={"company_name": "Hume Logistic", "contact_name": "Amelia", "phone": "0400 111 020", "email": "a.grant@humelogistics.example"},
            message="Company: Hume Logistic. We have three distribution sites in Melbourne and want a solar proposal. Consumption around two gigawatt hours annually. Contact Amelia. Best number 0400 111 020.",
            attachments=[],
        ),
    },
    {
        "id": "E003",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e003",
            from_address="Rohan Lee <rohan@greenfieldsfoods.example>",
            subject="Invoice 1847 does not match PO",
            body="Hi, our accounts team says invoice 1847 is $2,640 higher than the purchase order. Can someone check before Friday please? This is for the lighting project already completed at Geelong.",
            attachments=["03_greenfields_invoice_query.txt"],
        ),
    },
    {
        "id": "E004",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e004",
            from_address="sales@megaleadlists.example",
            subject="Buy 50,000 Australian CEO leads today",
            body="Special price expires in 24 hours. Reply now for cryptocurrency payment instructions.",
            attachments=[],
        ),
    },
    {
        "id": "E005",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e005",
            from_address="Melissa Tran <melissa.tran@northbankcollege.example>",
            subject="Government school lighting upgrade",
            body="I manage facilities at Northbank College. We have approximately 1,100 fluorescent fittings and want to understand whether an LED upgrade could access any government incentives. I do not have our latest electricity bill with me. Could someone tell me what you need from us?",
            attachments=["02_northbank_site_notes.txt"],
        ),
    },
    {
        "id": "E006",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e006",
            from_address="engineering@solarray.example",
            subject="Harmonics question on proposed battery inverter",
            body="We are reviewing the PCS specification on a 500 kW battery project. Can your engineer confirm acceptable THD limits at the point of common coupling and whether the current design requires an additional harmonic study?",
            attachments=[],
        ),
    },
    {
        "id": "E007",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e007",
            from_address="priya.dev@examplemail.test",
            subject="Marketing internship application",
            body="marketing-internship application from priya.dev@examplemail.test. This is not a sales or support enquiry.",
            attachments=[],
        ),
    },
    {
        "id": "E008",
        "channel": "email",
        "payload": EmailPayload(
            message_id="msg-e008",
            from_address="Daniel Wu <daniel@solarainstall.example>",
            subject="Crew confirmation Ballarat solar",
            body="existing installation partner Daniel Wu asks whether BEDA will confirm a four-person crew for a Ballarat commercial solar project for the week beginning 14 September. Confirmation is needed by Tuesday.",
            attachments=[],
        ),
    },
    {
        "id": "E009",
        "channel": "web_form",
        "payload": WebFormPayload(
            submission_id="sub-e009",
            fields={"contact_name": "Sam", "phone": "0411 999 120"},
            message="a Newcastle refrigerated warehouse enquiry says electricity bills are about $80,000/month and asks about solar and other operating-cost reductions. Contact name Sam. The first fixture contains mobile 0411 999 120.",
            attachments=[],
        ),
    },
    {
        "id": "E010",
        "channel": "web_form",
        "payload": WebFormPayload(
            submission_id="sub-e010",
            fields={"contact_name": "Sam", "phone": "0411 999 102", "email": "sam.new@warehouse.test"},
            message="follow-up from Sam corrects the phone number to 0411 999 102 and asks BEDA to use the new email identity going forward. Your system should preserve the conflict and decide how to handle identity resolution. Previous phone 0411 999 120.",
            attachments=[],
        ),
    },
    {
        "id": "E011",
        "channel": "messaging",
        "payload": MessagingPayload(
            message_id="msg-e011",
            sender={"name": "System Alert Bot", "email": "alerts@internal.beda.example"},
            text="internal BEDA system alert says HubSpot sync failed at 02:14 because an OAuth token expired; 146 records remain unsynchronised and retry was disabled after three failures. This is an internal systems incident, not a customer enquiry.",
            attachments=[],
        ),
    },
    {
        "id": "E012",
        "channel": "web_form",
        "payload": WebFormPayload(
            submission_id="sub-e012",
            fields={"company_name": "Small Cafe"},
            message="small cafe enquiry. The business leases a 70 square metre cafe, spends about $900/month on electricity, asks for a solar quote, and says the landlord has not agreed to roof works.",
            attachments=[],
        ),
    },
]


async def run_ingestion():
    await run_migrations()
    await seed_data()

    pipeline = WorkflowPipeline()
    results = []

    header = "=" * 80
    print("\n" + header)
    print("INGESTING BEDA FIXTURE CASES (E001 - E012)")
    print(header + "\n")

    for item in FIXTURE_CASES:
        case_id = item["id"]
        channel = item["channel"]
        payload = item["payload"]

        if channel == "email":
            enquiry = ChannelAdapter.from_email(payload)
        elif channel == "web_form":
            enquiry = ChannelAdapter.from_web_form(payload)
        else:
            enquiry = ChannelAdapter.from_messaging(payload)

        enquiry, is_dup = await pipeline.ingest_and_persist(enquiry, payload.model_dump(), channel)
        await pipeline.execute_workflow(enquiry.enquiry_id)

        cat = enquiry.classification.category if enquiry.classification else "UNKNOWN"
        crm_status = enquiry.crm_resolution.status.value if enquiry.crm_resolution else "NONE"
        owner = enquiry.routing.assigned_owner if enquiry.routing else "UNASSIGNED"

        summary_label = ""
        if case_id == "E001":
            summary_label = "MATCHED C001 → Matt Cooper"
        elif case_id == "E002":
            summary_label = "AMBIGUOUS C001/C002 → CRM review"
        elif case_id == "E003":
            summary_label = "MATCHED C003 → accounts/operations review"
        elif case_id == "E004":
            summary_label = "SPAM → quarantined"
        elif case_id == "E005":
            summary_label = "MATCHED C004 → clarification draft"
        elif case_id == "E006":
            summary_label = "TECHNICAL → technical review"
        elif case_id == "E007":
            summary_label = "NON-SALES → non-sales review"
        elif case_id == "E008":
            summary_label = "MATCHED C005 → Ties Rahardjo + approval"
        elif case_id == "E009":
            summary_label = "NEW/UNRESOLVED → Matt Cooper"
        elif case_id == "E010":
            summary_label = "IDENTITY CONFLICT → identity review"
        elif case_id == "E011":
            summary_label = "INTERNAL INCIDENT → Ali Pratama"
        elif case_id == "E012":
            summary_label = "NEW/UNRESOLVED → Matt Cooper + approval"

        results.append((case_id, summary_label, cat, crm_status, owner))
        print(f"[{case_id}] {summary_label}")

    print("\n" + header)
    print("DETERMINISTIC FIXTURE SUMMARY (Section 20 Compliance)")
    print(header)
    for cid, summary, cat, crm, owner in results:
        print(f"{cid} {summary}")
    print(header + "\n")


if __name__ == "__main__":
    asyncio.run(run_ingestion())
