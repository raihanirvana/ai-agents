"""State change + event in one transaction, guarded by the entity revision.

This is the persistence primitive behind "Setiap mutation membawa expected_revision":
domain commands (DEV-003) decide what may change; this makes the change atomic.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .events import EventSpec, append_event
from .models import Project
from .columns import utcnow


class PersistenceError(RuntimeError):
    pass


class NotFound(PersistenceError):
    def __init__(self, entity: str, entity_id: str):
        super().__init__(f"{entity} {entity_id} not found")
        self.entity, self.entity_id = entity, entity_id


class RevisionConflict(PersistenceError):
    """expected_revision no longer matches; nothing was written. Re-read and decide again."""

    def __init__(self, entity: str, entity_id: str, expected: int, actual: int):
        super().__init__(f"{entity} {entity_id} is at revision {actual}, expected {expected}")
        self.entity, self.entity_id, self.expected, self.actual = entity, entity_id, expected, actual


def apply_change(session: Session, model: type, entity_id: str, *, expected_revision: int,
                 values: dict[str, Any], event: EventSpec) -> Any:
    """Update a revisioned row and append its event; both land together or not at all.

    Raises RevisionConflict/NotFound before writing anything. The returned row is fresh.
    """
    if "revision" in values or "id" in values:
        raise ValueError("revision is managed by apply_change")
    table = model.__tablename__
    result = session.execute(
        update(model).where(model.id == entity_id, model.revision == expected_revision)
        .values(**values, revision=model.revision + 1, updated_at=utcnow())
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        actual = session.scalar(select(model.revision).where(model.id == entity_id))
        if actual is None:
            raise NotFound(table, entity_id)
        raise RevisionConflict(table, entity_id, expected_revision, actual)
    row = session.get(model, entity_id, populate_existing=True)
    project_id = row.id if model is Project else row.project_id
    append_event(session, project_id, EventSpec(
        type=event.type, actor=event.actor, run_id=event.run_id,
        entity_type=event.entity_type or table, entity_id=event.entity_id or entity_id,
        payload={**event.payload, "revision": row.revision}))
    return row
