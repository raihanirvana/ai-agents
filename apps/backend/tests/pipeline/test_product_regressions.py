"""Lease/publication/recovery regressions with actual DB, Git and Docker resources. Models are FAKE."""
import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
pytest.importorskip('fcntl')
from sqlalchemy import select
from app.domain import Attempt
from app.persistence.models import Job, Verification, Project, Artifact
from app.workers.queue import StaleLease
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.pipeline.test_product_loop import setup, plan, ScriptedDriver, FILES


def seed_baseline(env, runtime, files):
    """Trusted fixture setup, not product integration or an agent approval."""
    from app.workspace import WorkspaceSupervisor
    from app.workspace.manifest import parse_manifest
    sup = WorkspaceSupervisor(runtime.workspace.root)
    with env.db.read() as s:
        manifest = parse_manifest(s.get(Project, env.project.id).workflow['pipeline']['manifest'])
    started = sup.start_attempt(env.project.id, ticket_id='fixture', scope_version=1,
        role='developer', attempt=1, generation=1, lease_id='fixture', manifest=manifest)
    for name, content in files.items():
        sup.write_file(started.ref, started.credential, name, content.encode())
    commit = sup.submit_candidate(started.ref, started.credential, 'Trusted regression baseline')
    sup.broker(env.project.id)._bare('update-ref', 'refs/heads/accepted', commit['sha'], commit['base_sha'])
    sup.stop_run(started.ref, 'fixture ready')
    with env.db.write() as s:
        p = s.get(Project, env.project.id)
        p.workflow = {**p.workflow, 'accepted_tip': commit['sha']}


def start_stage(env, runtime, scheduler, t, stage):
    job = env.get(scheduler.tick()[0])
    assert job.stage == stage
    ctx = env.ctx(job)
    if stage in ('development', 'technical_review', 'qa'):
        env.world.w.bind_attempt(env.world.actor('scheduler'), t.id, env.world.ticket(t.id).revision,
                                Attempt(ctx.lease.job_id, ctx.lease.generation, t.current_version))
    return ctx


def prepare(env, runtime, scheduler, t):
    for stage in ('technical_plan', 'qa_plan', 'development', 'technical_review'):
        ctx = start_stage(env, runtime, scheduler, t, stage)
        try:
            outcome = runtime.run(ctx)
            assert outcome.status == 'succeeded', outcome.error
            if env.get(ctx.lease.job_id).status == 'running':
                env.queue.complete(ctx.lease, outcome.result)
        finally:
            ctx.stop_resources()
            env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)


@pytest.mark.parametrize('purpose', ['regression', 'feature'])
def test_baseline_green_is_allowed_only_for_regression_not_a_new_feature(agent_env, tmp_path, purpose):
    from copy import deepcopy
    from tests.pipeline.test_contracts import PLAN
    class Driver(ScriptedDriver):
        def run(self, ctx, identity, snapshot, tools, parameters):
            if identity['role'] == 'qa':
                proposed = deepcopy(PLAN)
                proposed['tests'][0]['purpose'] = purpose
                ctx.tool_call('propose_tests', lambda: tools['propose_tests']({'plan': proposed}))
                return {}
            return super().run(ctx, identity, snapshot, tools, parameters)
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, Driver())
    seed_baseline(env, runtime, FILES)
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Correct'})
    t = env.approved_ticket()
    prepare(env, runtime, scheduler, t)
    ctx = start_stage(env, runtime, scheduler, t, 'qa')
    try:
        outcome = runtime.run(ctx)
        assert outcome.status == 'failed'  # FAKE still cannot authorize QA/UAT
        with env.db.read() as s:
            v = s.scalar(select(Verification))
            reports = [json.loads(env.store.read_bytes(s, aid)) for aid in v.evidence_artifact_ids
                if s.get(Artifact, aid).kind == 'report']
            proof = next(p for p in reports if p.get('invocation_id') == v.evidence_id)
        assert proof['baseline']['execution']['counts']['passed'] == 1
        assert ('FAKE provider' if purpose == 'regression' else 'assertion also passed on base') in proof['error']
        assert env.world.ticket(t.id).phase == 'qa'
    finally:
        ctx.stop_resources()


def test_removing_a_baseline_repo_test_leaves_the_candidate_gate_incomplete(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path)
    baseline = {**FILES, 'test.cjs': FILES['test.cjs'] + "test('mandatory baseline test',()=>assert.ok(true));"}
    seed_baseline(env, runtime, baseline)
    env.script(plan())
    t = env.approved_ticket()
    for stage in ('technical_plan', 'qa_plan', 'development'):
        ctx = start_stage(env, runtime, scheduler, t, stage)
        try:
            outcome = runtime.run(ctx)
            if env.get(ctx.lease.job_id).status == 'running':
                env.queue.complete(ctx.lease, outcome.result)
            if stage == 'development':
                assert outcome.result['gate']['status'] == 'incomplete'
                assert outcome.result['gate']['missing_baseline_tests'] == ['node:mandatory baseline test']
        finally:
            ctx.stop_resources()
            env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)


def test_stop_terminates_an_inflight_browser_runner_without_waiting_for_its_timeout(agent_env, tmp_path):
    from copy import deepcopy
    from concurrent.futures import ThreadPoolExecutor
    import time
    from tests.pipeline.test_contracts import PLAN
    class SlowBrowser(ScriptedDriver):
        def run(self, ctx, identity, snapshot, tools, parameters):
            if identity['role'] == 'qa':
                proposed = deepcopy(PLAN)
                proposed['tests'][0]['steps'].append({'action': 'assert_visible', 'selector': '#never-present'})
                ctx.tool_call('propose_tests', lambda: tools['propose_tests']({'plan': proposed}))
                return {}
            return super().run(ctx, identity, snapshot, tools, parameters)
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SlowBrowser())
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Correct'})
    t = env.approved_ticket()
    prepare(env, runtime, scheduler, t)
    ctx = start_stage(env, runtime, scheduler, t, 'qa')
    docker = runtime.workspace.harness.sandbox
    def live():
        return docker._docker('ps', '--format', '{{.Names}}', '--filter', 'label=aiagent.owner=' + ctx.tag).stdout.strip()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.run, ctx)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and b'-runner' not in live():
            assert not future.done()
            time.sleep(0.05)
        assert b'-runner' in live(), 'actual browser runner never started'
        env.queue.cancel(ctx.lease.job_id, reason='user stop during browser execution', actor='user:qualification')
        started = time.monotonic()
        runtime.stop(ctx)
        with pytest.raises(StaleLease):
            future.result(timeout=10)
        assert time.monotonic() - started < 10
    assert not live()
    with env.db.read() as s:
        assert s.scalar(select(Verification)) is None
    assert env.world.ticket(t.id).phase != 'uat'


@pytest.mark.parametrize('invalidate', ['stop', 'scope'])
def test_harness_result_after_stop_or_scope_revision_cannot_publish_or_advance(agent_env, tmp_path, invalidate):
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path)
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Correct'})
    t = env.approved_ticket()
    prepare(env, runtime, scheduler, t)
    ctx = start_stage(env, runtime, scheduler, t, 'qa')
    original = runtime.workspace.harness.run
    def revoked(*args, **kwargs):
        proof = original(*args, **kwargs)
        if invalidate == 'stop':
            env.queue.cancel(ctx.lease.job_id, reason='user stop', actor='user:qualification')
        else:
            current = env.world.ticket(t.id)
            env.world.w.edit_scope(env.world.user, t.id, current.revision,
                {'title': 'Changed criteria', 'uac': [{'id': 'UAC-2', 'text': 'A different feature'}], 'dependencies': []})
        return proof
    runtime.workspace.harness.run = revoked
    try:
        with pytest.raises(StaleLease):
            runtime.run(ctx)
        with env.db.read() as s:
            assert s.scalar(select(Verification)) is None
        assert env.world.ticket(t.id).phase != 'uat'
    finally:
        ctx.stop_resources()


def test_db_revocation_denies_workspace_credential_even_if_local_manifest_is_still_active(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path)
    t = env.approved_ticket()
    job = env.job('developer', 'implement', ticket=t, stage='development', lane='execution', runtime='pipeline:fake')
    ctx = env.ctx(job)
    env.world.w.bind_attempt(env.world.actor('scheduler'), t.id, env.world.ticket(t.id).revision,
                            Attempt(ctx.lease.job_id, ctx.lease.generation, t.current_version))
    workspace, started, manifest = runtime.workspace.start(ctx)
    env.queue.cancel(job.id, reason='scope stopped', actor='user:qualification')
    assert workspace._store(started.ref).state()['status'] == 'active'
    with pytest.raises(StaleLease):
        workspace.write_file(started.ref, started.credential, 'escaped.txt', b'old attempt')
    assert not (workspace.src_dir(started.ref) / 'escaped.txt').exists()
    assert ctx.stop_resources()


def test_recovery_cleans_owned_workspace_before_retry_and_refuses_live_lease(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path)
    t = env.approved_ticket()
    job = env.job('developer', 'implement', ticket=t, stage='development', lane='execution', runtime='pipeline:fake')
    ctx = env.ctx(job)
    env.world.w.bind_attempt(env.world.actor('scheduler'), t.id, env.world.ticket(t.id).revision,
                            Attempt(ctx.lease.job_id, ctx.lease.generation, t.current_version))
    workspace, started, _ = runtime.workspace.start(ctx)
    current = env.get(job.id)
    assert runtime.reconcile({'id': current.id, 'runtime_ref': current.runtime_ref}) is False
    with env.db.write() as s:
        current = s.get(Job, job.id)
        current.lease_expires_at = env.queue.clock() - timedelta(seconds=1)
        current.runtime_ref = {**current.runtime_ref, 'cleanup': {**current.runtime_ref['cleanup'],
            'expires_at': current.lease_expires_at.isoformat()}}
    retry = env.queue.recover(job.id, runtime.reconcile, actor='worker:recovery')
    assert retry is not None and not (workspace.run_dir(started.ref) / 'worktree').exists()
    assert workspace._store(started.ref).state()['status'] != 'active'
    assert env.get(retry).usage == {} and env.get(job.id).usage.get('model_calls', 0) == 0


def test_exact_user_waiver_is_visible_and_cannot_cover_changed_failure_or_infrastructure(agent_env, tmp_path):
    env = agent_env
    runtime, _, _ = setup(env, tmp_path)
    t = env.approved_ticket()
    with env.db.read() as s:
        base = s.get(Project, t.project_id).workflow['accepted_tip']
    gate = {'status': 'failed', 'test_id': 'node:old baseline failure', 'signature': 'old-error',
            'environment_digest': 'e' * 64, 'infrastructure_failure': False}
    with env.db.write() as s:
        fingerprint = env.store.put_json(s, project_id=t.project_id, kind='report', name='fingerprint.json',
            document={'kind': 'baseline_failure', 'ticket_id': t.id, 'scope_version': t.current_version,
                'base_sha': base, 'category': 'baseline', 'uac_ids': [], 'infrastructure_failure': False,
                'environment': {'fixture': 'contract'}, **{k: gate[k] for k in ('test_id', 'signature', 'environment_digest')}},
            meta={'producer': 'verification'})
    identity = {'project_id': t.project_id, 'ticket_id': t.id, 'scope_version': t.current_version}
    candidate = SimpleNamespace(base_sha=base)
    gates = {'build_error': '', 'gate': gate}
    assert not runtime.gates_eligible(identity, candidate, gates)
    waiver = env.world.w.waive_baseline(env.world.user, t.id, env.world.ticket(t.id).revision, fingerprint.id, 'Keep this known baseline failure')
    assert runtime.gate_admission(identity, candidate, gates) == {'status': 'waived', 'waiver_ids': [waiver.id]}
    for changed in ({'signature': 'new-error'}, {'test_id': 'node:another bug'}, {'infrastructure_failure': True}, {'status': 'incomplete'}):
        assert not runtime.gates_eligible(identity, candidate, {'gate': {**gate, **changed}})
