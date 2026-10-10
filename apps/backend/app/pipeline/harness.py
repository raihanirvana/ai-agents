"""Separate Docker runner; authoritative frames come exclusively from the runner container."""
from pathlib import Path
import base64
import hashlib
import json
import time
import uuid
from .contracts import digest_of, validate_report

SUITE_DIR = Path(__file__).resolve().parents[4] / 'contracts' / 'verification'
RUNNER_IMAGE = 'aiagent-verification:1.63.0'


def remove_owned(sandbox, name, owner):
    def inspect_owner():
        probe = sandbox._docker('inspect', '--format', '{{index .Config.Labels "aiagent.owner"}}', name, check=False)
        if probe.returncode == 0:
            return probe.stdout.decode().strip()
        sandbox._docker('info', '--format', '{{.ServerVersion}}')  # engine loss cannot count as cleanup
        error = probe.stderr.lower()
        if b'no such object:' not in error and b'no such container:' not in error:
            raise RuntimeError('container absence could not be verified')
        return None
    actual = inspect_owner()
    if actual is None:
        return
    if actual != owner:
        raise ValueError('container cleanup ownership mismatch')
    # Stop and the run's own cleanup can remove the same container at the same moment: `docker rm -f` then answers
    # "removal already in progress" and the container is still visible for a moment. That is not a leak; wait for it.
    deadline = time.monotonic() + 15
    while True:
        sandbox.kill_and_remove(name)
        current = inspect_owner()
        if current is None:
            return
        if current != owner:
            raise ValueError('container cleanup ownership mismatch')
        if time.monotonic() >= deadline:
            raise RuntimeError('owned container remains after cleanup')
        time.sleep(0.2)


def legacy_locators(target):
    """Suites pinned before UI contracts keep Playwright engine selector semantics."""
    return not target.get('ui_contract')


def code_digest():
    return digest_of({name: hashlib.sha256((SUITE_DIR / name).read_bytes()).hexdigest()
                      for name in ('acceptance.py', 'static-server.cjs', 'Dockerfile')})


class DockerHarness:
    def __init__(self, sandbox, *, image=RUNNER_IMAGE, timeout_s=150):
        self.sandbox, self.image, self.timeout_s = sandbox, image, timeout_s

    def identity(self):
        return {'runner_code_digest': code_digest(), 'runner_image_id': self.sandbox.image_id(self.image)}

    def run(self, ctx, site, target_digest, suite, node_image, *, expected_runner, diagnostics=True,
            legacy_locators=False, legacy_test_ids=()):
        legacy_test_ids = sorted(set(legacy_test_ids))
        if not set(legacy_test_ids) <= {test.id for test in suite.tests}:
            raise ValueError('legacy locator test IDs must belong to the pinned suite')
        diagnostics_enabled = diagnostics
        identity = self.identity()
        if identity != expected_runner:
            raise ValueError('runner configuration changed: a new target is required')
        ctx.queue.verify(ctx.lease)
        invocation = 'e2e-' + uuid.uuid4().hex
        target_name, runner_name = 'pipeline-' + invocation + '-target', 'pipeline-' + invocation + '-runner'
        labels = {'aiagent.owner': ctx.tag, 'aiagent.verification': invocation}
        resource = {'kind': 'pipeline_containers', 'names': [target_name, runner_name], 'owner': ctx.tag,
                    'generation': ctx.lease.generation}
        ctx.queue.register_resource(ctx.lease, resource)  # durable intention before Docker create
        def cleanup():
            for name in (runner_name, target_name):
                remove_owned(self.sandbox, name, ctx.tag)
        ctx.add_stopper(cleanup, resource=resource)
        # Plan is supervisor-owned; only the runner receives it, readonly.
        plan_path = Path(site).parent / (invocation + '-plan.json')
        plan = suite.model_dump_json()
        if legacy_locators:  # Runner input only; the suite and its digest are unchanged.
            plan = json.dumps({**json.loads(plan), 'locator_semantics': 'legacy_engine'})
        if legacy_test_ids:
            plan = json.dumps({**json.loads(plan), 'legacy_test_ids': legacy_test_ids})
        plan_path.write_text(plan, encoding='utf-8')
        plan_path.chmod(0o444)
        docker = self.sandbox._docker
        common = ['--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--pids-limit', '256', '--memory', '1g', '--cpus', '1', '--user', '1000:1000',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=512m']
        label_args = [a for k, v in labels.items() for a in ('--label', f'{k}={v}')]
        report, stdout, stderr, exit_code, error = None, b'', b'', None, ''
        started = time.monotonic()
        try:
            ctx.queue.reserve(ctx.lease, 'tool')
            docker('create', '--name', target_name, '--network', 'none', *common, *label_args,
                '--mount', f'type=bind,src={Path(site).resolve()},dst=/site,readonly',
                '--mount', f'type=bind,src={SUITE_DIR / "static-server.cjs"},dst=/server.cjs,readonly',
                node_image, 'node', '/server.cjs')
            docker('start', target_name)
            ctx.queue.verify(ctx.lease)
            docker('create', '--name', runner_name, '--network', 'container:' + target_name, *common, *label_args,
                '-e', 'AIAGENT_DIAGNOSTICS=' + ('on-failure' if diagnostics else 'none'),
                '--mount', f'type=bind,src={SUITE_DIR},dst=/suite,readonly',
                '--mount', f'type=bind,src={plan_path},dst=/plan.json,readonly',
                identity['runner_image_id'], invocation, target_digest, suite.digest, 'http://127.0.0.1:4173/')
            result = docker('start', '--attach', runner_name, timeout=self.timeout_s, check=False)
            stdout, stderr = result.stdout, result.stderr
            exit_code = int(docker('inspect', '--format', '{{.State.ExitCode}}', runner_name).stdout)
            if len(stdout) <= 8 * 1024 * 1024:
                frames = [line[16:] for line in stdout.splitlines() if line.startswith(b'PIPELINE_REPORT ')]
                if len(frames) == 1:
                    report = json.loads(frames[0])
        except Exception as exc:
            # Timeouts/transport failures still leave an explicit incomplete record.
            error = type(exc).__name__ + ': ' + str(exc)[:400]
        finally:
            cleanup()
        ctx.queue.verify(ctx.lease)  # stale reports must never be published
        result = validate_report(report, invocation_id=invocation, target_digest=target_digest, suite=suite)
        if (result['status'] == 'passed' and exit_code != 0) or not isinstance(report, dict) or report.get('smoke_passed') is not True:
            result = {'status': 'incomplete', 'counts': {}, 'executed': [], 'coverage': {}}
        diagnostics = []
        items = report.get('artifacts', []) if isinstance(report, dict) else []
        for item in items if isinstance(items, list) else []:
            try:
                raw = base64.b64decode(item['data'], validate=True)
                if (item['kind'] in ('screenshot', 'trace', 'aria_snapshot') and len(raw) <=
                    (16 * 1024 if item['kind'] == 'aria_snapshot' else 1024 * 1024) and
                    item['test_id'] in {t.id for t in suite.tests} and hashlib.sha256(raw).hexdigest() == item['sha256']):
                    diagnostics.append({**item, 'data': raw, 'diagnostic_kind': item['kind'],
                                        'kind': 'log' if item['kind'] == 'aria_snapshot' else item['kind']})
            except (KeyError, ValueError, TypeError):
                pass
        if isinstance(report, dict):
            report = {k: v for k, v in report.items() if k != 'artifacts'}
        else:
            report = None
        duration = time.monotonic() - started
        from app.workers.telemetry import record_phase
        record_phase(ctx, 'browser' if diagnostics_enabled else 'baseline_browser', duration,
                     status='passed' if result['status'] == 'passed' else 'failed')
        return {**result, 'invocation_id': invocation, 'target_digest': target_digest, 'suite_digest': suite.digest,
            'runner': identity, 'report': report, 'exit_code': exit_code, 'error': error,
            'infrastructure_failure': result['status'] == 'incomplete', 'duration_s': duration,
            'commands': [{'argv': ['isolated-browser-runner', invocation], 'exit_code': exit_code}],
            'stdout_sha256': hashlib.sha256(stdout).hexdigest(), 'stderr_sha256': hashlib.sha256(stderr).hexdigest(),
            'stderr': stderr.decode(errors='replace')[:4000], 'diagnostics': diagnostics}
