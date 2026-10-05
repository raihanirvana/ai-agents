"""Transport/validation tests with a Docker stub; these are not browser execution evidence."""
from types import SimpleNamespace
import json
import pytest
from app.pipeline.harness import DockerHarness, remove_owned
from tests.pipeline.test_contracts import suite
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401


class DockerStub:
    def __init__(self, mode='pass'):
        self.mode, self.containers, self.commands = mode, {}, []

    def image_id(self, name):
        return 'sha256:' + 'd' * 64

    def kill_and_remove(self, name):
        self.containers.pop(name, None)

    def _docker(self, *args, **kwargs):
        self.commands.append(args)
        stdout, stderr, code = b'', b'', 0
        if args[0] == 'create':
            name = args[args.index('--name') + 1]
            owner = next(a.split('=', 1)[1] for a in args if a.startswith('aiagent.owner='))
            self.containers[name] = (owner, args)
        elif args[0] == 'inspect':
            name = args[-1]
            if name not in self.containers:
                code = 1
                stderr = b'error: no such object: ' + name.encode()
            elif 'ExitCode' in ' '.join(args):
                stdout = b'0'
            else:
                stdout = self.containers[name][0].encode()
        elif args[0] == 'start' and '--attach' in args:
            if self.mode == 'timeout':
                raise TimeoutError('runner timeout')
            invocation, target, digest, url = self.containers[args[-1]][1][-4:]
            report = {'schema': 1, 'invocation_id': invocation, 'target_digest': target, 'suite_digest': digest,
                'tests': [{'id': 'total', 'uac': ['UAC-1'], 'status': 'passed'}],
                'discovered': 1, 'executed': 1, 'passed': 1, 'failed': 0, 'skipped': 0, 'smoke_passed': True}
            if self.mode == 'foreign': report['invocation_id'] = 'forged-by-target'
            if self.mode == 'missing': report['tests'] = []
            if self.mode == 'skipped': report.update(executed=0, passed=0, skipped=1)
            if self.mode == 'unhealthy': report['smoke_passed'] = False
            stdout = b'PIPELINE_REPORT ' + json.dumps(report).encode() + b'\n'
            if self.mode == 'multiple': stdout += stdout
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


@pytest.mark.parametrize('mode', ['timeout', 'foreign', 'missing', 'skipped', 'unhealthy', 'multiple'])
def test_invalid_report_and_runner_timeout_leave_incomplete_evidence_and_cleanup(agent_env, tmp_path, mode):
    job = agent_env.job('qa', 'verify', stage='contract', lane='execution')
    ctx = agent_env.ctx(job)
    docker = DockerStub(mode)
    harness = DockerHarness(docker)
    site = tmp_path / 'site'
    site.mkdir()
    result = harness.run(ctx, site, 'a' * 64, suite(), 'node-image', expected_runner=harness.identity())
    assert result['status'] == 'incomplete' and result['infrastructure_failure']
    assert not docker.containers
    assert agent_env.get(job.id).runtime_ref['resources']
    creates = [c for c in docker.commands if c[0] == 'create']
    assert '--network' in creates[0] and 'none' in creates[0]
    assert 'container:' in ' '.join(creates[1])
    assert all('--publish' not in c for c in creates)
    assert 'dst=/plan.json,readonly' not in ' '.join(creates[0])
    assert 'dst=/plan.json,readonly' in ' '.join(creates[1])
    assert ctx.stop_resources()


def test_cleanup_never_kills_a_container_with_another_owner():
    docker = DockerStub()
    docker.containers['name'] = ('another-owner', ())
    with pytest.raises(ValueError, match='ownership'):
        remove_owned(docker, 'name', 'our-job:1')
    assert 'name' in docker.containers


def test_inspection_error_is_not_proof_of_absent_container_even_if_engine_is_available():
    class UncertainDocker(DockerStub):
        def _docker(self, *args, **kwargs):
            if args[0] == 'inspect':
                return SimpleNamespace(stdout=b'', stderr=b'inspect transport/permission failure', returncode=1)
            return super()._docker(*args, **kwargs)
    with pytest.raises(RuntimeError, match='absence could not be verified'):
        remove_owned(UncertainDocker(), 'name', 'our-job:1')


def test_cleanup_waits_for_a_concurrent_removal_instead_of_calling_it_a_leak():
    """DEV-013 review: stop and the run's own cleanup remove the same container at once; `docker rm -f` answers
    'removal already in progress' and the container stays visible for a moment. 18 of 25 real concurrent cleanups failed."""
    class SlowRemoval(DockerStub):
        removals = 0

        def kill_and_remove(self, name):
            self.removals += 1
            if self.removals >= 3:  # the other remover finishes after our first two attempts
                self.containers.pop(name, None)
    docker = SlowRemoval()
    docker.containers['name'] = ('our-job:1', ())
    remove_owned(docker, 'name', 'our-job:1')
    assert 'name' not in docker.containers and docker.removals == 3


def test_a_container_that_never_goes_away_is_still_reported_after_the_wait(monkeypatch):
    import app.pipeline.harness as module
    clock = [0.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s)))

    class Stuck(DockerStub):
        def kill_and_remove(self, name):
            pass
    docker = Stuck()
    docker.containers['name'] = ('our-job:1', ())
    with pytest.raises(RuntimeError, match='remains after cleanup'):
        remove_owned(docker, 'name', 'our-job:1')
    assert clock[0] >= 15 and 'name' in docker.containers


def test_concurrent_cleanups_of_one_real_container_both_succeed():
    import threading, uuid
    import os
    if os.name != 'posix':
        pytest.skip('actual Docker on Linux/macOS required')
    from app.workspace.sandbox import SandboxError
    from app.workspace.sandbox import DockerSandbox
    sandbox = DockerSandbox(supervisor_id='race-test')
    if not sandbox.available():
        pytest.skip('actual Docker on Linux/macOS required')
    try:
        sandbox.image_id('node:22.20.0-alpine')
    except SandboxError:
        pytest.skip('node:22.20.0-alpine is not present locally')
    for _ in range(6):
        name = 'race-' + uuid.uuid4().hex[:8]
        sandbox._docker('run', '-d', '--name', name, '--network', 'none', '--label', 'aiagent.owner=race:1',
                        'node:22.20.0-alpine', 'sleep', '60')
        errors = []

        def clean():
            try:
                remove_owned(sandbox, name, 'race:1')
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
        threads = [threading.Thread(target=clean) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        sandbox._docker('rm', '-f', name, check=False)
        assert errors == []
