"""Cleanup of unpinned artifact files. Never touches anything a product row still needs.

Ownership checks: only the given project's artifacts, only files whose stored path is
under that project, and only after min_age so an artifact written in one transaction and
referenced in the next is not collected in between. Database first, files second: a crash
between them leaves a harmless leftover file, never an "available" artifact without bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from .artifacts import ArtifactStore
from .columns import utcnow
from .db import Database
from .events import EventSpec, append_event
from .models import Artifact
from .pins import pinned_artifacts


@dataclass
class CleanupReport:
    removed: list[str] = field(default_factory=list)
    kept_pinned: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    dry_run: bool = True


def cleanup_unpinned(db: Database, store: ArtifactStore, *, project_id: str,
                     min_age: timedelta = timedelta(hours=1), kinds: tuple[str, ...] | None = None,
                     dry_run: bool = True, now: datetime | None = None,
                     actor: str = "system:cleanup") -> CleanupReport:
    now = now or utcnow()
    report = CleanupReport(dry_run=dry_run)
    paths = []

    def plan(session) -> None:
        pins = pinned_artifacts(session)
        query = select(Artifact).where(Artifact.project_id == project_id, Artifact.storage == "file",
                                       Artifact.availability == "available").order_by(Artifact.created_at)
        if kinds is not None:
            query = query.where(Artifact.kind.in_(kinds))
        for artifact in session.scalars(query):
            if artifact.id in pins:
                report.kept_pinned.append(artifact.id)
            elif artifact.created_at > now - min_age:
                report.skipped[artifact.id] = "too_recent"
            elif not (artifact.path or "").startswith(f"{project_id}/"):
                report.skipped[artifact.id] = "ownership_mismatch"
            else:
                report.removed.append(artifact.id)
                if not dry_run:
                    artifact.availability, artifact.unavailable_reason = "unavailable", "cleaned"
                    paths.append(store.resolve(artifact.path))
                    append_event(session, project_id, EventSpec(
                        "artifact.cleaned", actor, {"kind": artifact.kind, "checksum": artifact.checksum},
                        entity_type="artifact", entity_id=artifact.id))

    if dry_run:
        with db.read() as session:
            plan(session)
        return report
    with db.write() as session:  # pins cannot change while we hold the write lock
        plan(session)
    for path in paths:
        store.delete_path(path)
    return report
