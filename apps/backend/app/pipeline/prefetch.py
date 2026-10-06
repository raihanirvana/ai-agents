"""Optional reference tarball preparation; never executes project code."""
import json
import time
from pathlib import Path

from app.persistence.models import Project
from app.workers.runtime import Outcome
from app.workspace.dependencies import fetch_tarballs
from app.workspace.errors import WorkspaceError
from app.workspace.runspec import ResourceLimits
from .bootstrap import reference_catalog
from .budgets import project_limits
from .contracts import digest_of
from .files import allocate_directory, cleanup_directory


def enqueue_prefetch(session, queue, project):
    _, lock = reference_catalog()
    return queue.enqueue(session=session, project_id=project.id, ticket_id=None,
        lane='execution', role='technical-lead', stage='reference_prefetch', runtime='reference-prefetch',
        idempotency_key='reference-prefetch:' + digest_of(lock), actor='service:reference-prefetch',
        limits=project_limits(project, {'model_calls': 1, 'tool_calls': 4, 'active_s': 600}),
        payload={'catalog_digest': digest_of(lock)})


class ReferencePrefetch:
    name = 'reference-prefetch'

    def __init__(self, db, store, root, redactor):
        self.db, self.store, self.root, self.redactor = db, store, Path(root), redactor

    def stop(self, ctx):
        ctx.cancelled.set()
        ctx.stop_resources()

    def run(self, ctx):
        identity = ctx.queue.verify(ctx.lease)
        if (identity['ticket_id'] is not None or identity['role'] != 'technical-lead' or
                identity['stage'] != 'reference_prefetch'):
            return Outcome('failed', error='invalid reference-prefetch identity')
        with self.db.read() as s:
            project = s.get(Project, identity['project_id'])
            if project.mode != 'new' or not project.workflow.get('onboarding_detail', {}).get('reference_runner'):
                return Outcome('failed', error='prefetch requires an initialized reference new project')
        _, lock = reference_catalog()
        if digest_of(lock) != ctx.job['runtime_ref']['payload']['catalog_digest']:
            return Outcome('succeeded', {'cache_status': 'catalog_changed'})
        finalizers = []
        try:
            directory = allocate_directory(ctx, self.root / '.prefetch', 'prefetch',
                store=self.store, redactor=self.redactor, finalizers=finalizers)
            source, tarballs = directory / 'source', directory / 'tarballs'
            source.mkdir(); tarballs.mkdir()
            (source / 'package-lock.json').write_text(json.dumps(lock))
            def download():
                return fetch_tarballs(source, tarballs, ResourceLimits(), deadline=time.monotonic() + 300,
                    is_cancelled=lambda: ctx.cancelled.is_set(), cache_root=self.root / '.dependency-cache',
                    progress=lambda state: ctx.log('dependency.prefetch ' + json.dumps(state)))
            stats = ctx.tool_call('prefetch_reference_tarballs', download)
            return Outcome('succeeded', {'cache_status': 'ready', **stats})
        except (OSError, ValueError, WorkspaceError) as exc:
            error = self.redactor.redact(str(exc))[:400]
            ctx.queue.verify(ctx.lease)
            ctx.log('dependency.prefetch unavailable: ' + error)
            # Cache warming is optional. Actual install still verifies/fetches
            # required dependencies and produces authoritative command evidence.
            return Outcome('succeeded', {'cache_status': 'unavailable', 'error': error})
        finally:
            for finish in reversed(finalizers):
                finish()

    def reconcile(self, snapshot):
        resources = snapshot['runtime_ref'].get('resources', [])
        generation = snapshot['runtime_ref']['cleanup']['generation']
        for resource in resources:
            if (resource.get('kind') != 'pipeline_directory' or resource.get('purpose') != 'prefetch' or
                    resource.get('job_id') != snapshot['id'] or resource.get('project_id') != snapshot['project_id'] or
                    resource.get('generation') != generation or resource.get('owner') != snapshot['id'] + ':' + str(generation)):
                return False
            cleanup_directory(self.root / '.prefetch', resource, db=self.db, store=self.store, redactor=self.redactor)
        return True
