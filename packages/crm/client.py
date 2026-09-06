import json
from typing import Any, Dict, List, Optional, Protocol
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import get_db_session
from db.models.schema import CRMCustomerModel
from packages.domain.models import CRMCandidate
from packages.observability.logger import get_logger

logger = get_logger("crm")


class CRMClient(Protocol):
    async def find_by_customer_id(self, customer_id: str) -> Optional[Dict[str, Any]]: ...
    async def find_by_email(self, email: str) -> List[Dict[str, Any]]: ...
    async def find_by_phone(self, phone: str) -> List[Dict[str, Any]]: ...
    async def find_by_company_domain(self, domain: str) -> List[Dict[str, Any]]: ...
    async def create_customer(self, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]: ...
    async def update_customer(self, customer_id: str, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]: ...
    async def create_task(self, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]: ...
    async def save_draft(self, customer_id: str, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]: ...


class MockCRMClient:
    """
    Mock CRM client connected to the database CRM seed records (C001-C005)
    with idempotency protection on all writes.
    """

    def __init__(self):
        self._executed_idempotency_keys: set[str] = set()

    async def find_by_customer_id(self, customer_id: str) -> Optional[Dict[str, Any]]:
        cleaned = customer_id.strip()
        async with get_db_session() as session:
            stmt = select(CRMCustomerModel).where(CRMCustomerModel.customer_id == cleaned)
            r = (await session.execute(stmt)).scalar_one_or_none()
            if r:
                return {
                    "customer_id": r.customer_id,
                    "company_name": r.company_name,
                    "contact_name": r.contact_name,
                    "email": r.email,
                    "phone": r.phone,
                    "location": r.location,
                    "relationship_type": r.relationship_type,
                    "interest_product": r.interest_product,
                    "status": r.status,
                }
            return None

    async def find_by_email(self, email: str) -> List[Dict[str, Any]]:
        cleaned = email.strip().lower()
        async with get_db_session() as session:
            stmt = select(CRMCustomerModel).where(CRMCustomerModel.email.ilike(cleaned))
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "customer_id": r.customer_id,
                    "company_name": r.company_name,
                    "contact_name": r.contact_name,
                    "email": r.email,
                    "phone": r.phone,
                    "location": r.location,
                    "relationship_type": r.relationship_type,
                    "interest_product": r.interest_product,
                    "status": r.status,
                }
                for r in rows
            ]

    async def find_by_phone(self, phone: str) -> List[Dict[str, Any]]:
        # Normalize phone representation for match
        cleaned = "".join(filter(str.isdigit, phone))
        async with get_db_session() as session:
            stmt = select(CRMCustomerModel).where(CRMCustomerModel.phone.isnot(None))
            rows = (await session.execute(stmt)).scalars().all()
            matched = []
            for r in rows:
                if r.phone:
                    r_cleaned = "".join(filter(str.isdigit, r.phone))
                    if r_cleaned and (r_cleaned == cleaned or r_cleaned in cleaned or cleaned in r_cleaned):
                        matched.append(
                            {
                                "customer_id": r.customer_id,
                                "company_name": r.company_name,
                                "contact_name": r.contact_name,
                                "email": r.email,
                                "phone": r.phone,
                                "location": r.location,
                                "relationship_type": r.relationship_type,
                                "interest_product": r.interest_product,
                                "status": r.status,
                            }
                        )
            return matched

    async def find_by_company_domain(self, domain_or_name: str) -> List[Dict[str, Any]]:
        cleaned = domain_or_name.strip().lower()
        async with get_db_session() as session:
            stmt = select(CRMCustomerModel)
            rows = (await session.execute(stmt)).scalars().all()
            matched = []
            for r in rows:
                c_name = r.company_name.lower()
                c_email = (r.email or "").lower()
                if cleaned in c_name or c_name in cleaned or (cleaned in c_email):
                    matched.append(
                        {
                            "customer_id": r.customer_id,
                            "company_name": r.company_name,
                            "contact_name": r.contact_name,
                            "email": r.email,
                            "phone": r.phone,
                            "location": r.location,
                            "relationship_type": r.relationship_type,
                            "interest_product": r.interest_product,
                            "status": r.status,
                        }
                    )
            return matched

    async def create_customer(self, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in self._executed_idempotency_keys:
            logger.info(f"Idempotent create_customer ignored duplicate key: {idempotency_key}")
            return {"status": "DUPLICATE_IGNORED", "idempotency_key": idempotency_key}

        self._executed_idempotency_keys.add(idempotency_key)
        cid = payload.get("customer_id") or f"C{abs(hash(idempotency_key)) % 900 + 100}"
        async with get_db_session() as session:
            new_cust = CRMCustomerModel(
                customer_id=cid,
                company_name=payload.get("company_name", "Unknown"),
                contact_name=payload.get("contact_name", "Unknown"),
                email=payload.get("email"),
                phone=payload.get("phone"),
                location=payload.get("location", "Melbourne VIC"),
                relationship_type=payload.get("relationship_type", "Lead"),
                interest_product=payload.get("interest_product", "General"),
                status=payload.get("status", "New"),
            )
            session.add(new_cust)
            await session.commit()
        return {"status": "CREATED", "customer_id": cid}

    async def update_customer(self, customer_id: str, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in self._executed_idempotency_keys:
            return {"status": "DUPLICATE_IGNORED", "idempotency_key": idempotency_key}
        self._executed_idempotency_keys.add(idempotency_key)
        async with get_db_session() as session:
            stmt = select(CRMCustomerModel).where(CRMCustomerModel.customer_id == customer_id)
            cust = (await session.execute(stmt)).scalar_one_or_none()
            if cust:
                for k, v in payload.items():
                    if hasattr(cust, k):
                        setattr(cust, k, v)
                await session.commit()
                return {"status": "UPDATED", "customer_id": customer_id}
        return {"status": "NOT_FOUND", "customer_id": customer_id}

    async def create_task(self, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in self._executed_idempotency_keys:
            return {"status": "DUPLICATE_IGNORED", "idempotency_key": idempotency_key}
        self._executed_idempotency_keys.add(idempotency_key)
        logger.info(f"CRM Task created: {payload.get('title')} assigned to {payload.get('assignee')}")
        return {"status": "TASK_CREATED", "idempotency_key": idempotency_key}

    async def save_draft(self, customer_id: str, payload: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in self._executed_idempotency_keys:
            return {"status": "DUPLICATE_IGNORED", "idempotency_key": idempotency_key}
        self._executed_idempotency_keys.add(idempotency_key)
        logger.info(f"CRM Draft saved for customer {customer_id}")
        return {"status": "DRAFT_SAVED", "customer_id": customer_id}


crm_client_instance = MockCRMClient()

def get_crm_client() -> MockCRMClient:
    return crm_client_instance
