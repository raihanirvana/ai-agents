"""Preview requests: pure database logic, safe for the API process (no Docker, no filesystem).

A request pins one verified target. The supervisor (service.py) executes it. Switching previews, stopping, and the
single-active rule are decided here, in one transaction, so two commands cannot both claim the single local preview.
Approval never reads these rows: UAT refers to the candidate, target digest and evidence the user saw.
"""
from __future__ import annotations

from sqlalchemy import select

from app.domain import evidence
from app.domain.types import Conflict, Invalid
from app.persistence import ArtifactUnavailable, EventSpec, NotFound, append_event
from app.persistence.models import (ACTIVE_PREVIEW_STATUSES, Artifact, Candidate, Preview, Project, Ticket)

LIVE = ("requested", "starting", "ready")  # statuses a second request for the same candidate may reuse
# Only the minimum stack (static React/Vite, stateless fixture, no migrations) can be previewed (DEV-010 limits).
SUPPORTED_MIGRATIONS = ("none",)


def url(port: int) -> str:
    return f"http://localhost:{port}/"


def public(p: Preview) -> dict:
    return {"id": p.id, "project_id": p.project_id, "ticket_id": p.ticket_id, "candidate_id": p.candidate_id,
            "scope_version": p.scope_version, "target_artifact_id": p.target_artifact_id,
            "target_digest": p.target_digest, "status": p.status, "revision": p.revision,
            "url": url(p.port) if p.status == "ready" and p.port else None, "port": p.port,
            "stop_reason": p.stop_reason, "error": p.error, "details": p.details,
            "requested_at": p.created_at, "ready_at": p.ready_at, "stopped_at": p.stopped_at}


def active(s, project_id: str | None = None) -> list[Preview]:
    query = select(Preview).where(Preview.status.in_(ACTIVE_PREVIEW_STATUSES)).order_by(Preview.created_at)
    if project_id:
        query = query.where(Preview.project_id == project_id)
    return list(s.scalars(query))


def latest_for(s, candidate_id: str) -> Preview | None:
    return s.scalar(select(Preview).where(Preview.candidate_id == candidate_id)
                    .order_by(Preview.created_at.desc(), Preview.id.desc()))


def _event(s, p: Preview, kind: str, actor: str, **payload) -> None:
    append_event(s, p.project_id, EventSpec("preview." + kind, actor,
        {"candidate_id": p.candidate_id, "target_digest": p.target_digest, "status": p.status, **payload},
        entity_type="previews", entity_id=p.id))


def eligible_target(s, store, ticket_id: str, candidate_id: str) -> tuple[Ticket, Candidate, dict, dict]:
    """Everything that must hold before a target may be shown for UAT; raises Conflict/Invalid/ArtifactUnavailable."""
    t = s.get(Ticket, ticket_id)
    if t is None:
        raise NotFound("ticket", ticket_id)
    c = s.get(Candidate, candidate_id)
    if c is None or c.ticket_id != t.id:
        raise NotFound("candidate", candidate_id)
    if t.phase != "uat" or t.workflow.get("candidate_id") != c.id or c.status != "verified" \
            or c.scope_version != t.current_version:
        raise Conflict("only the current verified candidate of a ticket in UAT can be previewed")
    if s.get(Project, t.project_id).workflow.get("accepted_tip") != c.base_sha:
        raise Conflict("accepted base changed; a new candidate, QA and UAT are required")
    verification_id = (c.preview or {}).get("verification_id")
    if not verification_id:
        raise Conflict("candidate has no verification that opened UAT")
    evidence.verification(s, store, c, verification_id)  # passed, non-fake, executed, covered, same target
    target = evidence.document(s, store, c.target_artifact_id)
    build = evidence.document(s, store, c.build_artifact_id)
    bundle_id = build.get("bundle_artifact_id")
    if not isinstance(bundle_id, str):
        raise Invalid("build record has no stored build bundle")
    # Everything the preview serves, and the evidence shown next to it, must still be there. The flag alone is not
    # enough: reading re-hashes the stored file, which also flips a corrupted artifact to unavailable.
    required = [c.commit_artifact_id, c.build_artifact_id, c.target_artifact_id, bundle_id, *c.evidence_artifact_ids]
    for artifact_id in dict.fromkeys(required):
        row = s.get(Artifact, artifact_id)
        if row is None:
            raise NotFound("artifact", artifact_id)
        if row.storage == "file":
            store.read_bytes(s, artifact_id)  # raises ArtifactUnavailable for missing/corrupt files
        elif row.availability != "available":
            raise ArtifactUnavailable(artifact_id, row.unavailable_reason or "unavailable")
    manifest = target.get("execution_manifest") or {}
    migrations = (manifest.get("migrations") or {}).get("id")
    if migrations not in SUPPORTED_MIGRATIONS:
        raise Invalid(f"preview supports only stateless targets without migrations (this one declares {migrations!r})")
    return t, c, target, {"bundle_artifact_id": bundle_id, "verification_id": verification_id}


def request_preview(s, store, *, ticket_id: str, candidate_id: str, user_id: str, port: int) -> Preview:
    t, c, target, extra = eligible_target(s, store, ticket_id, candidate_id)
    same = [p for p in active(s) if p.candidate_id == c.id and p.target_digest == c.target_digest and p.status in LIVE]
    if same:
        return same[0]  # reopening a preview that is already requested/starting/ready changes nothing
    for other in active(s):  # one local preview: the earlier one is switched out first
        if other.status == "requested":
            other.status, other.stop_reason = "stopped", "switched"
            _event(s, other, "stopped", user_id, reason="switched")
        elif other.status != "stopping":
            other.status, other.stop_reason = "stopping", "switched"
            _event(s, other, "stopping", user_id, reason="switched")
    manifest = target.get("execution_manifest") or {}
    details = {k: target.get(k) for k in ("build_digest", "config_digest", "toolchain_digest", "fixture_digest",
                                           "migration_digest", "runner_manifest_digest", "node_image_id", "source_sha", "base_sha")}
    details.update(fixture=manifest.get("fixture"), migrations=manifest.get("migrations"), **extra,
                   build_artifact_id=c.build_artifact_id, evidence_ids=list(c.evidence_artifact_ids))
    p = Preview(project_id=c.project_id, ticket_id=t.id, candidate_id=c.id, scope_version=c.scope_version,
                target_artifact_id=c.target_artifact_id, target_digest=c.target_digest, status="requested", port=port,
                requested_by=user_id, details=details)
    s.add(p)
    s.flush()
    _event(s, p, "requested", user_id)
    return p


def request_stop(s, preview_id: str, user_id: str, reason: str = "user_stop") -> Preview:
    p = s.get(Preview, preview_id)
    if p is None:
        raise NotFound("preview", preview_id)
    if p.status == "requested":
        p.status, p.stop_reason = "stopped", reason
        _event(s, p, "stopped", user_id, reason=reason)
    elif p.status in ("starting", "ready"):
        p.status, p.stop_reason = "stopping", reason
        _event(s, p, "stopping", user_id, reason=reason)
    return p  # already stopping/stopped/failed: idempotent
