"""Real SQLite/Git/Docker baseline. No model call or real user approval is simulated by these checks."""
import json
from types import SimpleNamespace
import pytest
pytest.importorskip('fcntl')
from app.domain import Actor, Workflow
from app.agents import Redactor
from app.http.service import bind
from app.onboarding.requests import request
from app.onboarding.runtime import OnboardingRuntime
from app.persistence.models import Project, Message, Job
from app.workers import JobQueue, ProviderLimiter
from app.workers.runtime import RunContext
from app.workers.queue import StaleLease
from app.workspace import WorkspaceSupervisor
from app.workspace.manifest import reference_manifest_dict
from sqlalchemy import select
from tests.persistence.conftest import db, db_path, store  # noqa: F401
from .test_source import source_repo, fingerprint


def setup(db, store, tmp_path, raw=None):
    source, git = source_repo(tmp_path)
    w = Workflow(db, store)
    user = Actor('user:test', 'user', 'existing')
    p = w.create_project(user, name='Existing fixture', mode='existing', repo_ref=str(source))
    q = JobQueue(db, lease_s=3600)
    with db.write() as s:
        p, job = request(s, bind(s, store, Redactor()), store, user, p.revision,
                         raw or reference_manifest_dict(), 'test-request')
    lease = q.claim('worker:onboarding-test', 'execution', capacity=1, runtimes=('onboarding',))
    assert lease and lease.job_id == job.id
    snapshot = {'id': job.id, 'project_id': p.id, 'ticket_id': None, 'stage': job.stage,
                'runtime_ref': job.runtime_ref, 'limits': job.limits, 'lane': 'execution'}
    ctx = RunContext(queue=q, limiter=ProviderLimiter(), lease=lease, job=snapshot)
    runtime = OnboardingRuntime(db, store, tmp_path / 'managed', Redactor())
    return SimpleNamespace(source=source, git=git, w=w, user=user, queue=q, ctx=ctx, runtime=runtime, p=p, job=job)


def require_docker(root):
    sup = WorkspaceSupervisor(root)
    if not sup.sandbox.available():
        pytest.skip('actual Docker required')
    sup.sandbox.image_id('node:22.20.0-alpine')


def fast_manifest():
    raw = reference_manifest_dict()
    raw['commands'].update(install={'argv': ['node', '-e', 'process.exit(0)']},
        build={'argv': ['node', '-e', "require('fs').mkdirSync('dist',{recursive:true});require('fs').writeFileSync('dist/index.html','<p>Baseline</p>')"]},
        test={'argv': ['node', '--test', 'test.cjs']},
        start={'argv': ['node', 'serve.cjs']})
    return raw


def fast_source(env, test="const t=require('node:test');t('baseline',()=>{});"):
    (env.source / 'test.cjs').write_text(test)
    (env.source / 'serve.cjs').write_text("require('node:http').createServer((q,r)=>r.end(require('fs').readFileSync('dist/index.html'))).listen(4173,'0.0.0.0')")
    env.git.run(['add', '.'], cwd=env.source)
    env.git.run(['commit', '-m', 'fast baseline fixture'], cwd=env.source)


def read_project(db, pid):
    with db.read() as s:
        return s.get(Project, pid)


def test_reference_react_vite_install_build_test_start_and_dirty_source_are_verified(db, store, tmp_path):
    require_docker(tmp_path / 'managed')
    env = setup(db, store, tmp_path)
    (env.source / 'local.txt').write_text('keep local')
    before = fingerprint(env.source)
    try:
        outcome = env.runtime.run(env.ctx)
        assert outcome.status == 'succeeded', outcome
        p = read_project(db, env.p.id)
        assert p.workflow['onboarding'] == 'ready'
        assert p.workflow['onboarding_detail']['dirty']
        assert p.workflow['accepted_tip'] == WorkspaceSupervisor(env.runtime.root).broker(p.id).accepted_sha()
        with db.read() as s:
            report = json.loads(store.read_bytes(s, outcome.result['report_artifact_id']))
            assert report['baseline']['gate']['status'] == 'passed'
            assert report['baseline']['start']['healthy']
            assert len(report['commands']) == 7 and all(c['exit_code'] == 0 for c in report['commands'])
            assert report['toolchain'] == {'node': '22.20.0', 'npm': '10.9.3'}
            assert any(outcome.result['report_artifact_id'] in m.attachment_ids
                       for m in s.scalars(select(Message).where(Message.project_id == p.id)))
        assert fingerprint(env.source) == before
    finally:
        assert env.ctx.stop_resources()


def test_baseline_failure_is_separate_and_onboarding_does_not_grant_waiver(db, store, tmp_path):
    require_docker(tmp_path / 'managed')
    env = setup(db, store, tmp_path, fast_manifest())
    fast_source(env, "const t=require('node:test');const a=require('node:assert');t('existing bug',()=>a.equal(1,2));")
    try:
        outcome = env.runtime.run(env.ctx)
        assert outcome.status == 'succeeded', outcome
        p = read_project(db, env.p.id)
        assert p.workflow['onboarding'] == 'ready_with_baseline_failures'
        assert p.workflow['onboarding_detail']['required_checks'] == 'failed'
        assert p.workflow['accepted_tip']
        with db.read() as s:
            from app.persistence.models import Approval
            assert s.scalar(select(Approval.id)) is None
    finally:
        assert env.ctx.stop_resources()


def test_incomplete_tests_block_pipeline_with_command_evidence(db, store, tmp_path):
    require_docker(tmp_path / 'managed')
    raw = fast_manifest()
    raw['commands']['test'] = {'argv': ['node', 'test.cjs']}
    env = setup(db, store, tmp_path, raw)
    fast_source(env, 'console.log("not TAP");')
    try:
        outcome = env.runtime.run(env.ctx)
        assert outcome.status == 'failed'
        p = read_project(db, env.p.id)
        assert p.workflow['onboarding'] == 'blocked' and not p.workflow.get('pipeline')
        assert 'incomplete' in p.workflow['onboarding_detail']['blocker']
        with db.read() as s:
            report = json.loads(store.read_bytes(s, p.workflow['onboarding_detail']['report_artifact_id']))
            assert len(report['commands']) == 5
    finally:
        assert env.ctx.stop_resources()


def test_revoked_job_cannot_publish_configuration_or_accepted_tip(db, store, tmp_path, monkeypatch):
    env = setup(db, store, tmp_path, fast_manifest())
    from app.onboarding import runtime as module
    real = module.import_source
    def revoke(*args, **kwargs):
        result = real(*args, **kwargs)
        env.queue.cancel(env.job.id, reason='test stop', actor='user:test')
        return result
    monkeypatch.setattr(module, 'import_source', revoke)
    with pytest.raises(StaleLease):
        env.runtime.run(env.ctx)
    assert not read_project(db, env.p.id).workflow.get('accepted_tip')


def test_publication_failure_rolls_back_then_reuses_independent_import(db, store, tmp_path, monkeypatch):
    require_docker(tmp_path / 'managed')
    env = setup(db, store, tmp_path, fast_manifest())
    fast_source(env)
    from app.onboarding import runtime as module
    real = module.append_message
    monkeypatch.setattr(module, 'append_message', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('publication crash')))
    try:
        with pytest.raises(RuntimeError, match='publication crash'):
            env.runtime.run(env.ctx)
        assert not read_project(db, env.p.id).workflow.get('accepted_tip')
        monkeypatch.setattr(module, 'append_message', real)
        assert env.runtime.run(env.ctx).status == 'succeeded'
        with db.read() as s:
            messages = [m for m in s.scalars(select(Message).where(Message.project_id == env.p.id))
                        if m.meta.get('intent') == 'onboarding_baseline']
            assert len(messages) == 1 and messages[0].attachment_ids
    finally:
        assert env.ctx.stop_resources()


def test_restart_cleanup_validates_generation_owner_and_retains_managed_baseline(db, store, tmp_path):
    require_docker(tmp_path / 'managed')
    env = setup(db, store, tmp_path, fast_manifest())
    from app.onboarding.source import import_source
    from app.workspace.manifest import parse_manifest
    sup = WorkspaceSupervisor(env.runtime.root)
    saved = import_source(sup, env.p.id, env.source, env.job.id)
    started = sup.start_attempt(env.p.id, ticket_id='onboarding', scope_version=1, role='developer', attempt=1,
        generation=env.ctx.lease.generation, lease_id=env.ctx.lease.owner, manifest=parse_manifest(fast_manifest()),
        provenance={'job_id': env.job.id})
    resource = {'kind': 'pipeline_workspace', 'project_id': env.p.id, 'run_id': started.ref.run_id,
                'generation': env.ctx.lease.generation, 'owner': env.ctx.tag}
    env.queue.register_resource(env.ctx.lease, resource)
    def snapshot():
        with db.read() as s:
            j = s.get(Job, env.job.id)
            return {'id': j.id, 'project_id': j.project_id, 'runtime_ref': j.runtime_ref}
    assert not env.runtime.reconcile(snapshot())  # live lease cannot be reaped
    env.queue.cancel(env.job.id, reason='crashed fixture', actor='user:test')
    original = snapshot()
    forged = {**original, 'runtime_ref': {**original['runtime_ref'], 'resources': [{**resource, 'owner': 'another-job:1'}]}}
    assert not env.runtime.reconcile(forged)
    assert env.runtime.reconcile(original)
    assert not (sup.root / env.p.id / 'runs' / started.ref.run_id / 'worktree').exists()
    assert sup.broker(env.p.id).accepted_sha() == saved['baseline_sha']
