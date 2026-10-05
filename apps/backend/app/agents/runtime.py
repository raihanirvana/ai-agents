"""Structured PO/lead runtime: bounded model calls with validated outputs, applied through domain commands.

PO and technical lead start with structured model calls and limited tools (MVP blueprint); developer and QA
use the Hermes runtime in DEV-010. A job here: verifies its lease, builds a hashed context snapshot from the
database, makes one reserved model call (plus at most one repair call when the answer is invalid), validates
the answer against a contract, and applies it only through the same commands the user interface uses. PO/lead
output is always a PROPOSAL: nothing here approves, accepts or releases anything.

Registered with the supervisor as "structured" (real provider) or "structured:fake" (labelled fake provider);
a fake provider under a real label (or the reverse) is refused.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select

from app.domain import DomainError, Workflow
from app.persistence import RevisionConflict
from app.persistence.models import Job, Message, Ticket
from app.workers.queue import QuotaWait, StaleLease
from app.workers.runtime import Cancelled, Outcome, RunContext, WaitingForInput

from .context import ContextBuilder, ContextSnapshot, ContextRefused, ContextTooLarge
from .effects import append_effect
from .models import ConfigError, ModelClient, ModelError, ModelResult
from .outputs import (Clarification, InvalidOutput, LeadAnswer, LeadPlanOutput, PoOutput, PoProposal,
                      PoReviseOutput, parse_output)
from .redaction import Redactor
from .threads import Threads

TASKS = {("po", "breakdown"), ("po", "revise"), ("technical-lead", "technical_plan"),
         ("technical-lead", "answer_message")}


class StructuredAgentRuntime:
    def __init__(self, *, db, workflow: Workflow, threads: Threads, builder: ContextBuilder, client: ModelClient,
                 redactor: Redactor, fake: bool):
        self.db, self.workflow, self.threads = db, workflow, threads
        self.builder, self.client, self.redactor, self.fake = builder, client, redactor, fake
        self.threads.redactor = redactor
        self.name = "structured:fake" if fake else "structured"

    # -- contract with the supervisor -------------------------------------------------------------------------
    def stop(self, ctx: RunContext) -> None:
        ctx.cancelled.set()

    def run(self, ctx: RunContext) -> Outcome:
        identity = ctx.queue.verify(ctx.lease)
        role, task = identity["role"], ctx.job["runtime_ref"].get("payload", {}).get("task")
        if (role, task) not in TASKS:
            return Outcome("failed", error=f"structured runtime has no task {task!r} for role {role}")
        try:
            provider = self.client.provider_for(role)
            if bool(provider.fake) != self.fake or identity["fake"] != self.fake:
                return Outcome("failed", error="refusing to run: provider and runtime fake labels disagree")
            return getattr(self, "_" + task)(ctx, identity)
        except (WaitingForInput, QuotaWait, StaleLease, Cancelled):
            raise  # the supervisor owns these transitions
        except (ContextRefused, ContextTooLarge, ConfigError) as exc:
            return self._failed(ctx, f"context/config refused: {exc}", retryable=False)
        except InvalidOutput as exc:
            return self._failed(ctx, f"invalid structured output after one repair attempt: {exc}", retryable=False)
        except ModelError as exc:
            return self._failed(ctx, f"model call failed: {exc}", retryable=exc.retryable)
        except DomainError as exc:
            return self._failed(ctx, f"domain refused the proposal: {exc}", retryable=False)

    def _failed(self, ctx: RunContext, error: str, *, retryable: bool) -> Outcome:
        error = self.redactor.redact(error)[:500]
        ctx.log(error)
        return Outcome("failed", error=error, retryable=retryable)

    @staticmethod
    def _fence(ctx: RunContext) -> dict[str, Any]:
        """Re-verify the lease right before any side effect. The model call may have taken long enough for the
        attempt to be revoked (scope change, cancel); its answer is then dropped instead of persisted."""
        return ctx.queue.verify(ctx.lease)

    # -- shared steps -----------------------------------------------------------------------------------------------
    @staticmethod
    def _root(identity: dict[str, Any]) -> str:
        """The first job of a retry chain; effects are keyed on it so a retried run never duplicates them."""
        return identity["root_job_id"]

    def _ask(self, ctx: RunContext, identity: dict[str, Any], task: dict[str, Any], union, *, repo_refs=None):
        """Context -> reserved model call -> validated answer, with at most one repair call."""
        checkpoint_key = hashlib.sha256(json.dumps({"task": task, "answer": ctx.answer},
                                                   sort_keys=True).encode()).hexdigest()
        with self.db.read() as s:
            saved = (s.get(Job, identity["job_id"]).runtime_ref or {}).get("structured_outputs", {}).get(checkpoint_key)
        if saved is not None:
            ctx.queue.set_context(ctx.lease, saved["snapshot"]["artifact_id"])
            return parse_output(saved["text"], union), saved["meta"], ContextSnapshot(**saved["snapshot"])
        # Give the model the same contract we validate, including task-specific
        # union alternatives. Prose alone confused revision dependency IDs with
        # breakdown keys in the real DEV-015 pilot; repair needs the shape too.
        snapshot = self.builder.build(identity, task={**task, 'output_schema': TypeAdapter(union).json_schema()},
                                      repo_refs=repo_refs, answer=ctx.answer,
                                      lease=ctx.lease, queue=ctx.queue)
        ctx.log(f"context {snapshot.sha256[:12]} ~{snapshot.estimated_tokens} tokens (estimate), "
                f"{len(snapshot.manifest['gaps']) - 1} gaps")
        prompt, calls, total = snapshot.user, [], {}
        for attempt in (1, 2):
            result = self.client.complete(ctx, identity["role"], snapshot.system, prompt)
            calls.append(result)
            self._add_usage(total, result)
            try:
                parsed = parse_output(result.text, union)
                break
            except InvalidOutput as exc:
                ctx.log(f"invalid output (attempt {attempt}): {exc}")
                if attempt == 2:
                    raise
                prompt = (snapshot.user + "\n## Your previous answer was rejected\nFix these problems and answer again "
                          "with one valid JSON object only:\n- " + "\n- ".join(exc.errors[:6]))
        last: ModelResult = calls[-1]
        meta = {"model": last.model, "provider": last.provider, "fake": last.fake, "model_calls": len(calls),
                "context_sha256": snapshot.sha256, "context_artifact_id": snapshot.artifact_id, "usage": total}
        # Save the validated response before applying any effects. A crash resumes exactly this
        # proposal and its original evidence, rather than asking for a potentially different one.
        from dataclasses import asdict
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            job = s.get(Job, identity["job_id"])
            job.runtime_ref = {**job.runtime_ref, "structured_outputs": {
                **job.runtime_ref.get("structured_outputs", {}),
                checkpoint_key: {"text": last.text, "meta": meta, "snapshot": asdict(snapshot)}}}
        return parsed, meta, snapshot

    @staticmethod
    def _add_usage(total: dict[str, Any], result: ModelResult) -> None:
        """Sum what providers reported; a counter that any call did not report stays visibly unknown."""
        for key, value in result.usage.as_counters().items():
            if value is None or total.get(key, 0) is None:
                total[key] = None
            else:
                total[key] = total.get(key, 0) + value

    def _clarify(self, ctx: RunContext, identity: dict[str, Any], output: Clarification) -> None:
        text = output.as_text()
        ctx.log(f"needs clarification: {len(output.questions)} question(s)")
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]  # stable across processes (hash() is not)
        ctx.request_input(text, {"questions": [q.id for q in output.questions]},
                          f"clarify:{self._root(identity)}:{digest}")

    def _result(self, meta: dict[str, Any], **extra) -> Outcome:
        return Outcome("succeeded", {**extra, "provider": meta["provider"], "model": meta["model"],
                                     "context_sha256": meta["context_sha256"],
                                     "context_artifact_id": meta["context_artifact_id"], "usage": meta["usage"],
                                     "model_calls": meta["model_calls"]})

    def _post(self, identity, *, thread, sender_role, recipient, body, intent, meta_extra, key, ticket_id,
              attachments) -> str:
        with self.db.write() as s:
            self.threads.queue.verify_identity(s, identity)
            message, _ = append_effect(
                s, project_id=identity["project_id"], thread_id=thread, sender=f"agent:{sender_role}",
                recipient=recipient, ticket_id=ticket_id, body=self.redactor.redact(body)[:7900],
                idempotency_key=key, attachment_ids=[a for a in attachments if a],
                meta=self.redactor.redact_value({"intent": intent, "fake": identity["fake"],
                                                 "job_id": identity["job_id"], "generation": identity["generation"],
                                                 **meta_extra}))
            return message.id

    # -- PO ---------------------------------------------------------------------------------------------------------------
    def _breakdown(self, ctx: RunContext, identity: dict[str, Any]) -> Outcome:
        task = ctx.job["runtime_ref"]["payload"]
        output, meta, _ = self._ask(ctx, identity, {"name": "breakdown", "request": task.get("request", "")}, PoOutput)
        if isinstance(output, Clarification):
            self._clarify(ctx, identity, output)
        if not isinstance(output, PoProposal):
            return self._failed(ctx, "breakdown expects a proposal or a clarification", retryable=False)
        identity = self._fence(ctx)
        self._precheck_dependencies(identity, output)
        root, created, key_map = self._root(identity), [], {}
        for ticket in output.creation_order():
            document = {"title": ticket.title, "description": ticket.description,
                        "uac": [c.model_dump() for c in ticket.uac],
                        "dependencies": [key_map[k] for k in ticket.depends_on_keys] + ticket.depends_on_ticket_ids}
            made = self.workflow.create_ticket(ctx.actor(), document, idempotency_key=f"breakdown:{root}:{ticket.key}")
            key_map[ticket.key] = made.id
            created.append(made.id)
        self._post(identity, thread=f"chat:{identity['project_id']}", sender_role="po", recipient="user",
                   body=f"{output.summary}\nProposed {len(created)} ticket(s); none is approved until you approve it.",
                   intent="po_breakdown", key=f"breakdown-msg:{root}", ticket_id=None,
                   attachments=[meta["context_artifact_id"]],
                   meta_extra={"ticket_ids": created, "key_map": key_map, "assumptions": output.assumptions,
                               "usage": meta["usage"], "model": meta["model"], "provider": meta["provider"]})
        return self._result(meta, created_ticket_ids=created, key_map=key_map, approved=False)

    def _precheck_dependencies(self, identity: dict[str, Any], output: PoProposal) -> None:
        """Refuse a breakdown that names an unusable existing ticket BEFORE creating any ticket."""
        wanted = {d for t in output.tickets for d in t.depends_on_ticket_ids}
        if not wanted:
            return
        with self.db.read() as s:
            found = {t.id: t for t in s.scalars(select(Ticket).where(
                Ticket.project_id == identity["project_id"], Ticket.id.in_(wanted)))}
        for dep in sorted(wanted):
            if dep not in found or found[dep].phase == "cancelled":
                raise DomainError(f"dependency {dep} does not exist or is cancelled; nothing was created")

    def _revise(self, ctx: RunContext, identity: dict[str, Any]) -> Outcome:
        if identity["ticket_id"] is None:
            return self._failed(ctx, "revise needs a ticket run", retryable=False)
        output, meta, _ = self._ask(ctx, identity, {"name": "revise", "ticket_id": identity["ticket_id"],
                                                    "request": ctx.job["runtime_ref"]["payload"].get("request", "")},
                                    PoReviseOutput)
        if isinstance(output, Clarification):
            self._clarify(ctx, identity, output)
        identity = self._fence(ctx)
        document = {"title": output.title, "description": output.description,
                    "uac": [c.model_dump() for c in output.uac], "dependencies": output.depends_on_ticket_ids}
        for _ in range(3):  # the ticket may have changed revision while the model was thinking
            with self.db.read() as s:
                revision = s.get(Ticket, identity["ticket_id"]).revision
            try:
                proposal_id = self.workflow.propose_scope(ctx.actor(), identity["ticket_id"], revision, document,
                                                          idempotency_key=f"revise:{self._root(identity)}")
                return self._result(meta, proposal_id=proposal_id, approved=False)
            except RevisionConflict:
                continue
        return self._failed(ctx, "the ticket kept changing; proposal not recorded", retryable=True)

    # -- technical lead ---------------------------------------------------------------------------------------------------
    def _technical_plan(self, ctx: RunContext, identity: dict[str, Any]) -> Outcome:
        output, meta, _ = self._ask(ctx, identity, {"name": "technical_plan", "ticket_id": identity["ticket_id"]},
                                    LeadPlanOutput)
        if isinstance(output, Clarification):
            self._clarify(ctx, identity, output)
        identity = self._fence(ctx)
        root = self._root(identity)
        plan_id = self._post(
            identity, thread=f"plan:{identity['ticket_id']}", sender_role="technical-lead", recipient="user",
            body=output.summary, intent="technical_plan", key=f"plan:{root}", ticket_id=identity["ticket_id"],
            attachments=[meta["context_artifact_id"]],
            meta_extra={"plan": output.model_dump(), "usage": meta["usage"], "model": meta["model"],
                        "provider": meta["provider"], "authoritative": False})
        decisions = [self.threads.propose_decision(identity, title=d.title, rationale=d.rationale,
                                                   key=f"plan:{root}:decision:{i}")
                     for i, d in enumerate(output.decisions)]
        return self._result(meta, plan_message_id=plan_id, decision_proposal_ids=decisions,
                            needs_user=output.needs_user, authoritative=False)

    def _answer_message(self, ctx: RunContext, identity: dict[str, Any]) -> Outcome:
        payload = ctx.job["runtime_ref"]["payload"]
        request_id, message_id = payload.get("request_id"), payload["message_id"]
        if request_id:
            with self.db.read() as s:
                view = self.threads.input_request(s, request_id)
            if view.status != "open":
                # The asking attempt is gone (cancelled, scope revised, already answered): no model call.
                ctx.log(f"request {request_id} is {view.status}; not answering")
                return Outcome("succeeded", {"skipped": view.status, "model_calls": 0, "resumed": False})
        with self.db.read() as s:
            question = s.get(Message, message_id).body
        output, meta, _ = self._ask(ctx, identity, {"name": "answer_message", "message_id": message_id,
                                                    "question": question}, LeadAnswer)
        identity = self._fence(ctx)
        body = output.answer + (f"\nReason: {output.rationale}" if output.rationale else "")
        if output.outcome == "needs_user":
            escalation_id = self.threads.escalate(identity, message_id, body=body, request_id=request_id)
            return self._result(meta, user_request_id=escalation_id, resumed=False, outcome=output.outcome)
        key = f"answer:{message_id}"  # one answer per question, stable across retries and restarts
        if request_id:
            answer_id, resumed = self.threads.answer_request(identity, request_id, body=body, answer_key=key)
        else:
            answer_id, _ = self.threads.reply(identity, message_id, body=body, key=key, outcome=output.outcome)
            resumed = False
        return self._result(meta, answer_id=answer_id, resumed=resumed, outcome=output.outcome)
