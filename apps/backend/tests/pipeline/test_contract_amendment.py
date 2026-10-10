"""Product DB/Git planning handoff with a labelled fake model and fake driver."""
from copy import deepcopy
import pytest
from sqlalchemy import select
from app.persistence.models import Job, Message
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.pipeline.test_product_loop import setup


CONTROL = {'testid': 'add', 'role': 'button', 'name': 'Add', 'purpose': 'add a record'}


def plan(revision, controls):
    return {'kind': 'technical_plan', 'summary': 'Implement the approved record flow',
            'steps': [{'title': 'Implement controls'}],
            'ui_contract': {'revision': revision, 'controls': controls}}


class AmendmentDriver:
    def __init__(self):
        self.calls = 0

    def run(self, ctx, identity, snapshot, tools, parameters):
        self.calls += 1
        if self.calls == 1:
            response = tools['request_contract_amendment'](
                {'reason': 'UAC-1 requires a result node; add testid total for the resulting count.'})
            assert response['submitted'] and response['contract_amendment_requested']
        else:
            response = tools['propose_tests']({'plan': {'kind': 'qa_plan', 'summary': 'Approved record count',
                'tests': [{'id': 'count', 'uac': ['UAC-1'], 'purpose': 'feature', 'steps': [
                    {'action': 'click', 'selector': 'testid=add'},
                    {'action': 'assert_text', 'selector': 'testid=total', 'value': '4'}]}]}})
            assert response['submitted']
        return {}


def finish(env, runtime, scheduler, expected, *, retry=False):
    job = env.get(scheduler.tick()[0])
    assert job.stage == expected
    ctx = env.ctx(job)
    try:
        result = runtime.run(ctx)
        assert result.status == 'succeeded', result.error
        if retry:
            calls = len(env.provider.requests)
            repeated = runtime.run(ctx)
            assert repeated.status == 'succeeded'
            assert repeated.result['ui_contract_revision'] == result.result['ui_contract_revision']
            assert len(env.provider.requests) == calls
            field = 'plan_message_id' if expected == 'technical_plan' else 'contract_amendment'
            assert repeated.result[field] == result.result[field]
        env.queue.complete(ctx.lease, result.result)
        return env.get(job.id)
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)


def test_real_planning_handoff_and_tl_retry_are_idempotent(agent_env, tmp_path):
    env = agent_env
    driver = AmendmentDriver()
    runtime, _, scheduler = setup(env, tmp_path, driver)
    env.script(plan(1, [CONTROL]), plan(2, [CONTROL,
        {'testid': 'total', 'role': 'status', 'purpose': 'result count'}]))
    ticket = env.approved_ticket()
    finish(env, runtime, scheduler, 'technical_plan')
    request = finish(env, runtime, scheduler, 'qa_plan', retry=True)
    assert driver.calls == 1
    assert 'suite_artifact_id' not in request.result
    assert env.world.ticket(ticket.id).phase == 'ready'
    amended = finish(env, runtime, scheduler, 'technical_plan', retry=True)
    assert amended.lane == 'interactive' and amended.result['ui_contract_revision'] == 2
    qa = finish(env, runtime, scheduler, 'qa_plan')
    assert qa.result['suite_artifact_id'] and qa.result['ui_contract_revision'] == 2
    with env.db.read() as s:
        plans = [m for m in s.scalars(select(Message).where(Message.ticket_id == ticket.id))
                 if m.meta.get('intent') == 'technical_plan']
        jobs = list(s.scalars(select(Job).where(Job.ticket_id == ticket.id)))
    assert len(plans) == 2
    assert len({j.limits['budget_key'] for j in jobs}) == 1
    assert all(j.limits == jobs[0].limits for j in jobs)
    assert env.world.ticket(ticket.id).workflow['repair_cycles'] == 0
    assert env.get(scheduler.tick()[0]).stage == 'development'


@pytest.mark.parametrize('mutation', ['revision', 'remove', 'rename', 'decision'])
def test_amendment_rejects_replacing_existing_control_or_scope_decision(agent_env, tmp_path, mutation):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, AmendmentDriver())
    amended = plan(2, [deepcopy(CONTROL), {'testid': 'total', 'role': 'status', 'purpose': 'result'}])
    if mutation == 'revision':
        amended['ui_contract']['revision'] = 3
    elif mutation == 'remove':
        amended['ui_contract']['controls'].pop(0)
    elif mutation == 'rename':
        amended['ui_contract']['controls'][0]['name'] = 'Different Add'
    else:
        amended['needs_user'] = True
    env.script(plan(1, [CONTROL]), amended)
    ticket = env.approved_ticket()
    finish(env, runtime, scheduler, 'technical_plan')
    finish(env, runtime, scheduler, 'qa_plan')
    job = env.get(scheduler.tick()[0])
    ctx = env.ctx(job)
    try:
        outcome = runtime.run(ctx)
        assert outcome.status == 'failed'
        assert env.world.ticket(ticket.id).phase == 'ready'
        assert runtime.workspace.ui_contract(env.queue.verify(ctx.lease)).revision == 1
    finally:
        ctx.stop_resources()


def test_rejected_amendment_is_repaired_by_tl_in_the_same_job(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, AmendmentDriver())
    replaced = plan(2, [{**CONTROL, 'name': 'Different Add'}, {'testid': 'total', 'role': 'status', 'purpose': 'result'}])
    additive = plan(2, [CONTROL, {'testid': 'total', 'role': 'status', 'purpose': 'result'}])
    env.script(plan(1, [CONTROL]), replaced, additive)
    ticket = env.approved_ticket()
    finish(env, runtime, scheduler, 'technical_plan')
    finish(env, runtime, scheduler, 'qa_plan')
    before = len(env.provider.requests)
    amended = finish(env, runtime, scheduler, 'technical_plan')
    assert amended.result['ui_contract_revision'] == 2
    assert len(env.provider.requests) == before + 2  # One repair turn, no failed job.
    assert 'Amendment is additive' in str(env.provider.requests[-1])
    assert 'Changed or removed: add' in str(env.provider.requests[-1])
    assert env.world.ticket(ticket.id).phase == 'ready'
