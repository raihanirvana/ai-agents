import pytest
from app.pipeline.relay import ProductAdmission, reconcile_accounting
from app.runtime_spike.journal import AdmissionError
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401


def admission(env, limits=None):
    job = env.job('developer', 'test', lane='execution', stage='contract', limits=limits)
    ctx = env.ctx(job)
    return ProductAdmission(ctx, env.redactor), ctx


def test_relay_reserves_in_db_before_call_and_exactly_once_accounts_usage(agent_env):
    bridge, ctx = admission(agent_env)
    rid = bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'model', 'configured-model')
    job = agent_env.get(ctx.lease.job_id)
    assert job.usage['model_calls'] == 1 and rid in job.runtime_ref['pipeline_reservations']
    bridge.finish(rid, {'status': 'complete', 'usage': {'prompt_tokens': 7, 'completion_tokens': 5, 'total_tokens': 1}, 'cost': 0.002})
    bridge.finish(rid, {'status': 'complete', 'usage': {'prompt_tokens': 100}, 'cost': 1})
    job = agent_env.get(ctx.lease.job_id)
    assert job.usage['input_tokens'] == 7 and job.usage['total_tokens'] == 12 and job.usage['cost_usd'] == 0.002
    assert not job.runtime_ref['pipeline_reservations']


def test_budget_denial_commits_stop_even_with_atomic_reservation_wrapper(agent_env):
    bridge, ctx = admission(agent_env, {'model_calls': 1, 'tool_calls': 2, 'active_s': 30, 'output_tokens': 512})
    rid = bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'model', 'm')
    bridge.finish(rid, {'usage': {}, 'cost': None})
    with pytest.raises(AdmissionError, match='budget exhausted'):
        bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'model', 'm')
    job = agent_env.get(ctx.lease.job_id)
    assert job.status == 'stopped' and job.usage['model_calls'] == 1


def test_crash_pending_reservation_is_unknown_and_reconcile_is_idempotent(agent_env):
    bridge, ctx = admission(agent_env)
    bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'model', 'm')
    reconcile_accounting(agent_env.queue, {'id': ctx.lease.job_id})
    reconcile_accounting(agent_env.queue, {'id': ctx.lease.job_id})
    job = agent_env.get(ctx.lease.job_id)
    assert {'cost_usd', 'output_tokens', 'total_tokens'} <= set(job.usage['_unknown'])
    assert job.usage['model_calls'] == 1
    assert not job.runtime_ref['pipeline_reservations']
    ctx.limiter.release(ctx.lane)  # emulate the old process being gone, rather than calling its finish again


def test_revocation_denies_tools_but_late_spending_is_still_counted(agent_env):
    bridge, ctx = admission(agent_env)
    rid = bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'model', 'm')
    agent_env.queue.cancel(ctx.lease.job_id, reason='stop', actor='user:qualification')
    bridge.finish(rid, {'usage': {'prompt_tokens': 7, 'completion_tokens': 5}, 'cost': 0.01})
    with pytest.raises(AdmissionError):
        bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'tool', 'patch_file')
    job = agent_env.get(ctx.lease.job_id)
    assert job.status == 'cancelled' and job.usage['cost_usd'] == 0.01


@pytest.mark.parametrize('usage', [None, {'prompt_tokens': 'bad'}, {'completion_tokens': float('inf')}, {'total_tokens': -1}])
def test_malformed_or_missing_usage_is_unknown(agent_env, usage):
    bridge, ctx = admission(agent_env)
    rid = bridge.reserve(ctx.lease.job_id, ctx.lease.generation, 'model', 'm')
    bridge.finish(rid, {'usage': usage, 'cost': None})
    assert 'cost_usd' in agent_env.get(ctx.lease.job_id).usage['_unknown']
