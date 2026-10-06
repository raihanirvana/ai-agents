"""Durable reference runner setup for new projects; no model or target execution."""
from pathlib import Path

from sqlalchemy import select, func

from app.persistence import apply_change, EventSpec, append_message
from app.persistence.models import Project, Job
from app.persistence.transactions import bind_service
from app.workers.runtime import Outcome
from app.workspace import WorkspaceSupervisor
from app.workspace.manifest import parse_manifest
from app.workspace.errors import WorkspaceError


def reference_manifest():
    return parse_manifest({
        'revision': 1, 'runner': 'react-vite', 'image': 'node:22.20.0-alpine',
        'toolchain': {'node': '22.20.0', 'npm': '10.9.3'},
        'commands': {
            'install': {'argv': ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                        'network': 'egress', 'timeout_s': 300},
            'build': {'argv': ['npm', 'run', 'build'], 'network': 'none', 'timeout_s': 300},
            'test': {'argv': ['npm', 'test'], 'network': 'none', 'timeout_s': 300},
            'start': {'argv': ['npx', 'vite', 'preview', '--host', '0.0.0.0', '--port', '4173',
                               '--strictPort'], 'network': 'none', 'timeout_s': 300}},
        'port': 4173, 'health_path': '/', 'fixture': {'id': 'new-project-empty-v1'},
        'migrations': {'id': 'none'}, 'env': {'CI': '1'},
        'exclude_from_sync': ['node_modules', 'dist'], 'build_output': 'dist',
        'dependency_files': ['package-lock.json']})


def enqueue_setup(s, queue, project):
    if project.mode != 'new' or (project.workflow.get('accepted_tip') and project.workflow.get('pipeline')):
        return None
    # A permanent failure remains visible; do not bypass it with another job/key.
    existing = s.scalar(select(Job).where(Job.project_id == project.id, Job.stage == 'project_setup'))
    if existing is not None:
        return existing
    from .budgets import project_limits
    job = queue.enqueue(session=s, project_id=project.id, ticket_id=None, lane='execution',
        role='technical-lead', stage='project_setup', runtime='project-setup',
        idempotency_key='project-setup:reference-v1', actor='service:project-setup',
        limits=project_limits(project, {'model_calls': 1, 'tool_calls': 8, 'active_s': 300}),
        payload={'manifest': reference_manifest().to_dict()})
    apply_change(s, Project, project.id, expected_revision=project.revision,
        values={'workflow': {**project.workflow, 'onboarding': 'queued',
                             'onboarding_detail': {'job_id': job.id}}},
        event=EventSpec('project.setup_requested', 'service:project-setup', {'job_id': job.id}))
    return job


class NewProjectSetup:
    name = 'project-setup'

    def __init__(self, db, queue, root, redactor):
        self.db, self.queue, self.root, self.redactor = db, queue, Path(root), redactor

    def tick(self):
        # Also recover rows created by older API processes. Existing repositories
        # and custom initialized runners never receive a reference configuration.
        with self.db.write() as s:
            projects = s.scalars(select(Project).where(Project.mode == 'new',
                (func.json_extract(Project.workflow, '$.accepted_tip').is_(None)) |
                (func.json_extract(Project.workflow, '$.pipeline').is_(None))))
            for project in projects:
                enqueue_setup(s, self.queue, project)

    def stop(self, ctx):
        ctx.cancelled.set()
        ctx.stop_resources()

    def run(self, ctx):
        identity = ctx.queue.verify(ctx.lease)
        if identity['ticket_id'] is not None or identity['role'] != 'technical-lead' or identity['stage'] != 'project_setup':
            return Outcome('failed', error='invalid new-project setup identity')
        manifest = parse_manifest(ctx.job['runtime_ref']['payload']['manifest'])
        try:
            with self.db.read() as s:
                project = s.get(Project, identity['project_id'])
                if project.mode != 'new':
                    raise ValueError('reference setup requires a new project')
                expected_base = project.workflow.get('accepted_tip')
                if expected_base and project.workflow.get('pipeline'):
                    return Outcome('succeeded', {'already_configured': True})
            sup = WorkspaceSupervisor(self.root)
            def initialize():
                ctx.queue.verify(ctx.lease)
                if ctx.cancelled.is_set():
                    raise ValueError('setup cancelled')
                broker = sup.broker(identity['project_id'])
                project_dir = self.root / identity['project_id']
                project_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                (project_dir / 'runs').mkdir(exist_ok=True, mode=0o700)
                if expected_base:
                    base = broker.accepted_sha()
                    if base != expected_base:
                        raise ValueError('DB/Git accepted base differs; setup cannot reset it')
                    return base
                return broker.init_project(resume=True)
            base = ctx.tool_call('initialize_empty_managed_base', initialize)
            with self.db.write() as s:
                ctx.queue.verify_identity(s, identity)
                project = s.get(Project, identity['project_id'])
                if project.mode != 'new' or project.workflow.get('accepted_tip') != expected_base:
                    raise ValueError('project base changed during setup')
                pipeline = project.workflow.get('pipeline') or {'manifest': manifest.to_dict()}
                apply_change(s, Project, project.id, expected_revision=project.revision,
                    values={'workflow': {**project.workflow, 'accepted_tip': base, 'pipeline': pipeline,
                        'onboarding': 'ready', 'onboarding_detail': {'job_id': identity['root_job_id'],
                            'baseline_sha': base, 'reference_runner': True}}},
                    event=EventSpec('project.setup_completed', 'service:project-setup',
                                    {'job_id': identity['job_id'], 'manifest_digest': manifest.digest}))
                append_message(s, project_id=project.id, thread_id='onboarding:' + project.id,
                    sender='service:project-setup', body='Reference React/Vite runner and managed Git base ready. '
                    'Implementation starts only after user scope approval.',
                    idempotency_key='project-setup-ready:' + identity['root_job_id'],
                    meta={'intent': 'project_setup', 'job_id': identity['job_id']})
                from .prefetch import enqueue_prefetch
                enqueue_prefetch(s, ctx.queue, project)
                result = {'accepted_tip': base, 'reference_runner': True,
                          'pipeline_completion': {'job_id': identity['job_id'], 'generation': identity['generation']}}
                bind_service(ctx.queue, s).complete(ctx.lease, result)
            return Outcome('succeeded', result)
        except (ValueError, OSError, WorkspaceError) as exc:
            error = self.redactor.redact(str(exc))[:500]
            with self.db.write() as s:
                ctx.queue.verify_identity(s, identity)
                project = s.get(Project, identity['project_id'])
                apply_change(s, Project, project.id, expected_revision=project.revision,
                    values={'workflow': {**project.workflow, 'onboarding': 'blocked',
                        'onboarding_detail': {'job_id': identity['root_job_id'], 'blocker': error}}},
                    event=EventSpec('project.setup_failed', 'service:project-setup', {'error': error}))
            return Outcome('failed', error=error)

    def reconcile(self, snapshot):
        # Git is persistent project state, not an attempt resource to delete.
        # Recovery retries the checked initial-base operation on the same repo.
        return not snapshot['runtime_ref'].get('resources')
