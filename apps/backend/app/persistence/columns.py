"""Column helpers shared by the persistence models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime
from sqlalchemy.types import TypeDecorator

# NULL stays SQL NULL (not the JSON text "null") so CHECKs on optional JSON work.
Json = JSON(none_as_null=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class UtcDateTime(TypeDecorator):
    """Timezone-aware UTC in Python, naive UTC text in SQLite. Naive input is a bug."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timezone-aware datetime required")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)
