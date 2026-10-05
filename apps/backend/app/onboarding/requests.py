"""Short authenticated transaction; no Git or target execution in the HTTP process."""
from sqlalchemy import select
from app.domain import Conflict, Invalid
from app.persistence import apply_change, EventSpec, append_message
from app.persistence.models import Project, Job
from app.workspace.manifest import parse_manifest
from app.workspace.errors import ManifestError


def request(s, services, store, actor, expected_revision, manifest, key, *, patch=None, source_sha=None):
    services.workflow._permit(s, actor, 'user')
    p = services.workflow._row(s, Project, actor.project_id, actor)
    if p.mode != 'existing' or p.workflow.get('accepted_tip'):
        raise Conflict('onboarding requires an uninitialized existing project')
    if s.scalar(select(Job.id).where(Job.project_id == p.id, Job.stage == 'onboarding',
                                    Job.status.in_(('queued', 'running', 'waiting_input', 'waiting_quota')))):
        raise Conflict('onboarding is already active')
    try:
        parsed = parse_manifest(manifest)
    except ManifestError as exc:
        raise Invalid(str(exc)) from exc
    if patch is not None and (not patch.strip() or not source_sha):
        raise Invalid('explicit patch requires nonempty content and the source HEAD SHA')
    if source_sha is not None:
        import re
        if not re.fullmatch('[0-9a-f]{40}', source_sha):
            raise Invalid('source SHA must be a full Git commit ID')
    patch_id = None
    if patch is not None:
        a = store.put_bytes(s, project_id=p.id, kind='other', name='explicit-source.patch', data=patch.encode(),
                            meta={'producer': 'user', 'source_sha': source_sha, 'explicit_patch': True})
        patch_id = a.id
    job = services.queue.enqueue(session=s, project_id=p.id, ticket_id=None, lane='execution', role='technical-lead',
        stage='onboarding', runtime='onboarding', idempotency_key='onboarding:' + key,
        limits={'model_calls': 1, 'tool_calls': 32, 'active_s': 1800}, actor=actor.id,
        payload={'manifest': parsed.to_dict(), 'source_sha': source_sha, 'patch_artifact_id': patch_id})
    p = apply_change(s, Project, p.id, expected_revision=expected_revision,
        values={'workflow': {**p.workflow, 'onboarding': 'queued', 'onboarding_detail': {'job_id': job.id}}},
        event=EventSpec('project.onboarding_requested', actor.id, {'job_id': job.id, 'patch_artifact_id': patch_id}))
    if patch_id:
        append_message(s, project_id=p.id, thread_id='onboarding:' + p.id, sender=actor.id, recipient='technical-lead',
            kind='message', body='Explicit source patch selected for onboarding.', attachment_ids=[patch_id])
    return p, job
