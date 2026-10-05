"""Persisted threads, directed replies and input requests between roles, with real supervisor and domain."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.agents import Threads
from app.domain import Forbidden, Invalid
from app.persistence import AlreadyAnswered, NotFound
from app.persistence.models import Job, Message
from app.workers.runtime import WaitingForInput
from app.workers.queue import StaleLease
from tests.domain.conftest import SCOPE

from .conftest import LIMITS

LEAD_ANSWER = {"kind": "answer", "outcome": "proceed", "answer": "Yes, remove the row.", "rationale": "Matches UAC-1."}


def dev_job(env, ticket, **kw):
    return env.job("developer", "implement", ticket=ticket, stage="development", lane="execution", runtime="fake", **kw)


def reply_job_for(env, request_id) -> Job | None:
    with env.db.read() as s:
        return s.scalar(select(Job).where(Job.idempotency_key == f"reply:{request_id}"))


def request_of(env, job_id):
    return env.get(job_id).waiting_request_id


def job_count(env):
    with env.db.read() as s:
        return s.scalar(select(func.count()).select_from(Job))


def ask_flow(env, sup, ticket):
    """Developer asks the lead through the supervisor; returns (dev job id, request id) once the reply job exists."""
    job = dev_job(env, ticket)
    env.run_until(sup, lambda: env.get(job.id).status == "waiting_input")
    request_id = request_of(env, job.id)
    env.run_until(sup, lambda: reply_job_for(env, request_id) is not None)  # written by a second transaction
    return job.id, request_id


def direct_ask(env, ticket):
    """The same question without a running supervisor, so the order of events is fully controlled."""
    job = dev_job(env, ticket)
    ctx = env.ctx(job)
    with pytest.raises(WaitingForInput):
        env.tools.call(ctx, "request_decision", {"question": "Should the row be removed at quantity zero?"})
    request_id = request_of(env, job.id)
    return job.id, request_id, reply_job_for(env, request_id)


# --- sending ---------------------------------------------------------------------------------------------------
def identity(env, role="developer", ticket=None, stage="development"):
    ticket = ticket or env.approved_ticket()
    ctx = env.ctx(env.job(role, "x", ticket=ticket, stage=stage, runtime="fake" if role == "developer" else "structured:fake",
                          lane="execution" if role == "developer" else "interactive"))
    return env.queue.verify(ctx.lease), ticket


def test_a_directed_question_is_persisted_before_its_reply_job_exists(agent_env):
    env = agent_env
    ident, ticket = identity(env)
    result = env.threads.send(ident, to_role="technical-lead", thread_id="dev-lead", category="clarification",
                              body="Which date library?", needs_reply=True, idempotency_key="q1")
    [message] = env.messages(kind="message", sender="agent:developer")
    assert message.id == result.message_id and message.recipient == "role:technical-lead"
    assert (message.meta["job_id"], message.meta["generation"], message.meta["scope_version"]) == (
        ident["job_id"], ident["generation"], ident["scope_version"])
    reply = env.get(result.reply_job_id)
    assert (reply.stage, reply.lane, reply.runtime_ref["role"], reply.ticket_id) == ("reply", "interactive",
                                                                                   "technical-lead", ticket.id)
    assert reply.runtime_ref["runtime"] == "structured:fake" and reply.runtime_ref["fake"] is True
    again = env.threads.send(ident, to_role="technical-lead", thread_id="dev-lead", category="clarification",
                             body="Which date library?", needs_reply=True, idempotency_key="q1")
    assert again.message_id == result.message_id and again.reply_job_id == result.reply_job_id
    assert len(env.messages(kind="message", sender="agent:developer")) == 1


def test_notes_logs_and_broadcasts_never_wake_a_soul(agent_env):
    env = agent_env
    ident, _ = identity(env)
    before = job_count(env)
    for n, (to_role, category) in enumerate([(None, "note"), ("technical-lead", "note"), (None, "log"),
                                             ("qa", "handoff"), (None, "bug"), ("technical-lead", "clarification")]):
        result = env.threads.send(ident, to_role=to_role, thread_id="t", category=category, body=f"fyi {n}",
                                  needs_reply=False, idempotency_key=f"k{n}")
        assert result.reply_job_id is None
    assert job_count(env) == before and len(env.messages(kind="message")) == 6  # stored, but nobody scheduled


@pytest.mark.parametrize("kwargs, why", [
    ({"to_role": None, "category": "clarification", "needs_reply": True}, "directed"),
    ({"to_role": "technical-lead", "category": "note", "needs_reply": True}, "directed"),
    ({"to_role": "developer", "category": "clarification", "needs_reply": True}, "itself"),
    ({"to_role": "janitor", "category": "clarification", "needs_reply": True}, "roles"),
    ({"to_role": "qa", "category": "gossip", "needs_reply": False}, "category"),
    ({"to_role": "qa", "category": "note", "needs_reply": False, "body": "  "}, "body"),
])
def test_invalid_messages_are_refused(agent_env, kwargs, why):
    ident, _ = identity(agent_env)
    base = {"thread_id": "t", "body": "text", "idempotency_key": "k"}
    with pytest.raises(Invalid, match=why):
        agent_env.threads.send(ident, **{**base, **kwargs})


def test_a_reply_cannot_trigger_further_work(agent_env):
    env = agent_env
    ident, _ = identity(env, role="technical-lead", stage="reply")
    with pytest.raises(Invalid, match="cannot trigger"):
        env.threads.send(ident, to_role="qa", thread_id="t", category="clarification", body="and you?",
                         needs_reply=True, idempotency_key="loop")


# --- developer -> lead over the supervisor ---------------------------------------------------------------------------------
def test_developer_waits_lead_answers_and_only_the_valid_attempt_resumes(agent_env):
    env = agent_env
    env.script(LEAD_ANSWER)
    sup = env.supervisor()
    ticket = env.approved_ticket()
    dev_id, request_id = ask_flow(env, sup, ticket)
    reply = reply_job_for(env, request_id)
    assert reply.lane == "interactive" and reply.runtime_ref["role"] == "technical-lead"
    with env.db.read() as s:  # the generation recorded when the question was asked (the job may already be resumed)
        asked_generation = env.threads.input_request(s, request_id).generation

    final = env.finish(sup, dev_id)
    assert final.result["lead_answer"] == "Yes, remove the row.\nReason: Matches UAC-1."
    assert final.lease_generation > asked_generation  # a new generation resumed from the stored answer
    replied = env.finish(sup, reply.id)
    assert replied.result["resumed"] is True and replied.result["fake_provider"] is True
    assert replied.result["usage"]["prompt_tokens"] == 100 and replied.result["context_artifact_id"]
    with env.db.read() as s:
        view = env.threads.input_request(s, request_id)
    assert view.status == "answered" and view.answer.startswith("Yes, remove the row.")
    assert view.attempt_status in ("succeeded", "queued", "running")
    resumed = [e for e in env.events() if e.type == "job.resumed" and e.entity_id == dev_id]
    assert len(resumed) == 1


def test_the_lead_reply_charges_the_same_scope_budget_as_the_developer(agent_env):
    env = agent_env
    env.script(LEAD_ANSWER)
    sup = env.supervisor()
    ticket = env.approved_ticket()
    dev_id, request_id = ask_flow(env, sup, ticket)
    reply = reply_job_for(env, request_id)
    assert reply.limits["budget_key"] == env.get(dev_id).limits["budget_key"]
    env.finish(sup, dev_id)
    env.finish(sup, reply.id)
    from app.persistence import scope_usage
    with env.db.read() as s:
        usage = scope_usage(s, ticket.id, 1)
    assert usage["totals"]["model_calls"] == 1 and usage["totals"]["tool_calls"] == 1  # lead call + dev tool call


def test_duplicate_answers_resume_exactly_once(agent_env):
    env = agent_env
    sup = env.supervisor()
    ticket = env.approved_ticket()
    dev_id, request_id = ask_flow(env, sup, ticket)
    ident = env.world.user
    first = env.threads.answer_request(ident, request_id, body="Remove it.", answer_key="a1")
    again = env.threads.answer_request(ident, request_id, body="Remove it.", answer_key="a1")
    assert first[1] is True and again == (first[0], False)
    with pytest.raises(AlreadyAnswered):
        env.threads.answer_request(ident, request_id, body="Keep it.", answer_key="a2")
    env.finish(sup, dev_id)
    assert len([e for e in env.events() if e.type == "job.resumed" and e.entity_id == dev_id]) == 1
    answers = [m for m in env.messages() if m.kind == "input_answer"]
    assert len(answers) == 1 and answers[0].body == "Remove it."


def test_an_answer_after_cancel_stays_history_and_never_revives_the_attempt(agent_env):
    env = agent_env
    sup = env.supervisor()
    ticket = env.approved_ticket()
    dev_id, request_id = ask_flow(env, sup, ticket)
    env.queue.cancel(dev_id, reason="user cancelled", actor="user:local")
    with env.db.read() as s:
        assert env.threads.input_request(s, request_id).status == "cancelled"
    answer_id, resumed = env.threads.answer_request(env.world.user,
                                                    request_id, body="Late answer", answer_key="late")
    assert resumed is False and env.get(dev_id).status == "cancelled"
    with env.db.read() as s:
        view = env.threads.input_request(s, request_id)
    assert view.status == "answered" and view.answer == "Late answer" and view.attempt_status == "cancelled"
    sup.tick()
    assert env.get(dev_id).status == "cancelled"


def test_scope_revision_cancels_the_waiting_attempt_and_the_late_answer_is_only_history(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    dev_id, request_id, reply = direct_ask(env, ticket)  # both jobs are still waiting: nothing races the revision
    t = env.world.ticket(ticket.id)
    env.world.w.edit_scope(env.world.user, t.id, t.revision, {**SCOPE, "uac": [{"id": "UAC-9", "text": "Different"}]})
    assert env.get(dev_id).status == "cancelled"
    assert env.get(reply.id).status == "cancelled"  # the pending reply job for the old scope was revoked too
    with env.db.read() as s:
        assert env.threads.input_request(s, request_id).status == "cancelled"
    _, resumed = env.threads.answer_request(env.world.user, request_id, body="Remove it", answer_key="u1")
    assert resumed is False and env.get(dev_id).status == "cancelled"
    with env.db.read() as s:
        assert env.threads.input_request(s, request_id).status == "answered"  # kept as history only


def test_a_reply_job_skips_the_model_when_the_asking_attempt_is_gone(agent_env):
    env = agent_env
    env.script(LEAD_ANSWER)
    ticket = env.approved_ticket()
    dev_id, request_id, reply = direct_ask(env, ticket)
    env.queue.cancel(dev_id, reason="user cancelled", actor="user:local")
    final = env.finish(env.supervisor(), reply.id)
    assert final.result["skipped"] == "cancelled" and final.result["model_calls"] == 0
    assert env.provider.requests == []  # no budget spent answering a question nobody is waiting for


def test_only_the_addressed_role_or_the_user_may_answer(agent_env):
    env = agent_env
    sup = env.supervisor()
    dev_id, request_id = ask_flow(env, sup, env.approved_ticket())
    for role in ("po", "qa", "developer"):
        with pytest.raises(Forbidden):
            env.threads.answer_request({"role": role, "project_id": env.project.id}, request_id, body="x", answer_key="k")
    with pytest.raises(Forbidden):
        env.threads.answer_request({"role": "technical-lead", "project_id": "other-project"}, request_id,
                                   body="x", answer_key="k")
    with pytest.raises(NotFound):
        env.threads.answer_request({"role": "technical-lead", "project_id": env.project.id}, "missing", body="x",
                                   answer_key="k")
    assert env.get(dev_id).status == "waiting_input"
    _, resumed = env.threads.answer_request(env.world.user, request_id, body="User decides: remove.", answer_key="u")
    assert resumed is True  # the user may always answer


def test_a_crash_between_the_question_and_its_reply_job_is_reconciled(agent_env, monkeypatch):
    env = agent_env
    env.script(LEAD_ANSWER)
    original = Threads._enqueue_reply
    state = {"fail": True}

    def flaky(self, *args, **kwargs):
        if state["fail"]:
            raise RuntimeError("process died before the reply job was written")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Threads, "_enqueue_reply", flaky)
    sup = env.supervisor()
    dev = dev_job(env, env.approved_ticket())
    env.run_until(sup, lambda: env.get(dev.id).status == "waiting_input")
    dev_id, request_id = dev.id, request_of(env, dev.id)
    assert reply_job_for(env, request_id) is None  # the question was saved, its reply job was not
    state["fail"] = False  # the next tick's maintenance hook repairs it
    env.run_until(sup, lambda: reply_job_for(env, request_id) is not None)
    assert env.threads.ensure_reply_jobs() == []  # idempotent
    assert env.finish(sup, dev_id).status == "succeeded"


def test_waiting_survives_a_worker_restart(agent_env, db_path):
    env = agent_env
    env.script(LEAD_ANSWER)
    dev_id, request_id, reply = direct_ask(env, env.approved_ticket())
    # A brand-new process: new database connection and a supervisor with no memory of the old one.
    from app.persistence import Database
    reopened = Database(db_path)
    try:
        assert reopened.engine is not env.db.engine
        with reopened.read() as s:
            assert s.get(Job, dev_id).status == "waiting_input"  # not re-run, not lost
    finally:
        reopened.dispose()
    second = env.supervisor("worker:second")
    for _ in range(10):  # nothing runs the waiting developer without an answer; only the reply job works
        second.tick()
    assert env.get(dev_id).status in ("waiting_input", "queued", "running", "succeeded")
    assert env.finish(second, dev_id).result["lead_answer"].startswith("Yes, remove the row.")
    assert len([e for e in env.events() if e.type == "job.resumed" and e.entity_id == dev_id]) == 1


def test_waiting_for_a_role_releases_the_execution_slot(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    dev_id, request_id, reply = direct_ask(env, ticket)
    assert env.get(dev_id).status == "waiting_input" and env.get(dev_id).lease_owner is None
    other = env.job("developer", "other", stage="work", lane="execution", runtime="fake", limits=LIMITS)
    assert env.queue.claim("w2", "execution", capacity=1, runtimes=("fake",)).job_id == other.id
    assert reply.lane == "interactive"  # the lead answers in the interactive lane: no deadlock on the one slot


def test_the_persisted_request_shows_scope_recipient_attempt_generation_and_status(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    dev_id, request_id, reply = direct_ask(env, ticket)
    with env.db.read() as s:
        view = env.threads.input_request(s, request_id)
        stored = s.get(Message, request_id)
    assert (view.status, view.recipient, view.job_id, view.ticket_id, view.scope_version) == (
        "open", "role:technical-lead", dev_id, ticket.id, 1)
    assert view.generation == 1 and view.answer is None and view.answer_id is None
    assert stored.kind == "input_request" and stored.sender == "agent:developer"
    assert stored.meta["checkpoint"] == {"reply_expected": "short"} and stored.meta["scope_version"] == 1
    lead_ctx = env.ctx(reply)
    env.threads.answer_request(env.queue.verify(lead_ctx.lease), request_id,
                               body="Remove it.", answer_key="a1")
    with env.db.read() as s:
        done = env.threads.input_request(s, request_id)
    assert done.status == "answered" and done.answer == "Remove it." and done.attempt_status == "queued"
