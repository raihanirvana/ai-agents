"""Append-only event log. The cursor is the SSE/replay position (DEV-008 reads it).

append_event only adds a row to the caller's session, so an event commits or rolls back
together with the state change that caused it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Event


@dataclass(frozen=True)
class EventSpec:
    type: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None


def append_event(session: Session, project_id: str, spec: EventSpec) -> Event:
    event = Event(project_id=project_id, type=spec.type, actor=spec.actor, run_id=spec.run_id,
                  entity_type=spec.entity_type, entity_id=spec.entity_id, payload=dict(spec.payload))
    session.add(event)
    session.flush()  # assigns the cursor inside the caller's transaction
    return event


def read_events(session: Session, *, after: int = 0, limit: int = 100, project_id: str | None = None) -> list[Event]:
    """Events with cursor > after, oldest first. Resume with the last cursor you saw."""
    if limit < 1:
        raise ValueError("limit must be positive")
    query = select(Event).where(Event.cursor > after).order_by(Event.cursor).limit(limit)
    if project_id is not None:
        query = query.where(Event.project_id == project_id)
    return list(session.scalars(query))


def latest_cursor(session: Session, *, project_id: str | None = None) -> int:
    query = select(func.max(Event.cursor))
    if project_id is not None:
        query = query.where(Event.project_id == project_id)
    return session.scalar(query) or 0
