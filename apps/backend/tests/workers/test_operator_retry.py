"""Explicit retry uses real queue state and a labelled fake runtime, never model/QA evidence."""
import pytest
from app.persistence.models import Job
from app.workers.queue import QueueError, StaleLease
from tests.workers.test_queue import claim


def failed(env):
    j = env.enqueue()
    lease = claim(env)
    env.queue.reserve(lease, 'model')
    env.queue.fail(lease, error='operator must fix config', retryable=False)
    return j, lease


def test_operator_retry_is_idempotent_preserves_root_budget_and_rejects_old_lease(env):
    j, lease = failed(env)
    rid = env.queue.retry_failed(j.id, user='user:operator', authorization_id='fixed-config')
    assert env.queue.retry_failed(j.id, user='user:operator', authorization_id='fixed-config') == rid
    with env.db.read() as s:
        old, new = s.get(Job, j.id), s.get(Job, rid)
        assert old.limits == new.limits and new.parent_job_id == old.id and new.attempt == old.attempt+1
        assert env.queue.budget_usage(s, new)['model_calls'] == 1 and new.runtime_ref['fake']
    with pytest.raises(StaleLease):
        env.queue.complete(lease, {})
    fresh = claim(env, owner='new-worker')
    assert fresh.job_id == rid
    with pytest.raises(QueueError, match='already has a retry'):
        env.queue.retry_failed(j.id, user='user:operator', authorization_id='another')


def test_agents_and_exhausted_budgets_cannot_use_operator_retry(env):
    j, _ = failed(env)
    with pytest.raises(QueueError, match='user authorization'):
        env.queue.retry_failed(j.id, user='agent:developer', authorization_id='x')
    with env.db.write() as s:
        old = s.get(Job, j.id)
        old.usage = {'model_calls': old.limits['model_calls']}
    with pytest.raises(QueueError, match='exhausted budget'):
        env.queue.retry_failed(j.id, user='user:operator', authorization_id='x')


def test_cleanup_and_another_users_authorization_cannot_be_bypassed(env):
    j, _ = failed(env)
    rid = env.queue.retry_failed(j.id, user='user:first', authorization_id='fixed')
    with pytest.raises(QueueError, match='different user'):
        env.queue.retry_failed(j.id, user='user:second', authorization_id='fixed')
    with env.db.write() as s:
        old = s.get(Job, j.id)
        old.runtime_ref = {**old.runtime_ref, 'cleanup': {'generation': 1}}
    with pytest.raises(QueueError, match='completed cleanup'):
        env.queue.retry_failed(j.id, user='user:first', authorization_id='fixed')
