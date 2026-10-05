"""Queue contract: claims, leases, generations, retries, budgets and waiting states."""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from app.persistence import scope_usage
from app.persistence.models import Job, Message
from app.workers import BudgetExhausted, Lease, QuotaWait, StaleLease
from app.workers.queue import QueueError

from .conftest import LIMITS

RT = ("fake",)


def claim(env, owner="w1", lane="execution", capacity=1):
    return env.queue.claim(owner, lane, capacity=capacity, runtimes=RT)


@pytest.mark.parametrize("limits", [{}, {"model_calls": 1, "tool_calls": 1},
                                    {**LIMITS, "model_calls": float("inf")}, {**LIMITS, "tool_calls": 0},
                                    {**LIMITS, "model_calls": True}, {**LIMITS, "active_s": -1},
                                    {**LIMITS, "model_calls": 2.5}, {**LIMITS, "surprise": 1}])
def test_every_job_needs_finite_positive_limits(env, limits):
    with pytest.raises(ValueError):
        env.enqueue(limits=limits)


def test_enqueue_is_idempotent_and_fake_is_labelled(env):
    first = env.enqueue(key="k1")
    assert env.enqueue(key="k1").id == first.id
    with pytest.raises(QueueError):
        env.enqueue(key="k1", lane="interactive")
    assert first.runtime_ref["fake"] is True and first.runtime_ref["runtime"] == "fake"
    [event] = env.events(first.id, "enqueued")
    assert event.payload["fake"] is True and event.payload["runtime"] == "fake"


def test_two_claimers_never_take_the_same_job(env):
    jobs = [env.enqueue(lane="interactive") for _ in range(12)]
    claimed, lock = [], threading.Lock()

    def worker(n):
        while True:
            lease = claim(env, owner=f"w{n}", lane="interactive", capacity=100)
            if lease is None:
                return
            with lock:
                claimed.append(lease.job_id)

    pool = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in pool]
    [t.join(30) for t in pool]
    assert sorted(claimed) == sorted(j.id for j in jobs)  # every job exactly once


def test_single_execution_slot_is_counted_in_the_database(env):
    for _ in range(5):
        env.enqueue(lane="execution")
    leases, lock = [], threading.Lock()

    def worker(n):
        lease = claim(env, owner=f"w{n}")
        with lock:
            leases.append(lease)

    pool = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in pool]
    [t.join(30) for t in pool]
    assert len([lease for lease in leases if lease]) == 1  # separate workers, one slot


def test_claim_honours_runtime_and_available_at(clocked, clock):
    job = clocked.enqueue(runtime="hermes")
    assert claim(clocked) is None  # this worker has no hermes runtime
    assert clocked.queue.claim("w", "execution", capacity=1, runtimes=("hermes",)).job_id == job.id


def test_stale_generation_cannot_change_anything(env):
    env.enqueue()
    lease = claim(env)
    env.queue.cancel(lease.job_id, reason="user cancelled", actor="user:local")
    for action in (lambda: env.queue.complete(lease, {"done": True}),
                   lambda: env.queue.fail(lease, "x", retryable=True),
                   lambda: env.queue.reserve(lease, "tool"),
                   lambda: env.queue.request_input(lease, question="?", checkpoint={}, request_key="q"),
                   lambda: env.queue.register_process(lease, 1234, "tag"),
                   lambda: env.queue.release(lease, "x")):
        with pytest.raises(StaleLease):
            action()
    assert env.queue.heartbeat(lease, 1) == "revoked"
    job = env.job(lease.job_id)
    assert job.status == "cancelled" and job.lease_generation == lease.generation + 1
    assert job.usage.get("tool_calls") is None
    [event] = env.events(lease.job_id, "cancellation_requested")
    assert event.payload["reason"] == "user cancelled"


def test_borrowed_or_forged_lease_is_rejected(env):
    env.enqueue()
    lease = claim(env)
    for forged in (Lease(lease.job_id, "intruder", lease.generation), Lease(lease.job_id, lease.owner, lease.generation + 1)):
        with pytest.raises(StaleLease):
            env.queue.complete(forged, {})
    env.queue.complete(lease, {"ok": True})
    assert env.job(lease.job_id).status == "succeeded"


def test_expired_lease_is_fenced_then_retried_once_then_needs_human(clocked, clock):
    job = clocked.enqueue()
    first = claim(clocked, owner="dead-worker")
    clock.advance(31)
    with pytest.raises(StaleLease):  # the late owner can no longer act
        clocked.queue.complete(first, {})
    assert clocked.queue.expired() == [job.id]
    retry_id = clocked.queue.recover(job.id, lambda snapshot: True, actor="worker:new")
    old, retry = clocked.job(job.id), clocked.job(retry_id)
    assert (old.status, old.result["reason"], old.lease_generation) == ("failed", "lease_expired", first.generation + 1)
    assert (retry.status, retry.attempt, retry.parent_job_id) == ("queued", 2, job.id)
    second = claim(clocked, owner="also-dead")
    assert second.job_id == retry_id
    clock.advance(31)
    assert clocked.queue.recover(retry_id, lambda snapshot: True, actor="worker:new") is None
    assert clocked.job(retry_id).result["needs_human"] is True  # bounded: one transient retry
    assert clocked.events(retry_id, "needs_human")


def test_unverifiable_leftover_processes_block_the_retry(clocked, clock):
    job = clocked.enqueue()
    claim(clocked, owner="dead-worker")
    clock.advance(31)
    assert clocked.queue.recover(job.id, lambda snapshot: False, actor="worker:new") is None
    assert clocked.job(job.id).result["needs_human"] is True


def test_recovery_ignores_jobs_waiting_for_input(clocked, clock):
    clocked.enqueue()
    lease = claim(clocked)
    clocked.queue.request_input(lease, question="Remove the row at zero?", checkpoint={"sha": "a" * 40},
                                request_key="q1")
    clock.advance(3600)
    assert clocked.queue.expired() == []  # no lease to expire, never re-run without an answer
    assert clocked.job(lease.job_id).status == "waiting_input"


def test_input_request_and_checkpoint_are_persisted_before_the_slot_is_released(env):
    job = env.enqueue()
    lease = claim(env)
    other = env.enqueue()
    assert claim(env, owner="w2") is None  # slot busy
    request_id = env.queue.request_input(lease, question="Quantity zero?", checkpoint={"sha": "c" * 40},
                                         request_key="q1")
    with env.db.read() as s:
        request = s.get(Message, request_id)
        stored = s.get(Job, job.id)
        assert request.kind == "input_request" and request.meta["checkpoint"] == {"sha": "c" * 40}
        assert request.meta["generation"] == lease.generation
        assert (stored.status, stored.waiting_request_id, stored.lease_owner) == ("waiting_input", request_id, None)
    assert claim(env, owner="w2").job_id == other.id  # waiting released the execution slot


def test_answer_resumes_exactly_once_with_a_new_generation(env):
    job = env.enqueue()
    lease = claim(env)
    request_id = env.queue.request_input(lease, question="?", checkpoint={}, request_key="q1")
    assert env.queue.answer(request_id, body="remove the row", answer_key="a1", user="user:local")[1] is True
    assert env.queue.answer(request_id, body="remove the row", answer_key="a1", user="user:local")[1] is False
    assert len(env.events(job.id, "resumed")) == 1
    resumed = claim(env)
    assert resumed.job_id == job.id and resumed.generation == lease.generation + 1
    with pytest.raises(StaleLease):  # the attempt that asked can never act again
        env.queue.complete(lease, {})


def test_answer_after_scope_change_cancels_instead_of_resuming(env):
    from tests.domain.conftest import World
    world = World(env.db, env.store)  # its own project, user and integrator actors
    ticket = world.new()
    job = env.queue.enqueue(project_id=world.project.id, lane="interactive", stage="po", role="po",
                            idempotency_key="po-1", limits=LIMITS, runtime="fake", ticket_id=ticket.id)
    lease = env.queue.claim("w1", "interactive", capacity=2, runtimes=RT)
    request_id = env.queue.request_input(lease, question="?", checkpoint={}, request_key="q")
    t = world.ticket(ticket.id)
    world.w.edit_scope(world.user, t.id, t.revision, {"title": "Changed", "uac": [{"id": "U9", "text": "new"}]})
    assert env.job(job.id).status == "cancelled"  # the domain revoked the waiting attempt
    assert env.queue.answer(request_id, body="late answer", answer_key="a", user="user:local")[1] is False
    assert env.job(job.id).status == "cancelled"


def test_budget_is_cumulative_across_retries_and_stops_at_the_cap(env):
    job = env.enqueue(limits={**LIMITS, "tool_calls": 3})
    lease = claim(env)
    env.queue.reserve(lease, "tool")
    env.queue.reserve(lease, "tool")
    retry_id = env.queue.fail(lease, "transient", retryable=True)
    retry = claim(env)
    assert retry.job_id == retry_id
    env.queue.reserve(retry, "tool")  # third call overall
    with pytest.raises(BudgetExhausted):
        env.queue.reserve(retry, "tool")  # the retry did NOT get a fresh budget
    stopped = env.job(retry_id)
    assert (stopped.status, stopped.result["reason"], stopped.result["needs_human"]) == ("stopped", "budget_exhausted", True)
    assert env.job(job.id).usage["tool_calls"] + stopped.usage["tool_calls"] == 3
    with pytest.raises(StaleLease):
        env.queue.reserve(retry, "tool")


def test_extending_a_budget_needs_a_user_decision_and_keeps_usage(env):
    env.enqueue(limits={**LIMITS, "tool_calls": 1})
    lease = claim(env)
    env.queue.reserve(lease, "tool")
    with pytest.raises(BudgetExhausted):
        env.queue.reserve(lease, "tool")
    with pytest.raises(QueueError):
        env.queue.extend_budget(lease.job_id, user="agent:po", additions={"tool_calls": 2}, authorization_id="a1")
    extended = env.queue.extend_budget(lease.job_id, user="user:local", additions={"tool_calls": 2}, authorization_id="a1")
    assert env.queue.extend_budget(lease.job_id, user="user:local", additions={"tool_calls": 2},
                                   authorization_id="a1") == extended  # idempotent decision
    new = claim(env)
    assert new.job_id == extended
    env.queue.reserve(new, "tool")
    env.queue.reserve(new, "tool")
    with pytest.raises(BudgetExhausted):
        env.queue.reserve(new, "tool")  # 1 + 2 extra = 3 total


def test_usage_per_ticket_scope_survives_retries(env):
    from tests.domain.conftest import World
    world = World(env.db, env.store)
    ticket = world.new()
    env.queue.enqueue(project_id=world.project.id, lane="interactive", stage="po", role="po",
                      idempotency_key="po-usage", limits=LIMITS, runtime="fake", ticket_id=ticket.id)
    lease = env.queue.claim("w1", "interactive", capacity=2, runtimes=RT)
    env.queue.reserve(lease, "model")
    env.queue.finalize_usage(lease.job_id, lease.generation, {"output_tokens": 40, "cost_usd": None})
    env.queue.fail(lease, "timeout", retryable=True)
    retry = env.queue.claim("w1", "interactive", capacity=2, runtimes=RT)
    env.queue.reserve(retry, "model")
    env.queue.finalize_usage(retry.job_id, retry.generation, {"output_tokens": 10})
    with env.db.read() as s:
        usage = scope_usage(s, ticket.id, 1)
    assert usage["totals"] == {"model_calls": 2, "output_tokens": 50, "total_tokens": 50} and usage["jobs"] == 2
    assert usage["unknown"] == ["cost_usd", "total_tokens"]  # partial token total remains unknown


def test_active_time_cap_stops_the_job(env):
    env.enqueue(limits={**LIMITS, "active_s": 2})
    lease = claim(env)
    assert env.queue.heartbeat(lease, 1.5) == "ok"
    assert env.queue.heartbeat(lease, 1.0) == "budget_exhausted"
    assert env.job(lease.job_id).result["limit"] == "active_s"
    assert env.queue.heartbeat(lease, 0.1) == "revoked"


def test_quota_wait_is_visible_and_promoted_after_retry_time(clocked, clock):
    job = clocked.enqueue()
    lease = claim(clocked)
    clocked.queue.wait_quota(lease, QuotaWait(clock() + timedelta(seconds=60), "provider 429"))
    waiting = clocked.job(job.id)
    assert waiting.status == "waiting_quota" and waiting.result["quota"]["reason"] == "provider 429"
    assert claim(clocked) is None and clocked.queue.promote_quota_waiters() == []
    clock.advance(61)
    assert clocked.queue.promote_quota_waiters() == [job.id]
    assert claim(clocked).job_id == job.id


def test_release_returns_the_job_with_a_new_generation(env):
    job = env.enqueue()
    lease = claim(env)
    env.queue.release(lease, "worker_shutdown")
    again = claim(env)
    assert again.job_id == job.id and again.generation > lease.generation
