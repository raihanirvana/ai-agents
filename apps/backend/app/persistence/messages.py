"""Thread messages with idempotent retries and exactly-one input answers.

A retried request with the same idempotency key returns the stored message instead of
adding another; the storage-level unique indexes make that safe under concurrency.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .changes import NotFound, PersistenceError
from .models import Message


class IdempotencyConflict(PersistenceError):
    """The same key was reused with a different identity or payload."""


class AlreadyAnswered(PersistenceError):
    pass


def not_runtime_log():
    """Conversation filter shared by API projections and agent context queries."""
    return func.json_extract(Message.meta, "$.runtime_log").is_(None)


def _canonical(value: Any) -> str:
    """JSON-normalised form, so tuples/lists and key order do not look like differences."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload(*, thread_id, sender, recipient, kind, body, ticket_id, reply_to, meta, attachment_ids) -> tuple:
    """Every semantic field of a message. A retry must match all of it, not just the text."""
    return (thread_id, sender, recipient, kind, body, ticket_id, reply_to,
            _canonical(meta or {}), _canonical(list(attachment_ids or [])))


def _stored_payload(message: Message) -> tuple:
    return _payload(thread_id=message.thread_id, sender=message.sender, recipient=message.recipient,
                    kind=message.kind, body=message.body, ticket_id=message.ticket_id, reply_to=message.reply_to,
                    meta=message.meta, attachment_ids=message.attachment_ids)


def append_message(session: Session, *, project_id: str, thread_id: str, sender: str, body: str,
                   kind: str = "message", recipient: str | None = None, ticket_id: str | None = None,
                   reply_to: str | None = None, idempotency_key: str | None = None,
                   meta: dict[str, Any] | None = None,
                   attachment_ids: list[str] | None = None) -> tuple[Message, bool]:
    """Returns (message, created). created=False means an identical retry was recognised.

    Call inside db.write(): the BEGIN IMMEDIATE lock makes the per-thread seq race-free.
    """
    if idempotency_key is not None:
        existing = session.scalar(select(Message).where(
            Message.project_id == project_id, Message.idempotency_key == idempotency_key))
        if existing is not None:
            wanted = _payload(thread_id=thread_id, sender=sender, recipient=recipient, kind=kind, body=body,
                              ticket_id=ticket_id, reply_to=reply_to, meta=meta, attachment_ids=attachment_ids)
            if _stored_payload(existing) != wanted:
                raise IdempotencyConflict("idempotency key reused with a different message")
            return existing, False
    seq = (session.scalar(select(func.max(Message.seq)).where(Message.thread_id == thread_id)) or 0) + 1
    message = Message(project_id=project_id, thread_id=thread_id, seq=seq, sender=sender, recipient=recipient,
                      ticket_id=ticket_id, kind=kind, body=body, reply_to=reply_to,
                      idempotency_key=idempotency_key, meta=dict(meta or {}),
                      attachment_ids=list(attachment_ids or []))
    session.add(message)
    session.flush()
    return message, True


def answer_input_request(session: Session, *, request_id: str, sender: str, body: str,
                         answer_key: str, meta: dict[str, Any] | None = None) -> tuple[Message, bool]:
    """Persist the answer to an input request once. Re-sending the same answer is a no-op;
    a different answer to an already answered request raises AlreadyAnswered."""
    request = session.get(Message, request_id)
    if request is None or request.kind != "input_request":
        raise NotFound("input_request", request_id)
    answered = session.scalar(select(Message).where(Message.reply_to == request_id, Message.kind == "input_answer"))
    if answered is not None:
        if answered.idempotency_key != answer_key:
            raise AlreadyAnswered(f"input request {request_id} already has an answer")
        wanted = _payload(thread_id=request.thread_id, sender=sender, recipient=None, kind="input_answer",
                          body=body, ticket_id=request.ticket_id, reply_to=request_id, meta=meta,
                          attachment_ids=None)
        if _stored_payload(answered) != wanted:
            raise IdempotencyConflict("answer key reused with a different sender, body or metadata")
        return answered, False
    return append_message(session, project_id=request.project_id, thread_id=request.thread_id, sender=sender,
                          body=body, kind="input_answer", ticket_id=request.ticket_id, reply_to=request_id,
                          idempotency_key=answer_key, meta=meta)
