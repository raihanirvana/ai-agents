"""Review context limits; model responses are explicitly FAKE contract evidence."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agents import ContextRefused, ContextTooLarge
from app.pipeline.contracts import Review
from app.pipeline.runtime import PipelineRuntime
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.domain.conftest import SCOPE


def review_context(env, ticket=None):
    ticket = ticket or env.approved_ticket()
    ctx = env.ctx(env.job('technical-lead', 'technical_plan', ticket=ticket, stage='plan'))
    return ctx, env.queue.verify(ctx.lease)


def test_full_large_review_has_isolated_bounded_context_and_recorded_usage(agent_env):
    env = agent_env
    ctx, identity = review_context(env)
    diff = 'diff --git a/package-lock.json b/package-lock.json\n' + 'lockfile-record ' * 6000
    task = {'name': 'review', 'diff': diff, 'evidence': {'status': 'passed'}}
    with pytest.raises(ContextTooLarge, match='limit 8000'):
        env.runtime._ask(ctx, identity, task, Review)
    assert env.provider.requests == []
    env.script({'kind': 'review', 'accept': True, 'summary': 'Fake review only', 'findings': []})
    output, meta, snapshot = env.runtime._ask(
        ctx, identity, task, Review,
        context_limits=replace(env.builder.limits, total_tokens=32768))
    assert output.accept and meta['fake'] is True
    assert diff.replace('\n', '\\n') in env.provider.requests[0].user
    assert 'APPROVED by the user' in snapshot.user
    assert 8000 < snapshot.estimated_tokens <= 32768
    assert snapshot.manifest['limit_tokens'] == 32768
    assert env.builder.limits.total_tokens == 8000
    job = env.get(ctx.lease.job_id)
    assert job.context_artifact_id == snapshot.artifact_id
    assert job.usage['model_calls'] == 1


def test_review_still_refuses_context_above_its_limit(agent_env):
    env = agent_env
    ctx, identity = review_context(env)
    with pytest.raises(ContextTooLarge, match='limit 32768'):
        env.runtime._ask(ctx, identity, {'name': 'review', 'diff': 'x' * 140000}, Review,
                         context_limits=replace(env.builder.limits, total_tokens=32768))
    assert env.provider.requests == []


def test_review_limit_does_not_override_scope_approval(agent_env):
    env = agent_env
    ctx, identity = review_context(env, env.world.new(SCOPE))
    with pytest.raises(ContextRefused, match='approved'):
        env.runtime._ask(ctx, identity, {'name': 'review'}, Review,
                         context_limits=replace(env.builder.limits, total_tokens=32768))
    assert env.provider.requests == []


@pytest.mark.parametrize('error', [ContextTooLarge('oversized diff'), ContextRefused('scope refused')])
def test_pipeline_context_failure_is_permanent_without_model_call(agent_env, monkeypatch, error):
    env = agent_env
    ctx, identity = review_context(env)
    runtime = PipelineRuntime(env.runtime, SimpleNamespace(store=env.store), None)

    def refuse(*args):
        raise error

    monkeypatch.setattr(runtime, '_technical_plan', refuse)
    outcome = runtime.run(ctx)
    assert outcome.status == 'failed' and outcome.retryable is False
    assert 'pipeline context refused' in outcome.error
    assert env.provider.requests == []
