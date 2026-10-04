"""Usage per scope, aggregated across every attempt so retries never reset it.

jobs.usage holds numeric counters (model_calls, tool_calls, active_s, tokens, cost...).
A counter the provider did not report is recorded as unknown: it is never treated as 0.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .changes import NotFound
from .models import Job


def record_usage(session: Session, job_id: str, delta: dict[str, float | int | None]) -> dict[str, Any]:
    """Add counters to one job's usage. A None delta marks that counter unknown for this job.

    Runs inside the caller's write transaction, so concurrent updates cannot be lost.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise NotFound("jobs", job_id)
    usage = dict(job.usage or {})
    unknown = set(usage.get("_unknown", []))
    for key, value in delta.items():
        if key.startswith("_"):
            raise ValueError("usage keys cannot start with an underscore")
        if value is None:
            unknown.add(key)
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"usage {key} must be a non-negative number or None")
        else:
            usage[key] = usage.get(key, 0) + value
    usage["_unknown"] = sorted(unknown)
    job.usage = usage
    session.flush()
    return usage


def scope_usage(session: Session, ticket_id: str, scope_version: int) -> dict[str, Any]:
    """Totals over all jobs/attempts of one ticket scope, plus which counters had unknown parts."""
    totals: dict[str, float | int] = {}
    unknown: set[str] = set()
    jobs = session.scalars(select(Job).where(Job.ticket_id == ticket_id, Job.scope_version == scope_version)).all()
    for job in jobs:
        for key, value in (job.usage or {}).items():
            if key == "_unknown":
                unknown.update(value)
            else:
                totals[key] = totals.get(key, 0) + value
    return {"totals": totals, "unknown": sorted(unknown), "jobs": len(jobs)}
