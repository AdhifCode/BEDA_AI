import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.schema import AuditEventModel
from packages.domain.models import AuditEvent
from packages.observability.logger import get_logger

logger = get_logger("audit")


def format_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AuditService:
    @staticmethod
    def calculate_event_hash(
        previous_event_hash: Optional[str],
        enquiry_id: str,
        event_type: str,
        actor_type: str,
        actor_id: str,
        timestamp_iso: str,
        outcome: str,
        metadata_json: Dict[str, Any],
    ) -> str:
        serialized_meta = json.dumps(metadata_json, sort_keys=True, default=str)
        chain_parent = previous_event_hash if previous_event_hash else "GENESIS"
        payload = f"{chain_parent}|{enquiry_id}|{event_type}|{actor_type}|{actor_id}|{timestamp_iso}|{outcome}|{serialized_meta}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    async def record_event(
        cls,
        session: AsyncSession,
        enquiry_id: str,
        event_type: str,
        actor_type: str,
        actor_id: str,
        outcome: str,
        metadata_json: Optional[Dict[str, Any]] = None,
        model_provider: Optional[str] = None,
        model_version: Optional[str] = None,
        input_reference: Optional[str] = None,
        decision_reference: Optional[str] = None,
    ) -> AuditEvent:
        if metadata_json is None:
            metadata_json = {}

        # Fetch last audit event for hash chaining
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.enquiry_id == enquiry_id)
            .order_by(AuditEventModel.timestamp.desc())
        )
        events = (await session.execute(stmt)).scalars().all()
        previous_hash = events[0].event_hash if events else None

        now = datetime.now(timezone.utc)
        now_iso = format_iso(now)

        event_hash = cls.calculate_event_hash(
            previous_event_hash=previous_hash,
            enquiry_id=enquiry_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            timestamp_iso=now_iso,
            outcome=outcome,
            metadata_json=metadata_json,
        )

        db_event = AuditEventModel(
            enquiry_id=enquiry_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            timestamp=now,
            model_provider=model_provider,
            model_version=model_version,
            input_reference=input_reference,
            decision_reference=decision_reference,
            outcome=outcome,
            metadata_json=metadata_json,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
        )
        session.add(db_event)
        await session.flush()

        return AuditEvent(
            id=db_event.id,
            enquiry_id=enquiry_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            timestamp=now,
            model_provider=model_provider,
            model_version=model_version,
            input_reference=input_reference,
            decision_reference=decision_reference,
            outcome=outcome,
            metadata_json=metadata_json,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
        )

    @classmethod
    async def verify_chain(cls, session: AsyncSession, enquiry_id: str) -> bool:
        stmt = (
            select(AuditEventModel)
            .where(AuditEventModel.enquiry_id == enquiry_id)
        )
        all_events = (await session.execute(stmt)).scalars().all()
        if not all_events:
            return True

        # Build map by previous_event_hash
        by_prev: Dict[Optional[str], AuditEventModel] = {}
        for ev in all_events:
            by_prev[ev.previous_event_hash] = ev

        curr = by_prev.get(None)
        if not curr:
            logger.error("No genesis audit event found!")
            return False

        visited_count = 0
        while curr:
            visited_count += 1
            now_iso = format_iso(curr.timestamp)
            calc_hash = cls.calculate_event_hash(
                previous_event_hash=curr.previous_event_hash,
                enquiry_id=curr.enquiry_id,
                event_type=curr.event_type,
                actor_type=curr.actor_type,
                actor_id=curr.actor_id,
                timestamp_iso=now_iso,
                outcome=curr.outcome,
                metadata_json=curr.metadata_json,
            )
            if calc_hash != curr.event_hash:
                logger.error(f"Audit hash mismatch at {curr.id}: expected {calc_hash} got {curr.event_hash}")
                return False
            curr = by_prev.get(curr.event_hash)

        return visited_count == len(all_events)
