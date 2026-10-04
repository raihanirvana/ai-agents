"""Usage per scope across retries, and idempotent persisted input requests/answers."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from app.persistence import (
    AlreadyAnswered, IdempotencyConflict, NotFound, answer_input_request, append_message, record_usage, scope_usage,
)
from app.persistence.models import Job, Message

from . import factories as f


def test_usage_accumulates_per_job_and_is_never_reset_by_retries(db, world):
    with db.write() as s:
        first = f.job(s, world.project, world.ticket, attempt=1)
        record_usage(s, first.id, {"model_calls": 12, "tool_calls": 30, "active_s": 100.5})
    with db.write() as s:  # retry = a new job/attempt for the same scope
        second = f.job(s, world.project, world.ticket, attempt=2, parent_job_id=first.id)
        record_usage(s, second.id, {"model_calls": 5, "tool_calls": 9, "active_s": 20})
        record_usage(s, second.id, {"model_calls": 1})
    with db.read() as s:
        usage = scope_usage(s, world.ticket.id, 1)
    assert usage == {"totals": {"model_calls": 18, "tool_calls": 39, "active_s": 120.5}, "unknown": [], "jobs": 2}


def test_usage_the_provider_did_not_report_is_unknown_not_zero(db, world):
    with db.write() as s:
        job = f.job(s, world.project, world.ticket)
        record_usage(s, job.id, {"model_calls": 3, "cost_usd": None})
        record_usage(s, job.id, {"prompt_tokens": None})
    with db.read() as s:
        usage = scope_usage(s, world.ticket.id, 1)
    assert usage["totals"] == {"model_calls": 3}
    assert "cost_usd" not in usage["totals"]  # never silently 0
    assert usage["unknown"] == ["cost_usd", "prompt_tokens"]


def test_usage_is_per_scope_version(db, world):
    with db.write() as s:
        job = f.job(s, world.project, world.ticket)
        record_usage(s, job.id, {"model_calls": 4})
    with db.read() as s:
        assert scope_usage(s, world.ticket.id, 2)["jobs"] == 0


@pytest.mark.parametrize("delta", [{"model_calls": -1}, {"model_calls": "3"}, {"model_calls": True}, {"_unknown": 1}])
def test_invalid_usage_is_rejected(db, world, delta):
    with db.write() as s:
        job = f.job(s, world.project, world.ticket)
    with pytest.raises(ValueError):
        with db.write() as s:
            record_usage(s, job.id, delta)
    with pytest.raises(NotFound):
        with db.write() as s:
            record_usage(s, "missing", {"model_calls": 1})


def test_concurrent_usage_updates_are_not_lost(db, world):
    with db.write() as s:
        job = f.job(s, world.project, world.ticket)

    def worker():
        for _ in range(10):
            with db.write() as s:
                record_usage(s, job.id, {"tool_calls": 1})

    pool = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in pool]
    [t.join(60) for t in pool]
    with db.read() as s:
        assert s.get(Job, job.id).usage["tool_calls"] == 50


def send(db, world, **kw):
    with db.write() as s:
        message, created = append_message(s, project_id=world.project.id, thread_id="po-chat", sender="user", **kw)
        return message.id, message.seq, created


def test_messages_are_ordered_per_thread(db, world):
    assert [send(db, world, body=f"m{n}")[1] for n in range(3)] == [1, 2, 3]
    with db.write() as s:
        other, _ = append_message(s, project_id=world.project.id, thread_id="other", sender="user", body="x")
    assert other.seq == 1


def test_retried_message_is_recognised_by_idempotency_key(db, world):
    first = send(db, world, body="build a menu", idempotency_key="msg-1")
    again = send(db, world, body="build a menu", idempotency_key="msg-1")
    assert again[0] == first[0] and again[2] is False
    with db.read() as s:
        assert len(s.scalars(select(Message)).all()) == 1
    with pytest.raises(IdempotencyConflict):
        send(db, world, body="something else", idempotency_key="msg-1")


def open_request(db, world):
    with db.write() as s:
        request, _ = append_message(
            s, project_id=world.project.id, thread_id="clarify", sender="agent:developer", kind="input_request",
            body="What happens when quantity reaches zero?", ticket_id=world.ticket.id,
            meta={"job_id": "job-1", "generation": 3, "scope_version": 1})
        return request.id


def answer(db, request_id, body="remove the row", key="answer-1"):
    with db.write() as s:
        message, created = answer_input_request(s, request_id=request_id, sender="user", body=body, answer_key=key)
        return message.id, created


def test_input_answer_is_persisted_once_and_retries_are_noops(db, world):
    request_id = open_request(db, world)
    first = answer(db, request_id)
    assert first[1] is True
    assert answer(db, request_id) == (first[0], False)  # same answer, same key
    with pytest.raises(IdempotencyConflict):  # same key, different content: not a retry
        answer(db, request_id, body="keep the row")
    with pytest.raises(AlreadyAnswered):  # a different key is a second answer
        answer(db, request_id, key="answer-2")
    with db.read() as s:
        thread = s.scalars(select(Message).where(Message.thread_id == "clarify").order_by(Message.seq)).all()
    assert [m.kind for m in thread] == ["input_request", "input_answer"]
    assert thread[1].reply_to == request_id and thread[0].meta["generation"] == 3


def test_only_input_requests_can_be_answered(db, world):
    plain, _, _ = send(db, world, body="hello")
    for target in (plain, "missing"):
        with pytest.raises(NotFound):
            answer(db, target)


def test_simultaneous_answers_resolve_to_exactly_one(db, world):
    request_id = open_request(db, world)
    outcomes, lock = [], threading.Lock()

    def worker(body):
        try:
            result = answer(db, request_id, body=body)
        except (AlreadyAnswered, IdempotencyConflict):
            result = "refused"
        with lock:
            outcomes.append(result)

    pool = [threading.Thread(target=worker, args=("remove the row",)) for _ in range(4)]
    pool += [threading.Thread(target=worker, args=("keep the row",)) for _ in range(2)]
    [t.start() for t in pool]
    [t.join(60) for t in pool]
    with db.read() as s:
        answers = s.scalars(select(Message).where(Message.kind == "input_answer")).all()
    assert len(answers) == 1  # storage allows a single answer per request
    created = [o for o in outcomes if o != "refused" and o[1] is True]
    assert len(created) == 1


def request_with(db, world, **overrides):
    fields = dict(thread_id="clarify", sender="agent:developer", kind="input_request", body="quantity at zero?",
                  ticket_id=world.ticket.id, recipient="po", idempotency_key="K",
                  meta={"job_id": "job-1", "generation": 1, "scope_version": 1}, attachment_ids=[])
    fields.update(overrides)
    with db.write() as s:
        message, created = append_message(s, project_id=world.project.id, **fields)
        return message.id, created


def test_an_identical_retry_is_a_noop_even_with_reordered_payload(db, world):
    first = request_with(db, world)
    again = request_with(db, world, meta={"scope_version": 1, "generation": 1, "job_id": "job-1"})
    assert again == (first[0], False)


@pytest.mark.parametrize("override", [
    {"recipient": "qa"},
    {"meta": {"job_id": "job-1", "generation": 2, "scope_version": 1}},  # a later generation is not a retry
    {"meta": {"job_id": "job-2", "generation": 1, "scope_version": 1}},
    {"attachment_ids": ["some-artifact"]},
    {"sender": "agent:qa"},
    {"thread_id": "another-thread"},
    {"ticket_id": None},
    {"kind": "message"},
])
def test_same_key_with_a_different_payload_is_refused_not_deduplicated(db, world, override):
    first = request_with(db, world)
    with pytest.raises(IdempotencyConflict):
        request_with(db, world, **override)
    with db.read() as s:
        stored = s.get(Message, first[0])
        assert (stored.recipient, stored.meta["generation"]) == ("po", 1)  # the original is untouched
        assert len(s.scalars(select(Message)).all()) == 1


def test_answer_key_reused_with_another_sender_or_metadata_is_a_conflict(db, world):
    request_id = open_request(db, world)
    with db.write() as s:
        answer_input_request(s, request_id=request_id, sender="user", body="remove", answer_key="a1", meta={"n": 1})
    with db.write() as s:  # identical retry: no-op
        assert answer_input_request(s, request_id=request_id, sender="user", body="remove",
                                    answer_key="a1", meta={"n": 1})[1] is False
    for changes in ({"sender": "agent:po"}, {"meta": {"n": 2}}, {"body": "keep"}):
        with pytest.raises(IdempotencyConflict):
            with db.write() as s:
                answer_input_request(s, request_id=request_id, answer_key="a1",
                                     **{"sender": "user", "body": "remove", "meta": {"n": 1}, **changes})
