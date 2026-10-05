"""Actual Docker/build/browser, scripted FAKE model/driver. This is contract evidence, not real provider QA."""
import os
import json
from pathlib import Path
import time
import traceback
import pytest
pytest.importorskip('fcntl')
from sqlalchemy import select
from app.pipeline.workspace import ProductWorkspace
from app.pipeline.runtime import PipelineRuntime
from app.pipeline.scheduler import PipelineScheduler
from app.pipeline.harness import DockerHarness
from app.workspace import WorkspaceSupervisor
from app.workspace.manifest import reference_manifest_dict, parse_manifest
from app.persistence.models import Project, Candidate, Job, Verification, Message
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.pipeline.test_contracts import PLAN

HTML = '<button id="add">Add</button><div id="total">0</div><script>document.querySelector("#add").onclick=()=>document.querySelector("#total").textContent="4";</script>'
FILES = {'index.html': HTML, 'build.cjs': "require('fs').mkdirSync('dist',{recursive:true});require('fs').copyFileSync('index.html','dist/index.html')",
    'test.cjs': "const test=require('node:test');const assert=require('node:assert/strict');test('fixture has an Add button',()=>assert.match(require('fs').readFileSync('index.html','utf8'),/id=\"add\"/));"}


class ScriptedDriver:
    """Hermes stand-in explicitly labelled fake by the runtime. Reservations still use production queue."""
    def __init__(self, *, broken=False, wait=False):
        self.broken, self.wait = broken, wait

    def run(self, ctx, identity, snapshot, tools, parameters):
        ctx.model_call(lambda cap: ({}, {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2, 'cost_usd': 0}))
        def call(name, args):
            try:
                return ctx.tool_call(name, lambda: tools[name](args))
            except Exception:
                traceback.print_exc()
                raise
        if identity['role'] == 'qa':
            call('propose_tests', {'plan': PLAN})
        else:
            for name, data in FILES.items():
                if name == 'index.html' and self.broken:
                    data = data.replace('textContent="4"', 'textContent="9"')
                call('patch_file', {'path': name, 'content': data})
            if self.wait and ctx.answer is None:
                call('request_decision', {'question': 'Should the item total be four?'})
            call('submit_candidate', {'message': 'Implement cart total'})
        return {'completed': True}


def setup(env, tmp_path, driver=None):
    root = tmp_path / 'workspaces'
    supervisor = WorkspaceSupervisor(root)
    if not supervisor.sandbox.available():
        pytest.skip('actual Docker engine required')
    try:
        supervisor.sandbox.image_id('node:22.20.0-alpine')
        supervisor.sandbox.image_id('aiagent-verification:1.63.0')
    except Exception:
        pytest.skip('build pinned verification image and pull node image first')
    base = supervisor.create_project(env.project.id)
    raw = reference_manifest_dict()
    raw['commands'].update(install={'argv': ['node', '-e', 'process.exit(0)']},
        build={'argv': ['node', 'build.cjs']}, test={'argv': ['node', '--test', 'test.cjs']})
    with env.db.write() as s:
        p = s.get(Project, env.project.id)
        p.workflow = {'accepted_tip': base, 'pipeline': {'manifest': raw}}
    harness = DockerHarness(supervisor.sandbox)
    workspace = ProductWorkspace(env.db, env.store, env.world.w, root, harness, env.redactor)
    runtime = PipelineRuntime(env.runtime, workspace, driver or ScriptedDriver())
    scheduler = PipelineScheduler(env.db, env.queue, env.world.w, runtime=runtime.name)
    sup = env.supervisor(**{runtime.name: runtime})
    sup.maintenance.append(scheduler.tick)
    return runtime, sup, scheduler


def plan(accept=True):
    return {'kind': 'technical_plan', 'summary': 'Add cart total', 'steps': [{'title': 'Add handler'}]}


def test_actual_build_and_browser_pass_with_fake_model_cannot_advance_uat(agent_env, tmp_path):
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path)
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Diff implements scope', 'findings': []})
    t = env.approved_ticket()
    env.run_until(sup, lambda: any(j.stage == 'qa' and j.status == 'failed' for j in jobs(env, t.id)), timeout_s=90)
    sup.wait_idle(10)
    with env.db.read() as s:
        verification = s.scalar(select(Verification))
        assert verification.status == 'incomplete'
        assert verification.results['fake_provider'] is True
        assert verification.counts['passed'] == 1  # actual browser passed, but fake label prevents QA admission
        assert env.world.ticket(t.id).phase == 'qa'
        candidate = s.scalar(select(Candidate))
        assert candidate.commit_sha != candidate.base_sha and candidate.target_digest
        assert all(not Path(runtime.workspace.root / env.project.id / 'runs' / resource['run_id'] / 'worktree').exists()
            for j in jobs(env, t.id) for resource in j.runtime_ref.get('resources', []) if resource['kind'] == 'pipeline_workspace')


def jobs(env, ticket_id):
    with env.db.read() as s:
        return list(s.scalars(select(Job).where(Job.ticket_id == ticket_id).order_by(Job.created_at)))


def test_direct_product_commit_build_receipt(agent_env, tmp_path):
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path)
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Correct'})
    t = env.approved_ticket()
    from app.domain import Attempt
    for stage in ('technical_plan', 'qa_plan', 'development', 'technical_review', 'qa'):
        job_id = scheduler.tick()[0]
        ctx = env.ctx(env.get(job_id))
        if stage in ('development', 'technical_review', 'qa'):
            env.world.w.bind_attempt(env.world.actor('scheduler'), t.id, env.world.ticket(t.id).revision,
                                    Attempt(ctx.lease.job_id, ctx.lease.generation, t.current_version))
        try:
            outcome = runtime.run(ctx)
            assert outcome.status == ('failed' if stage == 'qa' else 'succeeded'), outcome.error
            if stage == 'qa':
                env.queue.fail(ctx.lease, error=outcome.error, retryable=False)
            else:
                if env.get(job_id).status == 'running':
                    env.queue.complete(ctx.lease, outcome.result)
        finally:
            ctx.stop_resources()
            env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)


def test_review_rejection_stops_after_three_cycles_and_usage_is_not_reset(agent_env, tmp_path):
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path)
    env.script(plan(), *[{'kind': 'review', 'accept': False, 'summary': 'Wrong total', 'findings': ['repair']} for _ in range(3)])
    t = env.approved_ticket()
    env.run_until(sup, lambda: (env.world.ticket(t.id).blocker or {}).get('reason') == 'needs_human', timeout_s=120)
    sup.wait_idle(10)
    attempts = [j for j in jobs(env, t.id) if j.stage == 'development']
    assert len(attempts) == 3
    assert sum(j.usage.get('model_calls', 0) for j in jobs(env, t.id)) >= 8
    assert scheduler.tick() == []


def test_developer_input_checkpoint_is_product_persistent_and_resumes_new_generation(agent_env, tmp_path):
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path, ScriptedDriver(wait=True))
    env.script(plan(), {'kind': 'answer', 'outcome': 'proceed', 'answer': 'Total four'},
               {'kind': 'review', 'accept': True, 'summary': 'Correct'})
    t = env.approved_ticket()
    env.run_until(sup, lambda: any(j.stage == 'qa' and j.status == 'failed' for j in jobs(env, t.id)), timeout_s=100)
    sup.wait_idle(10)
    dev = next(j for j in jobs(env, t.id) if j.stage == 'development')
    assert dev.status == 'succeeded' and dev.lease_generation > 1
    assert dev.runtime_ref['pipeline_checkpoint']
    with env.db.read() as s:
        assert any(m.kind == 'input_request' for m in s.scalars(select(Message).where(Message.ticket_id == t.id)))


def test_broken_candidate_with_green_repo_gate_cannot_pass_browser_qa(agent_env, tmp_path):
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path, ScriptedDriver(broken=True))
    env.script(plan(), *[{'kind': 'review', 'accept': True, 'summary': 'Contract reviewer accepts diff'} for _ in range(3)])
    t = env.approved_ticket()
    env.run_until(sup, lambda: (env.world.ticket(t.id).blocker or {}).get('reason') == 'needs_human', timeout_s=120)
    sup.wait_idle(10)
    with env.db.read() as s:
        verifications = list(s.scalars(select(Verification)))
        assert len(verifications) == 3 and all(v.status == 'failed' and v.counts['failed'] == 1 for v in verifications)
    assert env.world.ticket(t.id).phase == 'development'


def test_crash_after_candidate_publication_does_not_retry_development_in_the_wrong_phase(agent_env, tmp_path):
    class CrashAfterPublication(ScriptedDriver):
        def run(self, *args, **kw):
            result = super().run(*args, **kw)
            if args[1]['role'] == 'developer':
                raise RuntimeError('injected crash after atomic publication')
            return result
    env = agent_env
    runtime, sup, scheduler = setup(env, tmp_path, CrashAfterPublication())
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Correct'})
    t = env.approved_ticket()
    env.run_until(sup, lambda: any(j.stage == 'qa' and j.status == 'failed' for j in jobs(env, t.id)), timeout_s=90)
    sup.wait_idle(10)
    development = [j for j in jobs(env, t.id) if j.stage == 'development']
    assert len(development) == 1 and development[0].status == 'succeeded'
    assert development[0].result['pipeline_completion']['job_id'] == development[0].id
