import asyncio
from sqlalchemy import select
from db.database import get_db_session
from db.models.schema import UserModel, CRMCustomerModel
from packages.observability.logger import get_logger

logger = get_logger("seeder")

STAFF_USERS = [
    {"id": "matt-cooper", "email": "matt.cooper@beda.example", "name": "Matt Cooper", "role": "admin", "active": True},
    {"id": "ties-rahardjo", "email": "ties.rahardjo@beda.example", "name": "Ties Rahardjo", "role": "reviewer", "active": True},
    {"id": "zidane-mouldino", "email": "zidane.mouldino@beda.example", "name": "Zidane Mouldino", "role": "reviewer", "active": True},
    {"id": "ali-pratama", "email": "ali.pratama@beda.example", "name": "Ali Pratama", "role": "admin", "active": True},
    {"id": "default-reviewer", "email": "reviewer@beda.example", "name": "Operations Reviewer", "role": "reviewer", "active": True},
    {"id": "default-auditor", "email": "auditor@beda.example", "name": "Compliance Auditor", "role": "audit_reader", "active": True},
]

CRM_SEED = [
    {
        "customer_id": "C001",
        "company_name": "Hume Logistics Pty Ltd",
        "contact_name": "Amelia Grant",
        "email": "amelia.grant@humelogistics.example",
        "phone": "0400 111 020",
        "location": "Melbourne VIC",
        "relationship_type": "Prospect",
        "interest_product": "Commercial Solar",
        "status": "Open",
    },
    {
        "customer_id": "C002",
        "company_name": "Hume Logistic",
        "contact_name": "Amelia Grant",
        "email": "a.grant@humelogistics.example",
        "phone": None,
        "location": "Melbourne VIC",
        "relationship_type": "Lead",
        "interest_product": "Solar",
        "status": "New",
    },
    {
        "customer_id": "C003",
        "company_name": "Greenfields Foods Pty Ltd",
        "contact_name": "Rohan Lee",
        "email": "rohan@greenfieldsfoods.example",
        "phone": "0400 222 310",
        "location": "Geelong VIC",
        "relationship_type": "Client",
        "interest_product": "Energy Efficiency",
        "status": "Active",
    },
    {
        "customer_id": "C004",
        "company_name": "Northbank College",
        "contact_name": "Melissa Tran",
        "email": "melissa.tran@northbankcollege.example",
        "phone": "0400 330 110",
        "location": "Sydney NSW",
        "relationship_type": "Prospect",
        "interest_product": "LED",
        "status": "Open",
    },
    {
        "customer_id": "C005",
        "company_name": "Solara Installations",
        "contact_name": "Daniel Wu",
        "email": "daniel@solarainstall.example",
        "phone": "0400 880 101",
        "location": "Sydney NSW",
        "relationship_type": "Partner",
        "interest_product": "Installation",
        "status": "Active",
    },
]

async def seed_data():
    logger.info("Starting database seed...")
    async with get_db_session() as session:
        # Seed users
        for u in STAFF_USERS:
            existing = await session.scalar(select(UserModel).where(UserModel.id == u["id"]))
            if not existing:
                session.add(UserModel(**u))
        
        # Seed CRM customers
        for c in CRM_SEED:
            existing = await session.scalar(select(CRMCustomerModel).where(CRMCustomerModel.customer_id == c["customer_id"]))
            if not existing:
                session.add(CRMCustomerModel(**c))
        
        await session.commit()
    logger.info("Database seeding completed successfully.")

if __name__ == "__main__":
    from db.migrations.runner import run_migrations
    asyncio.run(run_migrations())
    asyncio.run(seed_data())
