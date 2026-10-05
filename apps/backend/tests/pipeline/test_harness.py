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
