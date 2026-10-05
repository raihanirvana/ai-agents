"""Persisted threads between roles: directed messages, input requests, decisions and summaries.

Messages are saved before anyone is scheduled. Only a *directed* message that asks for a reply creates
work (one reply job per message, deduplicated by key); notes, logs and broadcasts never wake a soul, and
a reply can never trigger further work, so conversations cannot loop. A question from a developer to the
lead is an input request addressed to a role: the developer's job waits (its slot is released), the lead
answers in a short job, and only a still-valid attempt is resumed. Late, duplicate, post-cancel or
post-scope-revision answers stay in the history and never advance a stale attempt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.domain import Actor, Forbidden, Invalid
from app.persistence import Database, EventSpec, NotFound, append_event, append_message
from app.persistence.models import Job, Message, Ticket
from app.workers.queue import JobQueue, Lease, is_fake_runtime
from app.workers.runtime import RunContext, WaitingForInput

from .souls import ROLES
from .redaction import Redactor
from .effects import append_effect

CATEGORIES = ("clarification", "handoff", "bug", "note", "log", "reply")
REPLY_CATEGORIES = ("clarification", "handoff", "bug")  # only these may ask for a reply
PROPOSAL_INTENTS = ("scope_proposal", "decision_proposal", "technical_plan", "dependency_plan")
MAX_BODY = 8000


@dataclass(frozen=True)
class SendResult:
    message_id: str
    reply_job_id: str | None
    created: bool


@dataclass(frozen=True)
class InputRequestView:
    id: str
    thread_id: str
    project_id: str
    ticket_id: str | None
    scope_version: int | None
    recipient: str
    job_id: str | None
    generation: int | None
    status: str  # open | answered | stale_scope | cancelled | orphaned
    answer_id: str | None
    answer: str | None
    attempt_status: str | None


class Threads:
    def __init__(self, db: Database, queue: JobQueue, *, reply_lane: str = "interactive", redactor=None):
        self.db, self.queue, self.reply_lane = db, queue, reply_lane
        self.redactor = redactor or Redactor()

    # -- helpers ---------------------------------------------------------------------------------
    @staticmethod
    def structured_runtime(fake: bool) -> str:
        return "structured:fake" if fake else "structured"

    def _body(self, text: Any) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_BODY:
            raise Invalid(f"message body must be 1..{MAX_BODY} characters")
        return self.redactor.redact(text.strip())

    @staticmethod
    def _role(role: Any, *, allow_none: bool = False) -> str | None:
        if role is None and allow_none:
            return None
        if role not in ROLES:
            raise Invalid("recipient must be one of the four roles")
        return role

    # -- directed messages -------------------------------------------------------------------------
    def send(self, identity: dict[str, Any], *, to_role: str | None, thread_id: str, category: str, body: str,
             refs: list[str] | None = None, needs_reply: bool = False, expected: str = "short",
             idempotency_key: str, intent: str | None = None) -> SendResult:
        """Persist a message from the calling attempt. identity comes from the verified run, never from
        model arguments. Returns the stored message and, for a directed question, its reply job."""
        if category not in CATEGORIES:
            raise Invalid("unknown message category")
        body = self._body(body)
        to_role = self._role(to_role, allow_none=True)
        if expected not in ("short", "long"):
            raise Invalid("expected reply must be short or long")
        if needs_reply:
            if to_role is None or category not in REPLY_CATEGORIES:
                raise Invalid("only a directed clarification/handoff/bug can ask for a reply")
            if identity["stage"] == "reply" or category == "reply":
                raise Invalid("a reply cannot trigger further work")
            if to_role == identity["role"]:
                raise Invalid("a role cannot ask itself")
        with self.db.write() as s:
            self.queue.verify_identity(s, identity)
            message, created = append_effect(
                s, project_id=identity["project_id"], thread_id=self.redactor.redact(thread_id), sender=f"agent:{identity['role']}",
                recipient=f"role:{to_role}" if to_role else None, ticket_id=identity["ticket_id"], body=body,
                idempotency_key=idempotency_key, attachment_ids=self.redactor.redact_value(list(refs or [])),
                meta={"category": category, "needs_reply": needs_reply, "expected": expected,
                      "intent": self.redactor.redact_value(intent), "job_id": identity["job_id"],
                      "generation": identity["generation"], "scope_version": identity["scope_version"],
                      "fake": identity["fake"]})
            if created:
                append_event(s, identity["project_id"], EventSpec(
                    "message.sent", f"agent:{identity['role']}", {"category": category, "to": to_role,
                                                                   "needs_reply": needs_reply, "fake": identity["fake"]},
                    entity_type="messages", entity_id=message.id))
        sender = {**identity, "job_id": message.meta["job_id"], "generation": message.meta["generation"]}
        reply = self._enqueue_reply(message.id, sender, to_role, expected) if needs_reply else None
        return SendResult(message.id, reply, created)

    def _enqueue_reply(self, message_id: str, sender: dict[str, Any], to_role: str, expected: str,
                       request_id: str | None = None) -> str:
        """One reply job per message (idempotent). It reuses the sender's scope budget and limits."""
        with self.db.write() as s:
            origin = s.get(Job, sender["job_id"])
            if request_id and (origin.status != "waiting_input" or origin.waiting_request_id != request_id):
                return None
            ticket = s.get(Ticket, origin.ticket_id) if origin.ticket_id else None
            if (origin.status in ("cancelled", "stopped") or
                    ticket is not None and ticket.current_version != origin.scope_version):
                return None
            limits = {k: v for k, v in origin.limits.items() if k != "budget_key"}
            job = self.queue.enqueue(
                project_id=sender["project_id"], lane="interactive" if expected == "short" else "execution",
                stage="reply", role=to_role, idempotency_key=f"reply:{message_id}", limits=limits,
                runtime=self.structured_runtime(sender["fake"]), ticket_id=sender["ticket_id"],
                payload={"task": "answer_message", "message_id": message_id, "request_id": request_id,
                         "from_job": sender["job_id"], "from_generation": sender["generation"]},
                actor=f"agent:{sender['role']}", session=s, expected_scope=origin.scope_version,
                budget_pool=origin.runtime_ref.get("budget_pool"))
        return job.id

    def reply(self, identity: dict[str, Any], to_message_id: str, *, body: str, key: str,
              outcome: str | None = None) -> tuple[str, bool]:
        """Reply to a directed message that is not an input request. Stored as history; it never wakes anyone."""
        body = self._body(body)
        with self.db.write() as s:
            self.queue.verify_identity(s, identity)
            original = s.get(Message, to_message_id)
            if original is None or original.project_id != identity["project_id"]:
                raise NotFound("message", to_message_id)
            if original.recipient != f"role:{identity['role']}":
                raise Forbidden("only the addressed role replies to a directed message")
            message, created = append_effect(
                s, project_id=original.project_id, thread_id=original.thread_id, sender=f"agent:{identity['role']}",
                recipient=original.sender, ticket_id=original.ticket_id, body=body, reply_to=original.id,
                idempotency_key=key, meta={"category": "reply", "needs_reply": False, "outcome": outcome,
                                           "job_id": identity["job_id"], "generation": identity["generation"],
                                           "scope_version": identity["scope_version"], "fake": identity["fake"]})
            return message.id, created

    # -- developer -> lead style questions ---------------------------------------------------------------
    def ask(self, ctx: RunContext, to_role: str, question: str, *, key: str, checkpoint: dict[str, Any],
            expected: str = "short") -> None:
        """Ask another role and wait: persists the request + checkpoint, releases the slot, schedules the
        directed reply job, then ends this run with WaitingForInput. Resume happens only on a valid answer."""
        to_role = self._role(to_role)
        question = self._body(question)
        identity = self.queue.verify(ctx.lease)
        if identity["stage"] == "reply" or to_role == identity["role"]:
            raise Invalid("this run may not ask that role")
        checkpoint = self.redactor.redact_value(checkpoint)
        request_id = self.queue.request_input(ctx.lease, question=question, checkpoint={**checkpoint, "reply_expected": expected},
                                              request_key=key, recipient=f"role:{to_role}")
        ctx.log(f"asked {to_role}: request {request_id}")
        try:
            self._enqueue_reply(request_id, identity, to_role, expected, request_id=request_id)
        except Exception as exc:  # the question is already persisted: the reconciler schedules the reply
            ctx.log(f"reply job not scheduled yet ({type(exc).__name__}); reconciler will retry")
        raise WaitingForInput(request_id)

    def ensure_reply_jobs(self) -> list[str]:
        """Reconcile: every open role-addressed request must have its reply job (crash between the two writes)."""
        created: list[str] = []
        with self.db.read() as s:
            pending = [(m.id, m.recipient, m.meta, m.project_id, m.ticket_id) for m in s.scalars(
                select(Message).where(Message.recipient.like("role:%")))]
        for message_id, recipient, meta, project_id, ticket_id in pending:
            with self.db.read() as s:
                waiting = s.scalar(select(Job).where(Job.waiting_request_id == message_id, Job.status == "waiting_input"))
                exists = s.scalar(select(Job.id).where(Job.project_id == project_id,
                                                       Job.idempotency_key == f"reply:{message_id}"))
                message = s.get(Message, message_id)
                is_request = message.kind == "input_request"
                if exists or (is_request and waiting is None):
                    continue
                if not is_request and not (meta.get("needs_reply") and meta.get("category") in REPLY_CATEGORIES):
                    continue
                origin = waiting if is_request else s.get(Job, meta.get("job_id"))
                if origin is None:
                    continue
                sender = {"project_id": project_id, "ticket_id": ticket_id, "job_id": origin.id,
                          "generation": meta.get("generation"), "role": origin.runtime_ref["role"],
                          "fake": bool(origin.runtime_ref.get("fake"))}
                expected = (meta.get("checkpoint", {}).get("reply_expected", "short") if is_request
                            else meta.get("expected", "short"))
            job_id = self._enqueue_reply(message_id, sender, recipient.split(":", 1)[1], expected,
                                         request_id=message_id if is_request else None)
            if job_id:
                created.append(job_id)
        return created

    def answer_request(self, identity: dict[str, Any] | Actor, request_id: str, *, body: str,
                       answer_key: str) -> tuple[str, bool]:
        """Persist an answer to a role-addressed (or user-addressed) request. Returns (message_id, resumed).

        Only the addressed role (or a user) may answer. The answer is always kept as history; the waiting
        attempt resumes only if it is still valid (queue.answer enforces scope/cancel/duplicate rules).
        """
        body = self._body(body)
        with self.db.write() as s:
            request = s.get(Message, request_id)
            if request is None or request.kind != "input_request":
                raise NotFound("input_request", request_id)
            recipient, project_id = request.recipient or "user", request.project_id
            if isinstance(identity, Actor):
                sender, project, role = identity.id, identity.project_id, identity.role
                allowed = role == "user" or recipient == f"role:{role}"
            else:
                sender, project, role = f"agent:{identity['role']}", identity["project_id"], identity["role"]
                allowed = recipient == f"role:{role}"
            if project != project_id or not allowed:
                raise Forbidden("only the addressed role (or the user) may answer this request")
            if isinstance(identity, Actor):
                if identity.role != "user":
                    bound = self.queue.identity(s, Lease(identity.job_id, identity.id, identity.generation))
                    if (bound["project_id"], bound["role"]) != (project, role):
                        raise Forbidden("actor does not match its run")
            else:
                self.queue.verify_identity(s, identity)
            return self.queue.answer(request_id, body=body, answer_key=answer_key, user=sender, session=s)

    def escalate(self, identity, message_id: str, *, body: str, request_id=None) -> str:
        """Persist a user question, keeping the original attempt blocked until the user answers."""
        body = self._body(body)
        with self.db.write() as s:
            self.queue.verify_identity(s, identity)
            original = s.get(Message, message_id)
            if original is None or original.project_id != identity["project_id"]:
                raise NotFound("message", message_id)
            if original.recipient != f"role:{identity['role']}":
                raise Forbidden("only the addressed role escalates a question")
            waiting = s.scalar(select(Job).where(Job.waiting_request_id == request_id)) if request_id else None
            key = f"escalation:{message_id}"
            existing = s.scalar(select(Message).where(Message.project_id == original.project_id,
                                                      Message.idempotency_key == key))
            if request_id and not existing and self.input_request(s, request_id).status != "open":
                raise Invalid("only an open request may be escalated")
            question, created = append_effect(
                s, project_id=original.project_id, thread_id=original.thread_id,
                sender=f"agent:{identity['role']}", recipient="user", kind="input_request",
                ticket_id=original.ticket_id, body=self._body((original.body + "\n\n" + body)[:MAX_BODY]),
                reply_to=original.id, idempotency_key=key,
                meta={"intent": "user_escalation", "source_request_id": request_id,
                      "scope_version": original.meta.get("scope_version"),
                      "job_id": waiting.id if waiting else identity["job_id"],
                      "generation": original.meta.get("generation") if waiting else identity["generation"],
                      "fake": identity["fake"]})
            if waiting and waiting.status == "waiting_input":
                waiting.waiting_request_id = question.id
                waiting.revision += 1
            if created:
                append_event(s, original.project_id, EventSpec(
                    "input.escalated", f"agent:{identity['role']}",
                    {"source_message_id": original.id, "request_id": question.id,
                     "waiting_job_id": waiting.id if waiting else None, "fake": identity["fake"]},
                    entity_type="messages", entity_id=question.id))
            return question.id

    def input_request(self, session, request_id: str) -> InputRequestView:
        """The persisted request with its scope, recipient attempt/generation, status and answer."""
        request = session.get(Message, request_id)
        if request is None or request.kind != "input_request":
            raise NotFound("input_request", request_id)
        answer = session.scalar(select(Message).where(Message.reply_to == request_id, Message.kind == "input_answer"))
        job = session.scalar(select(Job).where(Job.waiting_request_id == request_id))
        ticket = session.get(Ticket, request.ticket_id) if request.ticket_id else None
        scope = request.meta.get("scope_version")
        if answer is not None:
            status = "answered"
        elif job is not None and job.status in ("cancelled", "stopped", "failed"):
            status = "cancelled"
        elif ticket is not None and ticket.current_version != scope:
            status = "stale_scope"
        elif job is None:
            # A nonblocking directed message can still produce a visible user question.
            status = ("open" if request.meta.get("intent") == "user_escalation"
                      and request.meta.get("source_request_id") is None else "orphaned")
        elif job.status == "waiting_input":
            status = "open"
        else:
            status = "orphaned"
        return InputRequestView(request.id, request.thread_id, request.project_id, request.ticket_id, scope,
                                request.recipient or "user", request.meta.get("job_id"), request.meta.get("generation"),
                                status, answer.id if answer else None, answer.body if answer else None,
                                job.status if job else None)

    # -- decisions: proposals are not authoritative ------------------------------------------------------------
    def propose_decision(self, identity: dict[str, Any], *, title: str, rationale: str, key: str) -> str:
        if identity["role"] != "technical-lead":
            raise Forbidden("only the technical lead proposes technical decisions")
        title, rationale = self._body(title), self._body(rationale)
        with self.db.write() as s:
            self.queue.verify_identity(s, identity)
            message, created = append_effect(
                s, project_id=identity["project_id"], thread_id=f"decisions:{identity['project_id']}",
                sender="agent:technical-lead", recipient="user", ticket_id=identity["ticket_id"], body=title,
                idempotency_key=key, meta={"intent": "decision_proposal", "title": title, "rationale": rationale,
                                           "job_id": identity["job_id"], "generation": identity["generation"],
                                           "scope_version": identity["scope_version"], "fake": identity["fake"]})
            if created:
                append_event(s, identity["project_id"], EventSpec(
                    "decision.proposed", "agent:technical-lead", {"title": title, "fake": identity["fake"]},
                    entity_type="messages", entity_id=message.id))
            return message.id

    def decide(self, actor: Actor, proposal_id: str, accept: bool) -> str:
        """The user accepts or rejects a decision proposal; only accepted ones enter project knowledge."""
        if actor.role != "user" or type(accept) is not bool:
            raise Forbidden("only the user decides, with an explicit accept/reject")
        with self.db.write() as s:
            proposal = s.get(Message, proposal_id)
            if proposal is None or proposal.meta.get("intent") != "decision_proposal":
                raise NotFound("decision_proposal", proposal_id)
            if proposal.project_id != actor.project_id:
                raise Forbidden("cross-project command")
            verdict = "accepted" if accept else "rejected"
            message, created = append_message(
                s, project_id=proposal.project_id, thread_id=proposal.thread_id, sender=actor.id, kind="system",
                reply_to=proposal.id, body=verdict, idempotency_key=f"decision:{proposal.id}",
                meta={"intent": f"decision_{verdict}", "proposal_id": proposal.id})
            if not created and message.body != verdict:
                raise Invalid("proposal already decided differently")
            if created:
                append_event(s, proposal.project_id, EventSpec(f"decision.{verdict}", actor.id,
                             {"proposal_id": proposal.id}, entity_type="messages", entity_id=message.id))
            return message.id

    @staticmethod
    def accepted_decisions(session, project_id: str) -> list[Message]:
        """Accepted decisions only, oldest first. Pending/rejected proposals are never returned."""
        accepted = {m.reply_to for m in session.scalars(select(Message).where(
            Message.project_id == project_id, Message.kind == "system"))
            if m.meta.get("intent") == "decision_accepted"}
        proposals = [m for m in session.scalars(select(Message).where(
            Message.project_id == project_id).order_by(Message.created_at, Message.thread_id, Message.seq, Message.id))
            if m.meta.get("intent") == "decision_proposal" and m.id in accepted]
        return proposals

    # -- summaries: derived, never replacing history -------------------------------------------------------------
    def record_summary(self, *, project_id: str, thread_id: str, from_seq: int, to_seq: int, text: str,
                       author: str, key: str) -> str:
        """Store a summary of messages from_seq..to_seq. The originals stay; the summary records which ones it
        covers and their digest, so a stale or tampered summary is detectable."""
        text = self._body(text)
        with self.db.write() as s:
            covered = list(s.scalars(select(Message).where(
                Message.thread_id == thread_id, Message.project_id == project_id,
                Message.seq >= from_seq, Message.seq <= to_seq).order_by(Message.seq)))
            if not covered or from_seq > to_seq:
                raise Invalid("a summary must cover at least one existing message")
            digest = hashlib.sha256(json.dumps([[m.id, m.body] for m in covered]).encode()).hexdigest()
            message, _ = append_message(
                s, project_id=project_id, thread_id=thread_id, sender=author, kind="system", body=text,
                idempotency_key=key, ticket_id=covered[0].ticket_id,
                meta={"intent": "summary", "from_seq": from_seq, "to_seq": to_seq,
                      "message_ids": [m.id for m in covered], "source_digest": digest})
            return message.id
