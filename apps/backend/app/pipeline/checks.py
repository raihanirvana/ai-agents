"""One developer tool for fenced prerequisites, real tests and build.

Install receipts live outside target source. They only avoid a redundant local
installation; candidate submission still builds/tests an independent snapshot.
"""
import json
import hashlib
import time

from app.workspace import fsutil
from app.workspace.runspec import atomic_write_json
from .contracts import digest_of
from .gates import node_gate


def command_summary(phase, result):
    gate = node_gate(result.stdout, result.stderr, result.exit_code) if phase == 'test' else None
    clean = result.exit_code == 0 and not any((result.cancelled, result.timed_out,
                                             result.oom_killed, result.truncated))
    passed = clean and (gate is None or gate['status'] == 'passed')
    row = {'phase': phase, 'status': 'passed' if passed else 'failed',
           'exit_code': result.exit_code, 'duration_s': result.duration_s,
           'cache_hit': bool(result.cache_key),
           'infrastructure_failure': any((result.cancelled, result.timed_out, result.oom_killed, result.truncated))}
    if gate is not None:
        row['repository_gate'] = {k: gate.get(k) for k in ('status', 'counts', 'failed_test_ids')}
        row['repository_gate']['failed_test_ids'] = (gate.get('failed_test_ids') or [])[:30]
        if gate['status'] == 'incomplete':
            row['next'] = 'Run actual repository tests with complete counts; exit code 0 alone is not evidence.'
    if not passed:
        row['error_excerpt'] = (result.stderr.decode(errors='replace') + '\n' +
                                result.stdout.decode(errors='replace'))[:2000]
    return row


class DeveloperChecks:
    def __init__(self, ctx, sup, started, workspace):
        self.ctx, self.sup, self.started, self.workspace = ctx, sup, started, workspace

    def _inputs(self, manifest, source, limits):
        return digest_of({'manifest': manifest.to_dict(), 'image_id': self.sup.sandbox.image_id(manifest.image),
            'inputs': {p: hashlib.sha256(fsutil.read_file_beneath(source, p,
                       max_bytes=limits.max_snapshot_bytes)).hexdigest()
                       for p in ('package.json', 'package-lock.json')}})

    @staticmethod
    def _tree(source, limits):
        tree = source / 'node_modules'
        if not tree.exists() and not tree.is_symlink():
            return None
        entries = fsutil.scan_tree(tree, limits=fsutil.TreeLimits(limits.max_snapshot_files, limits.max_snapshot_bytes))
        return fsutil.sha256_tree(tree, entries)

    def _phase(self, phase):
        spec, manifest, store = self.sup.authorize(self.started.ref, self.started.credential, 'run_phase')
        cmd = manifest.commands[phase]
        if cmd.network == 'egress' and not spec.allow_install_egress:
            raise ValueError('this workspace is not authorized for dependency acquisition')
        return self.sup._execute(self.started.ref, spec, manifest, store,
            source=self.sup.src_dir(self.started.ref), argv=list(cmd.argv), network=cmd.network,
            timeout_s=cmd.timeout_s, label='phase-' + phase, secrets_to_redact=[self.started.credential])

    def run(self):
        decision_started = time.monotonic()
        store = self.sup._store(self.started.ref)
        rows = []
        # Do not nest serialized run_phase/read_file: flock is not reentrant.
        # Holding the operation lock for the whole batch prevents concurrent
        # edits from invalidating an install decision between the phases.
        with store.lock('operation.lock'):
            spec, manifest, _ = self.sup.authorize(self.started.ref, self.started.credential, 'run_phase')
            source = self.sup.src_dir(self.started.ref)
            receipt_path = store.dir / 'checks-install.json'
            try:
                inputs = self._inputs(manifest, source, spec.limits)
            except FileNotFoundError:
                return {'status': 'failed', 'phases': [], 'next':
                    'Create package.json and a matching generated lockfile. If reference_bootstrap is available, '
                    'call run_command phase bootstrap, then run_checks.'}
            from app.workspace.errors import WorkspaceError
            try:
                previous = json.loads(receipt_path.read_text())
                installed = (previous['inputs'] == inputs and
                             previous['tree_digest'] == self._tree(source, spec.limits))
            except (OSError, ValueError, KeyError, TypeError, WorkspaceError):
                installed = False
            if installed:
                duration = time.monotonic() - decision_started
                rows.append({'phase': 'install', 'status': 'reused', 'cache_hit': True,
                             'duration_s': duration, 'execution_kind': 'validated_local_reuse',
                             'reason': 'Exact package/lock/environment and installation tree unchanged.'})
                from app.workers.telemetry import record_phase
                record_phase(self.ctx, 'install', duration, cache_hit=True)
            else:
                receipt_path.unlink(missing_ok=True)
                result = self._phase('install')
                row = command_summary('install', result)
                rows.append(row)
                if row['status'] == 'passed':
                    # An install that changes package/lock cannot seed this receipt.
                    try:
                        if self._inputs(manifest, source, spec.limits) == inputs:
                            atomic_write_json(receipt_path, {'inputs': inputs,
                                'tree_digest': self._tree(source, spec.limits)})
                    except (OSError, WorkspaceError):
                        row['install_reuse_available'] = False
                        # An unsupported cache tree never prevents real checks.
            if rows[-1]['status'] in ('passed', 'reused'):
                for phase in ('test', 'build'):
                    row = command_summary(phase, self._phase(phase))
                    rows.append(row)
                    if row['status'] != 'passed':
                        break  # Do not spend time building after a failed/incomplete gate.
            report = {'status': 'passed' if len(rows) == 3 and all(
                r['status'] in ('passed', 'reused') for r in rows) else 'failed', 'phases': rows,
                'installation_identity': inputs,
                'candidate_gate_authority': False}
        identity = self.ctx.queue.verify(self.ctx.lease)
        with self.workspace.db.write() as s:
            self.ctx.queue.verify_identity(s, identity)
            artifact = self.workspace.store.put_json(s, project_id=identity['project_id'], kind='report',
                name='developer-checks.json', document=self.workspace.redactor.redact_value(report, source=True),
                meta={'producer': 'developer-checks', 'job_id': identity['job_id'],
                      'generation': identity['generation'], 'fake': identity['fake']})
            from app.persistence.models import Job
            job = s.get(Job, identity['job_id'])
            job.runtime_ref = {**job.runtime_ref, 'pipeline_checks': {
                'artifact_id': artifact.id, 'status': report['status'], 'generation': identity['generation']}}
            from app.persistence import append_message
            append_message(s, project_id=identity['project_id'], ticket_id=identity['ticket_id'],
                thread_id=f"job:{identity['job_id']}:g{identity['generation']}", sender='service:checks',
                body='Developer checks: ' + report['status'], attachment_ids=[artifact.id],
                meta={'runtime_log': True, 'generation': identity['generation']})
        response = {**json.loads(json.dumps(report)), 'artifact_id': artifact.id,
                'next': 'Inspect diff and submit_candidate.' if report['status'] == 'passed' else
                        'Fix the first failed phase, then run_checks again. Full stdout/stderr remain in command evidence.'}
        # Tool transport uses ASCII JSON and a 64 KiB envelope. Preserve the
        # full saved report, but never let long/unicode test names trigger a loop.
        while len(json.dumps(response).encode()) > 58000:
            failed = [r for r in response['phases'] if r.get('error_excerpt')]
            if failed and len(max(failed, key=lambda r: len(r['error_excerpt']))['error_excerpt']) > 100:
                row = max(failed, key=lambda r: len(r['error_excerpt']))
                row['error_excerpt'] = row['error_excerpt'][:len(row['error_excerpt']) // 2]
                response['excerpt_truncated'] = True
                continue
            gates = [r['repository_gate'] for r in response['phases'] if r.get('repository_gate', {}).get('failed_test_ids')]
            if not gates:
                raise ValueError('checks summary exceeded transport bound; read its artifact')
            gates[0]['failed_test_ids'].pop()
            response['failed_ids_truncated'] = True
        return response
