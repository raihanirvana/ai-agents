"""Supervisor with real threads and the LABELLED fake runtime (no provider, no real QA)."""
from __future__ import annotations

import threading
import time

import pytest

from app.persistence.models import Artifact, Job
from app.workers import Outcome, QuotaWait

from .conftest import LIMITS


def done(env, job_id, *statuses):
    return lambda: env.job(job_id).status in statuses


def test_fake_job_completes_labelled_and_its_log_is_archived(env):
    sup = env.supervisor()
    job = env.enqueue([{"model": {"output_tokens": 12}}, {"tool": "read_file"}, {"finish": {"summary": "ok"}}])
    env.run_until(sup, done(env, job.id, "succeeded"))
    assert sup.wait_idle()
    finished = env.job(job.id)
    assert finished.result["fake_provider"] is True and finished.result["summary"] == "ok"
    assert (finished.usage["model_calls"], finished.usage["tool_calls"], finished.usage["output_tokens"]) == (1, 1, 12)
    assert all(e.payload["fake"] is True for e in env.events(job.id))
    [log_id] = finished.result["evidence_artifact_ids"]
    with env.db.read() as s:
        log = s.get(Artifact, log_id)
        assert log.kind == "log" and log.meta["fake"] is True and log.meta["end_state"] == "succeeded"
        assert b"FAKE runtime start" in env.store.read_bytes(s, log_id)


def test_interactive_work_is_served_while_a_long_execution_job_runs(env):
    sup = env.supervisor()
    long = env.enqueue([{"sleep": 3}, {"finish": {}}], lane="execution")
    env.run_until(sup, done(env, long.id, "running"))
    chat = env.enqueue([{"model": {"output_tokens": 3}}, {"finish": {"reply": "hi"}}], lane="interactive", role="po")
    env.run_until(sup, done(env, chat.id, "succeeded"))
    assert env.job(long.id).status == "running"  # the execution job is still going
    env.run_until(sup, done(env, long.id, "succeeded"))


def test_only_one_execution_job_runs_at_a_time(env):
    sup = env.supervisor()
    first = env.enqueue([{"sleep": 0.6}, {"finish": {}}])
    second = env.enqueue([{"finish": {}}])
    env.run_until(sup, done(env, first.id, "running"))
    for _ in range(10):
        sup.tick()
        assert env.job(second.id).status == "queued"
        time.sleep(0.02)
    env.run_until(sup, done(env, second.id, "succeeded"))
    assert env.job(first.id).finished_at <= env.job(second.id).started_at


def test_cancel_while_a_tool_is_active_revokes_then_stops_and_keeps_the_log(env):
    sup = env.supervisor()
    job = env.enqueue([{"tool": "run_tests"}, {"sleep": 30}, {"finish": {"should": "never happen"}}])
    env.run_until(sup, lambda: (env.job(job.id).usage or {}).get("tool_calls") == 1)
    env.queue.cancel(job.id, reason="user cancelled", actor="user:local")
    started = time.monotonic()
    env.run_until(sup, lambda: sup.running() == 0, 5)  # the heartbeat notices the revocation
    assert time.monotonic() - started < 5
    stopped = env.job(job.id)
    assert stopped.status == "cancelled" and "should" not in (stopped.result or {})
    [log_id] = stopped.result["evidence_artifact_ids"]
    with env.db.read() as s:
        assert s.get(Artifact, log_id).meta["end_state"] == "revoked"


class StubbornRuntime:
    """Ignores cancellation and tries to report success after its attempt was revoked."""
    name = "fake"

    def __init__(self, env):
        self.env, self.revoked = env, threading.Event()

    def run(self, ctx):
        while self.env.job(ctx.lease.job_id).status == "running":
            time.sleep(0.02)
        self.revoked.set()
        return Outcome("succeeded", {"late": True})

    def stop(self, ctx):
        pass  # does not cooperate


def test_a_revoked_attempts_late_result_is_rejected(env):
    runtime = StubbornRuntime(env)
    sup = env.supervisor(runtime=runtime)
    job = env.enqueue([])
    env.run_until(sup, done(env, job.id, "running"))
    env.queue.cancel(job.id, reason="scope changed", actor="user:local")
    assert runtime.revoked.wait(5) and sup.wait_idle(5)
    final = env.job(job.id)
    assert final.status == "cancelled" and "late" not in (final.result or {})


def test_fake_model_that_keeps_calling_tools_stops_at_the_cap(env):
    sup = env.supervisor()
    job = env.enqueue([{"loop_tools": True}], limits={**LIMITS, "tool_calls": 7})
    env.run_until(sup, done(env, job.id, "stopped"))
    stopped = env.job(job.id)
    assert stopped.usage["tool_calls"] == 7
    assert (stopped.result["reason"], stopped.result["limit"], stopped.result["needs_human"]) == \
        ("budget_exhausted", "tool_calls", True)
    # A user-approved extension continues from the usage already spent.
    extra = env.queue.extend_budget(job.id, user="user:local", additions={"tool_calls": 3}, authorization_id="ok-1")
    env.run_until(sup, done(env, extra, "stopped"))
    assert env.job(extra).usage["tool_calls"] == 3  # 7 + 3 = the new cap of 10


def test_waiting_for_input_survives_a_restart_and_resumes_once(env):
    sup = env.supervisor(worker_id="worker:first")
    job = env.enqueue([{"tool": "inspect"}, {"ask": {"question": "Remove the row at zero?", "key": "q1",
                                                     "checkpoint": {"sha": "c" * 40}}}],
                      resume_script=[{"finish": {"resumed": True}}])
    env.run_until(sup, done(env, job.id, "waiting_input"))
    asked = env.job(job.id)
    sup.shutdown()

    restarted = env.supervisor(worker_id="worker:second")  # a new process after a restart
    for _ in range(10):
        restarted.tick()
        time.sleep(0.02)
    assert env.job(job.id).status == "waiting_input"  # never re-run without an answer
    env.queue.answer(asked.waiting_request_id, body="remove the row", answer_key="a1", user="user:local")
    env.queue.answer(asked.waiting_request_id, body="remove the row", answer_key="a1", user="user:local")
    env.run_until(restarted, done(env, job.id, "succeeded"))
    final = env.job(job.id)
    assert final.result["answer"] == "remove the row" and final.result["resumed"] is True
    assert final.lease_generation > asked.lease_generation
    assert len(env.events(job.id, "resumed")) == 1 and len(env.events(job.id, "claimed")) == 2
    assert final.usage["tool_calls"] == 1  # usage of the first generation is kept


def test_shared_provider_quota_puts_every_affected_job_in_waiting_quota(env):
    sup = env.supervisor()
    hit = env.enqueue([{"quota": 120}], lane="interactive", role="po")
    env.run_until(sup, done(env, hit.id, "waiting_quota"))
    other = env.enqueue([{"model": {"output_tokens": 1}}, {"finish": {}}], lane="interactive", role="po")
    env.run_until(sup, done(env, other.id, "waiting_quota"))
    for job_id in (hit.id, other.id):
        waiting = env.job(job_id)
        assert waiting.result["quota"]["retry_at"] and "429" in waiting.result["quota"]["reason"]
        assert env.events(job_id, "waiting_quota")
    assert env.limiter.status()["blocked_until"] is not None


def test_interactive_capacity_is_reserved_in_the_provider_limiter(env):
    limiter = env.limiter  # 3 concurrent requests, 1 reserved for interactive work
    limiter.acquire("execution")
    limiter.acquire("execution")
    with pytest.raises(QuotaWait):
        limiter.acquire("execution", timeout_s=0.05)  # execution may not take the reserved slot
    limiter.acquire("interactive", timeout_s=0.05)  # chat still gets an answer
    for lane in ("execution", "execution", "interactive"):
        limiter.release(lane)


def test_a_crashing_runtime_is_retried_once_and_usage_accumulates(env):
    sup = env.supervisor()
    job = env.enqueue([{"tool": "t1"}, {"crash": "segfault-like failure"}])
    env.run_until(sup, lambda: env.job(job.id).status == "failed" and (env.job(job.id).result or {}).get("retry_job_id"))
    retry_id = env.job(job.id).result["retry_job_id"]
    env.run_until(sup, lambda: env.job(retry_id).status == "failed" and
                  (env.job(retry_id).result or {}).get("needs_human") is True)
    retry = env.job(retry_id)
    assert retry.attempt == 2 and retry.result["needs_human"] is True  # bounded
    assert env.job(job.id).usage["tool_calls"] + retry.usage["tool_calls"] == 2


def test_failed_provider_call_is_counted_and_its_usage_is_unknown(env):
    class Broken:
        name = "fake"

        def run(self, ctx):
            def call(max_tokens):
                raise ConnectionError("provider dropped the connection")
            ctx.model_call(call)

        def stop(self, ctx):
            ctx.cancelled.set()

    sup = env.supervisor(runtime=Broken())
    job = env.enqueue([])
    env.run_until(sup, done(env, job.id, "failed"))
    usage = env.job(job.id).usage
    assert usage["model_calls"] == 1 and set(usage["_unknown"]) >= {"output_tokens", "cost_usd"}


def test_shutdown_gives_the_job_back_with_a_new_generation(env):
    sup = env.supervisor()
    job = env.enqueue([{"sleep": 30}, {"finish": {}}])
    env.run_until(sup, done(env, job.id, "running"))
    before = env.job(job.id).lease_generation
    sup.shutdown()
    released = env.job(job.id)
    assert released.status == "queued" and released.lease_generation > before
    assert env.events(job.id, "released")[-1].payload["reason"] == "worker_shutdown"


def test_ticket_work_starts_only_when_the_domain_says_it_is_eligible(env):
    from tests.domain.conftest import World
    world = World(env.db, env.store)
    ticket = world.new()  # scope_review: not approved yet
    job = env.queue.enqueue(project_id=world.project.id, lane="execution", stage="development",
                            role="developer", idempotency_key="dev-1", limits=LIMITS, runtime="fake",
                            ticket_id=ticket.id, payload={"script": [{"sleep": 30}]})
    sup = env.supervisor()
    for _ in range(5):
        sup.tick()
    assert env.job(job.id).status == "queued"
    world.approve(ticket)
    env.run_until(sup, done(env, job.id, "running"))
    running = env.job(job.id)
    bound = world.ticket(ticket.id).workflow["attempts"]["development"]
    assert bound == {"job_id": job.id, "generation": running.lease_generation, "scope_version": 1}
    # Changing the scope revokes the attempt through the domain; the supervisor stops the run.
    t = world.ticket(ticket.id)
    world.w.edit_scope(world.user, t.id, t.revision, {"title": "Changed", "uac": [{"id": "U2", "text": "x"}]})
    env.run_until(sup, lambda: sup.running() == 0, 5)  # the heartbeat notices the revocation
    assert env.job(job.id).status == "cancelled"
