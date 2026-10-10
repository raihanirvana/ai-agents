"""Phase metrics are buffered observations: no write per call, merged into the heartbeat.

Runtime is the labelled fake queue harness; nothing here is provider or QA evidence.
"""
import pytest
from sqlalchemy import func, select

from app.persistence.models import Job, Message
from app.workers.runtime import RunContext
from tests.workers.conftest import LIMITS
from tests.workers.test_queue import claim


def context(env):
    job = env.enqueue()
    lease = claim(env)
    ctx = RunContext(queue=env.queue, limiter=env.limiter, lease=lease,
                     job={'id': job.id, 'lane': 'execution', 'limits': LIMITS, 'project_id': env.project.id,
                          'runtime_ref': {'role': 'developer', 'runtime': 'fake'}})
    return job, lease, ctx


def messages(env):
    with env.db.read() as s:
        return s.scalar(select(func.count()).select_from(Message))


def phases(env, job_id):
    return (env.job(job_id).runtime_ref.get('telemetry') or {}).get('phases', {})


def test_metrics_are_buffered_then_merged_into_heartbeat(env):
    job, lease, ctx = context(env)
    before = messages(env)
    for _ in range(3):
        ctx.record_phase({'phase': 'tool', 'duration_s': 0.5, 'status': 'passed', 'cache_hit': False})
    ctx.record_phase({'phase': 'model', 'duration_s': 2.0, 'status': 'failed', 'cache_hit': False})
    assert messages(env) == before and not {'tool', 'model'} & set(phases(env, job.id))
    assert env.queue.heartbeat(lease, 0.1, metrics=ctx.take_metrics()) == 'ok'
    saved = phases(env, job.id)
    assert saved['tool']['count'] == 3 and saved['tool']['total_s'] == 1.5
    assert saved['model'] == {**saved['model'], 'count': 1, 'failed': 1}
    assert messages(env) == before and ctx.take_metrics() == []


def test_invalid_metric_is_rejected_before_it_can_break_a_heartbeat(env):
    _, _, ctx = context(env)
    for bad in ({'phase': 'unknown', 'duration_s': 1}, {'phase': 'tool', 'duration_s': float('nan')},
                {'phase': 'tool', 'duration_s': -1}):
        with pytest.raises(ValueError):
            ctx.record_phase(bad)
    assert ctx.take_metrics() == []


def test_revoked_heartbeat_keeps_metrics_for_the_final_flush(env):
    job, lease, ctx = context(env)
    ctx.record_phase({'phase': 'test', 'duration_s': 4, 'status': 'passed', 'cache_hit': False})
    env.queue.cancel(job.id, reason='user', actor='user:test')
    metrics = ctx.take_metrics()
    assert env.queue.heartbeat(lease, 0.1, metrics=metrics) == 'revoked'
    ctx.restore_metrics(metrics)
    assert 'test' not in phases(env, job.id)
    # Like logs, observations of a revoked attempt remain accepted for its own generation.
    assert ctx.flush_metrics() is True
    assert phases(env, job.id)['test']['count'] == 1


def test_failed_flush_restores_buffer_and_buffer_stays_bounded(env, monkeypatch):
    job, _, ctx = context(env)
    ctx.record_phase({'phase': 'build', 'duration_s': 1, 'status': 'passed', 'cache_hit': False})
    def broken(*args):
        raise RuntimeError('database locked')
    monkeypatch.setattr(env.queue, 'record_metrics', broken)
    assert ctx.flush_metrics() is False
    monkeypatch.undo()
    for _ in range(RunContext.MAX_BUFFERED_METRICS - 1):
        ctx.record_phase({'phase': 'tool', 'duration_s': 0, 'status': 'passed', 'cache_hit': False})
    # Reaching the bound flushes synchronously, preserving the earlier failed batch.
    assert ctx.take_metrics() == []
    saved = phases(env, job.id)
    assert saved['build']['count'] == 1 and saved['tool']['count'] == RunContext.MAX_BUFFERED_METRICS - 1


def test_supervised_run_persists_metrics_by_the_end_of_the_run(env):
    from app.workers.telemetry import record_phase
    class Measured(type(env.runtime)):
        def run(self, ctx):
            record_phase(ctx, 'install', 1.25)
            return super().run(ctx)
    job = env.enqueue()
    sup = env.supervisor(runtime=Measured())
    env.run_until(sup, lambda: env.job(job.id).status in ('succeeded', 'failed') and
                  'install' in phases(env, job.id))
    assert phases(env, job.id)['install']['total_s'] == 1.25


def test_persistent_database_failure_keeps_buffer_bounded_without_losing_counts(env, monkeypatch):
    job, _, ctx = context(env)
    original = env.queue.record_metrics
    def broken(*args):
        raise RuntimeError('database unavailable')
    monkeypatch.setattr(env.queue, 'record_metrics', broken)
    count = ctx.MAX_BUFFERED_METRICS * 4
    for _ in range(count):
        ctx.record_phase({'phase': 'tool', 'duration_s': 0.5, 'status': 'failed', 'cache_hit': True})
        assert len(ctx._metrics) < ctx.MAX_BUFFERED_METRICS
    monkeypatch.setattr(env.queue, 'record_metrics', original)
    assert ctx.flush_metrics()
    assert phases(env, job.id)['tool'] == {'count': count, 'total_s': count * 0.5,
        'max_s': 0.5, 'failed': count, 'cache_hits': count, 'last_generation': 1}
