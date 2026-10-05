"""Retry an agent effect without rewriting its original attempt provenance."""
from sqlalchemy import select

from app.persistence import append_message
from app.persistence.models import Message


def append_effect(session, **payload):
    key = payload.get("idempotency_key")
    if key:
        existing = session.scalar(select(Message).where(
            Message.project_id == payload["project_id"], Message.idempotency_key == key))
        if existing:
            # All semantic fields still pass append_message's exact payload check.
            # Only the execution provenance is retained from the first successful write.
            meta = dict(payload.get("meta") or {})
            for field in ("job_id", "generation"):
                if field in existing.meta:
                    meta[field] = existing.meta[field]
            payload["meta"] = meta
    return append_message(session, **payload)
