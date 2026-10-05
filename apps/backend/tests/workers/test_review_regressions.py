"""DEV-004 review: real DB transactions, controlled runtime interleavings, durable evidence.

The provider is fake. These checks do not certify Hermes or real QA execution.
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import select

from app.persistence import pin_owners
from app.persistence.models import Job, Message
from app.workers import ProviderLimiter, QuotaWait, StaleLease, WorkerConfig
from app.workers.queue import QueueError
from app.workers.runtime import Cancelled, Outcome, RunContext, reap_recorded_processes

from .conftest import LIMITS


def claim(env, owner="other"):
    return env.queue.claim(owner, "execution", capacity=1, runtimes=("fake",))


class CleanupRuntime:
    """A resource remains active after the runtime returns its outcome."""
    name = "fake"

    def __init__(self, outcome="input"):
        self.outcome = outcome
        self.cleaning = threading.Event()
        self.allow_cleanup = threading.Event()

    def run(self, ctx):
        def cleanup():
            self.cleaning.set()
            if not self.allow_cleanup.wait(5):
                raise TimeoutError("test cleanup was not released")
        ctx.add_stopper(cleanup)
        if self.outcome == "input":
            ctx.request_input("?", {}, "q")
        if self.outcome == "failed":
            return Outcome("failed", error="transient", retryable=True)
        return Outcome("succeeded")

    def stop(self, ctx):
        ctx.cancelled.set()
        ctx.stop_resources()


@pytest.mark.parametrize("outcome", ["input", "failed", "succeeded"])
def test_other_worker_cannot_claim_until_old_resources_are_cleaned(env, outcome):
    runtime = CleanupRuntime(outcome)
    sup = env.supervisor(runtime=runtime)
    first = env.enqueue()
    sup.tick()
    assert runtime.cleaning.wait(2)
    second = env.enqueue()
    try:
        assert claim(env) is None  # DB lease may be released, physical execution slot is still owned
        if outcome == "failed":
            assert not env.job(first.id).result.get("retry_job_id")
    finally:
        runtime.allow_cleanup.set()
        assert sup.wait_idle(5)
    assert env.job(first.id).result["evidence_artifact_ids"]
    assert claim(env) is not None


def test_answer_cannot_start_a_new_generation_before_old_cleanup(env):
    runtime = CleanupRuntime()
    sup = env.supervisor(runtime=runtime)
    job = env.enqueue()
    sup.tick()
    assert runtime.cleaning.wait(2)
    waiting = env.job(job.id)
    env.queue.answer(waiting.waiting_request_id, body="yes", answer_key="a", user="user:local")
    try:
        assert claim(env) is None
    finally:
        runtime.allow_cleanup.set()
        assert sup.wait_idle(5)
    resumed = claim(env)
    assert resumed.job_id == job.id and resumed.generation > waiting.lease_generation


def test_recovery_can_resume_after_crash_between_fence_and_reap(clocked, clock):
    job = clocked.enqueue()
    claim(clocked)
    clock.advance(31)
    def crash(snapshot):
        raise SystemExit("simulated process death after fencing")
    with pytest.raises(SystemExit):
        clocked.queue.recover(job.id, crash, actor="recoverer:one")
    clock.advance(31)
    assert job.id in clocked.queue.expired()
    retry = clocked.queue.recover(job.id, lambda _: True, actor="recoverer:two")
    assert clocked.job(retry).parent_job_id == job.id
    assert clocked.queue.recover(job.id, lambda _: True, actor="recoverer:three") is None
    with clocked.db.read() as s:
        assert len(s.scalars(select(Job).where(Job.parent_job_id == job.id)).all()) == 1


def test_failed_reconciliation_keeps_slot_blocked(clocked, clock):
    job = clocked.enqueue()
    claim(clocked)
    clock.advance(31)
    clocked.queue.recover(job.id, lambda _: False, actor="recoverer")
    clocked.enqueue()
    assert claim(clocked) is None
    assert clocked.job(job.id).result["needs_human"]


def test_shutdown_revokes_before_runtime_stop(env):
    observed = []
    class Runtime:
        def run(self, ctx):
            ctx.cancelled.wait(5)
            return Outcome("succeeded", {"late": True})
        def stop(self, ctx):
            observed.append(env.job(ctx.lease.job_id).status)
            observed.append(env.queue.heartbeat(ctx.lease, 0))
            ctx.cancelled.set()
    sup = env.supervisor(runtime=Runtime())
    job = env.enqueue()
    sup.tick()
    sup.shutdown()
    assert observed == ["queued", "revoked"]
    assert env.job(job.id).status == "queued" and not (env.job(job.id).result or {}).get("late")


def test_slow_stop_does_not_block_interactive_claims(env):
    entered, unblock = threading.Event(), threading.Event()
    class Runtime:
        def run(self, ctx):
            if ctx.lane == "interactive":
                return Outcome("succeeded")
            ctx.cancelled.wait(5)
            return Outcome("succeeded")
        def stop(self, ctx):
            entered.set()
            unblock.wait(3)
            ctx.cancelled.set()
    sup = env.supervisor(runtime=Runtime())
    heavy = env.enqueue()
    sup.tick()
    env.queue.cancel(heavy.id, reason="cancel", actor="user:local")
    chat = env.enqueue(lane="interactive")
    timer = threading.Timer(1.5, unblock.set)
    timer.start()
    try:
        started = time.monotonic()
        sup.heartbeat()
        sup.tick()
        assert time.monotonic() - started < 1
        assert entered.wait(1)
        assert env.run_until(sup, lambda: env.job(chat.id).status == "succeeded", 1)
    finally:
        unblock.set()
        timer.cancel()
        assert sup.wait_idle(5)


@pytest.mark.parametrize("crash", [False, True])
def test_waiting_and_crashing_runtime_charge_final_active_time(env, crash):
    class Runtime:
        def run(self, ctx):
            time.sleep(0.08)
            if crash:
                raise RuntimeError("crashed before first heartbeat")
            ctx.request_input("?", {}, "q")
        def stop(self, ctx):
            ctx.cancelled.set()
    sup = env.supervisor(runtime=Runtime())
    job = env.enqueue()
    sup.tick()  # no subsequent heartbeat: the final interval must still be charged
    assert sup.wait_idle(5)
    assert env.job(job.id).usage["active_s"] >= 0.07


@pytest.mark.parametrize("usage", [{"input_tokens": 90, "output_tokens": 10},
                                   {"total_tokens": 100, "output_tokens": 10}])
def test_total_token_cap_includes_input_tokens(env, usage):
    job = env.enqueue(limits={**LIMITS, "total_tokens": 100})
    lease = claim(env)
    env.queue.finalize_usage(job.id, lease.generation, usage)
    assert env.job(job.id).status == "stopped"
    assert env.job(job.id).result["limit"] == "total_tokens"
    with pytest.raises(StaleLease):
        env.queue.reserve(lease, "model")


def test_late_token_usage_stops_current_retry_without_resetting_budget(env):
    job = env.enqueue(limits={**LIMITS, "total_tokens": 100})
    first = claim(env)
    retry_id = env.queue.fail(first, "transient", retryable=True)
    second = claim(env)
    env.queue.finalize_usage(job.id, first.generation, {"total_tokens": 100})
    assert env.job(retry_id).status == "stopped"
    with pytest.raises(StaleLease):
        env.queue.reserve(second, "tool")


def test_budget_extension_supports_tokens_and_rejects_changed_decision(env):
    job = env.enqueue(limits={**LIMITS, "total_tokens": 10})
    lease = claim(env)
    env.queue.finalize_usage(job.id, lease.generation, {"total_tokens": 10, "output_tokens": 10})
    new_id = env.queue.extend_budget(job.id, user="user:local", additions={"total_tokens": 20}, authorization_id="a")
    assert env.job(new_id).limits["total_tokens"] == 30
    assert claim(env).job_id == new_id
    with pytest.raises(QueueError):
        env.queue.extend_budget(job.id, user="user:local", additions={"total_tokens": 999}, authorization_id="a")


@pytest.mark.parametrize("change", [{"role": "qa"}, {"runtime": "hermes"},
                                    {"script": [{"tool": "delete_file"}]},
                                    {"limits": {**LIMITS, "tool_calls": 999}}])
def test_enqueue_rejects_idempotency_payload_changes(env, change):
    env.enqueue(key="same")
    with pytest.raises(QueueError):
        env.enqueue(key="same", **change)


def test_caller_cannot_reset_scope_budget_identity(env):
    with pytest.raises(QueueError):
        env.enqueue(limits={**LIMITS, "budget_key": "fresh-budget"})


def test_identical_job_keys_in_different_projects_do_not_share_usage(env):
    from tests.persistence import factories as f
    with env.db.write() as s:
        project = f.project(s)
    first = env.enqueue(key="same")
    lease = claim(env)
    env.queue.reserve(lease, "model")
    env.queue.complete(lease, {})
    second = env.queue.enqueue(project_id=project.id, lane="execution", stage="work", role="developer",
        runtime="fake", limits=LIMITS, idempotency_key="same")
    with env.db.read() as s:
        assert env.queue.budget_usage(s, second) == {}


def test_runtime_log_is_durable_before_cleanup_or_archival(env):
    job = env.enqueue()
    lease = claim(env)
    ctx = RunContext(queue=env.queue, limiter=env.limiter, lease=lease,
                     job={"lane": "execution", "limits": job.limits})
    ctx.log("durable before a worker crash")
    with env.db.read() as s:
        assert any("durable before a worker crash" in m.body for m in s.scalars(select(Message)))
    # A queue/harness caller that registers resources directly also keeps the physical slot.
    ctx.add_stopper(lambda: None)
    env.queue.fail(lease, "transient", retryable=True)
    env.enqueue()
    assert claim(env) is None
    assert ctx.stop_resources()
    assert env.queue.finish_cleanup(job.id, lease.generation)


def test_completed_run_log_is_pinned_against_cleanup(env):
    from datetime import timedelta
    from app.persistence import cleanup_unpinned
    job = env.enqueue()
    sup = env.supervisor()
    sup.tick()
    assert sup.wait_idle(5)
    [log_id] = env.job(job.id).result["evidence_artifact_ids"]
    with env.db.read() as s:
        assert pin_owners(s, log_id)
    report = cleanup_unpinned(env.db, env.store, project_id=env.project.id, min_age=timedelta(0), dry_run=False)
    assert log_id in report.kept_pinned and not report.removed


def test_unknown_process_ownership_is_not_treated_as_reconciled(monkeypatch):
    import app.workers.runtime as runtime
    monkeypatch.setattr(runtime.os.path, "isdir", lambda _: True)
    monkeypatch.setattr(runtime, "group_members", lambda _: [1234])
    monkeypatch.setattr(runtime, "process_tag", lambda _: None)
    assert not reap_recorded_processes({"runtime_ref": {"processes": [{"pgid": 1234, "tag": "job:1"}]}})


def test_resource_registered_after_stop_is_immediately_cleaned(env):
    job = env.enqueue()
    lease = claim(env)
    ctx = RunContext(queue=env.queue, limiter=env.limiter, lease=lease,
                     job={"lane": "execution", "limits": job.limits})
    ctx.stop_resources()
    stopped = []
    with pytest.raises(Cancelled):
        ctx.add_stopper(lambda: stopped.append(True))
    assert stopped == [True]


def test_execution_capacity_cannot_be_configured_above_one(env):
    env.enqueue()
    with pytest.raises(ValueError):
        env.queue.claim("worker", "execution", capacity=2, runtimes=("fake",))
    with pytest.raises(ValueError):
        WorkerConfig(execution_slots=2)


def test_quota_signal_wakes_requests_waiting_for_local_capacity():
    limiter = ProviderLimiter(max_concurrent=2, reserved_interactive=1)
    limiter.acquire("execution")
    waiting, errors = threading.Event(), []
    def call():
        waiting.set()
        try:
            limiter.acquire("execution", timeout_s=3)
        except QuotaWait as exc:
            errors.append(exc.reason)
    thread = threading.Thread(target=call)
    thread.start()
    try:
        assert waiting.wait(1)
        limiter.exhausted(60, "provider 429")
        thread.join(0.5)
        assert not thread.is_alive() and errors == ["provider 429"]
    finally:
        limiter.release("execution")
        thread.join(5)


def test_failed_resource_cleanup_does_not_schedule_retry_or_free_slot(env):
    class Runtime:
        def run(self, ctx):
            def broken():
                raise RuntimeError("resource is still running")
            ctx.add_stopper(broken)
            return Outcome("failed", error="transient", retryable=True)
        def stop(self, ctx):
            ctx.cancelled.set()
    job = env.enqueue()
    sup = env.supervisor(runtime=Runtime())
    sup.tick()
    assert sup.wait_idle(5)
    failed = env.job(job.id)
    assert failed.result["needs_human"] and not failed.result.get("retry_job_id")
    assert failed.runtime_ref["resources"] == [{"kind": "opaque", "generation": 1}]
    assert failed.result["evidence_artifact_ids"]
    env.enqueue()
    assert claim(env) is None


def test_archive_failure_is_visible_and_keeps_cleanup_pending(env, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(env.store, "put_bytes", broken)
    job = env.enqueue()
    sup = env.supervisor()
    sup.tick()
    assert sup.wait_idle(5)
    failed = env.job(job.id)
    assert failed.result["needs_human"] and failed.runtime_ref.get("cleanup")
    assert env.events(job.id, "cleanup_failed")
    env.enqueue()
    assert claim(env) is None


def test_same_scope_cannot_raise_caps_through_enqueue(env):
    from tests.domain.conftest import World
    world = World(env.db, env.store)
    ticket = world.new()
    args = dict(project_id=world.project.id, lane="interactive", stage="po", role="po",
                runtime="fake", ticket_id=ticket.id)
    env.queue.enqueue(**args, idempotency_key="first", limits=LIMITS)
    with pytest.raises(QueueError):
        env.queue.enqueue(**args, idempotency_key="second", limits={**LIMITS, "model_calls": 999})


def test_zero_addition_and_changed_authorization_are_rejected(env):
    job = env.enqueue(limits={**LIMITS, "active_s": 1})
    lease = claim(env)
    env.queue.heartbeat(lease, 2)
    with pytest.raises(QueueError):
        env.queue.extend_budget(job.id, user="user:local", additions={}, authorization_id="zero")
    env.queue.extend_budget(job.id, user="user:local", additions={"active_s": 2}, authorization_id="a")
    with pytest.raises(QueueError):
        env.queue.extend_budget(job.id, user="user:other", additions={"active_s": 2}, authorization_id="a")


def test_partial_token_report_keeps_unknown_total_and_enforces_known_lower_bound(env):
    job = env.enqueue(limits={**LIMITS, "total_tokens": 10})
    lease = claim(env)
    env.queue.register_resource(lease, {"kind": "opaque", "generation": lease.generation})
    env.queue.finalize_usage(job.id, lease.generation, {"output_tokens": 10})
    final = env.job(job.id)
    assert final.status == "stopped" and final.usage["total_tokens"] == 10
    assert "total_tokens" in final.usage["_unknown"]
    env.queue.cleanup_failed(job.id, "temporary archive failure")
    env.queue.finish_cleanup(job.id, lease.generation)
    assert env.job(job.id).result["needs_human"]  # cleaning resources does not authorize more budget
