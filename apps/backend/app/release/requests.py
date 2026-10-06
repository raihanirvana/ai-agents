"""Short authenticated transactions that record release intents as jobs. No Git or target execution in HTTP."""
from sqlalchemy import select

from app.domain import Conflict, Invalid
from app.persistence import EventSpec, NotFound, RevisionConflict, append_event
from app.persistence.models import Artifact, Job, Project, Release

ACTIVE = ("queued", "running", "waiting_input", "waiting_quota")
LIMITS = {"model_calls": 1, "tool_calls": 96, "active_s": 3600}


def active_release_job(s, project_id):
    return s.scalar(select(Job.id).where(Job.project_id == project_id, Job.stage == "release", Job.status.in_(ACTIVE)))


def public(r: Release, s=None) -> dict:
    """Public release view. `deployed` is never inferred: deployment has its own result and status."""
    target = s.get(Artifact, r.target_artifact_id) if s is not None else None
    return {"id": r.id, "project_id": r.project_id, "status": r.status, "accepted_tip": r.accepted_tip,
            "technical_review_evidence_ids": target.meta.get("technical_review_evidence_ids", []) if target else [],
            "scope": r.scope_snapshot, "checklist": sorted({k for e in r.scope_snapshot for k in e.get("checklist", [])}),
            "target_artifact_id": r.target_artifact_id, "target_digest": r.target_digest,
            "build_artifact_id": r.build_artifact_id, "evidence_ids": r.evidence_artifact_ids,
            "export": r.export_result, "deployment": r.deployment_result, "deployed": r.status == "deployed",
            "revision": r.revision, "created_at": r.created_at}


def _enqueue(s, services, actor, key, payload, kind):
    if active_release_job(s, actor.project_id):
        raise Conflict("a release operation is already running for this project")
    from app.pipeline.budgets import project_limits
    project = services.workflow._row(s, Project, actor.project_id, actor)
    return services.queue.enqueue(session=s, project_id=actor.project_id, ticket_id=None, lane="execution",
        role="technical-lead", stage="release", runtime="release", idempotency_key=f"release-{kind}:{key}",
        limits=project_limits(project, LIMITS), actor=actor.id, payload={"task": kind, **payload})


def request_freeze(s, services, actor, expected_revision, key):
    """Freeze the accepted tip and the accepted tickets not yet released. While the job is active the integrator does
    not move the tip: tickets accepted meanwhile are recorded and join the next release."""
    project = services.workflow._row(s, Project, actor.project_id, actor)
    if project.revision != expected_revision:
        raise RevisionConflict("projects", project.id, expected_revision, project.revision)
    manifest = (project.workflow.get("pipeline") or {}).get("manifest")
    if not manifest:
        raise Invalid("project has no runner configuration; onboard or configure the project first")
    tip, entries = services.workflow.freeze_release_scope(actor)
    _, regression = services.workflow.freeze_release_scope(actor, include_released=True)
    job = _enqueue(s, services, actor, key, {"accepted_tip": tip, "scope": entries,
        "regression_scope": regression, "manifest": manifest}, "verify")
    append_event(s, project.id, EventSpec("release.freeze_requested", actor.id, {"job_id": job.id, "accepted_tip": tip,
        "tickets": [e["ticket_id"] for e in entries]}, entity_type="jobs", entity_id=job.id))
    return job, tip, entries


def _release(s, services, actor, release_id, expected_revision):
    r = services.workflow._row(s, Release, release_id, actor)
    if r.revision != expected_revision:
        raise RevisionConflict("releases", r.id, expected_revision, r.revision)
    return r


def request_export(s, services, actor, release_id, expected_revision, key):
    services.workflow._permit(s, actor, "user")
    r = _release(s, services, actor, release_id, expected_revision)
    if r.status != "approved":
        raise Conflict("only an approved release can be exported")
    return _enqueue(s, services, actor, key, {"release_id": r.id}, "export")


def request_sync(s, services, actor, release_id, expected_revision, key):
    services.workflow._permit(s, actor, "user")
    r = _release(s, services, actor, release_id, expected_revision)
    project = services.workflow._row(s, Project, actor.project_id, actor)
    if r.status not in ("draft", "approved"):
        raise Conflict("only a draft or approved release that was not exported can be synchronised")
    if project.mode != "existing" or not project.repo_ref:
        raise Invalid("synchronisation applies to projects onboarded from an existing repository")
    manifest = (project.workflow.get("pipeline") or {}).get("manifest")
    if not manifest:
        raise Invalid("project has no runner configuration")
    return _enqueue(s, services, actor, key, {"release_id": r.id, "manifest": manifest}, "sync")


def releases(s, project_id):
    return list(s.scalars(select(Release).where(Release.project_id == project_id)
                          .order_by(Release.created_at.desc(), Release.id.desc()).limit(20)))


def get(s, release_id, project_id=None):
    r = s.get(Release, release_id)
    if r is None or (project_id is not None and r.project_id != project_id):
        raise NotFound("release", release_id)
    return r
