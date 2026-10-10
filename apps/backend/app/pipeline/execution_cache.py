"""Immutable baseline artifacts, keyed by execution inputs rather than a job ID."""
import json
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, select

from app.persistence.artifacts import ArtifactError
from app.persistence.models import Artifact
from app.workspace.errors import WorkspaceError
from app.workspace.runspec import ResourceLimits
from .contracts import digest_of, validate_report


def execution_policy():
    root = Path(__file__).resolve().parents[1]
    files = ('workspace/sandbox.py', 'workspace/bounded_io.py', 'workspace/installation_cache.py',
             'workspace/dependencies.py', 'workspace/manifest.py', 'pipeline/gates.py',
             'pipeline/execution_cache.py', 'pipeline/workspace.py')
    import hashlib
    return {'schema': 1, 'code': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in files}, 'limits': asdict(ResourceLimits())}


class ExecutionCache:
    def __init__(self, workspace, *, enabled=True):
        self.workspace, self.enabled = workspace, enabled

    def read(self, ctx, kind, key):
        if not self.enabled:
            return None
        identity = ctx.queue.verify(ctx.lease)
        with self.workspace.db.read() as s:
            rows = s.scalars(select(Artifact).where(Artifact.project_id == identity['project_id'],
                Artifact.kind == 'report', Artifact.availability == 'available',
                func.json_extract(Artifact.meta, '$.producer') == 'execution-cache',
                func.json_extract(Artifact.meta, '$.cache_kind') == kind,
                func.json_extract(Artifact.meta, '$.cache_key') == key)
                .order_by(Artifact.created_at.desc(), Artifact.id).limit(3))
            for row in rows:
                try:
                    document = json.loads(self.workspace.store.read_bytes(s, row.id))
                    if document['key'] == key and document['kind'] == kind:
                        ctx.log('execution.cache ' + json.dumps({'kind': kind, 'hit': True, 'key': key,
                            'artifact_id': row.id, 'origin': document['origin']}))
                        return row.id, document
                except (ArtifactError, ValueError, KeyError, TypeError, AttributeError, WorkspaceError):
                    continue  # Missing/corrupt evidence is a miss, never a waiver.
        ctx.log('execution.cache ' + json.dumps({'kind': kind, 'hit': False, 'key': key}))
        return None

    def write(self, ctx, kind, key, value):
        if not self.enabled:
            return None
        identity = ctx.queue.verify(ctx.lease)
        with self.workspace.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            document = {'key': key, 'kind': kind, 'value': value,
                        'origin': {'job_id': identity['job_id'], 'generation': identity['generation']}}
            row = self.workspace.store.put_json(s, project_id=identity['project_id'], kind='report',
                name=kind + '-cache.json', document=document,
                meta={'producer': 'execution-cache', 'cache_kind': kind, 'cache_key': key})
            self.workspace._post(s, identity, 'execution-cache:' + kind + ':' + row.id,
                'Stored immutable ' + kind + ' execution for identical inputs.', [row.id],
                'execution_cache', cache_kind=kind, cache_key=key, cache_hit=False)
            return row.id

    def baseline_browser(self, ctx, site, target_digest, suite, node_image, runner, *, fake=False):
        started = time.monotonic()
        key = digest_of({'target_digest': target_digest, 'suite_digest': suite.digest,
                         'node_image': node_image, 'runner': runner, 'fake': fake,
                         'policy': execution_policy(), 'schema': 1})
        saved = self.read(ctx, 'baseline-browser', key)
        if saved:
            artifact_id, document = saved
            try:
                proof = document['value']
                checked = validate_report(proof.get('report'), invocation_id=proof.get('invocation_id'),
                                          target_digest=target_digest, suite=suite)
                if (checked['status'] == proof.get('status') and checked['counts'] == proof.get('counts')
                        and proof.get('runner') == runner and proof.get('infrastructure_failure') is False
                        and proof.get('report', {}).get('smoke_passed') is True
                        and isinstance(proof.get('invocation_id'), str)
                        and (checked['status'] != 'passed' or proof.get('exit_code') == 0)
                        and checked['status'] in ('passed', 'failed')):
                    ctx.queue.verify(ctx.lease)
                    from app.workers.telemetry import record_phase
                    record_phase(ctx, 'baseline_browser_restore', time.monotonic()-started, cache_hit=True)
                    return {**proof, 'cache_hit': True, 'cache_artifact_id': artifact_id,
                            'cache_origin': document['origin']}
            except (KeyError, TypeError, AttributeError, ValueError):
                pass
            ctx.log('execution.cache baseline-browser invalid/unavailable; executing')
        proof = self.workspace.harness.run(ctx, site, target_digest, suite, node_image,
                                          expected_runner=runner, diagnostics=False)
        proof.pop('diagnostics', None)
        if not fake and proof['status'] in ('passed', 'failed') and proof['infrastructure_failure'] is False:
            artifact_id = self.write(ctx, 'baseline-browser', key, proof)
            if artifact_id:
                proof['cache_artifact_id'] = artifact_id
        return {**proof, 'cache_hit': False}
