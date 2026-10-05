"""Role tool policy and the facade every agent tool call goes through.

Authorisation comes from the run identity (project, ticket, scope version, job, generation, lease
owner, role) verified by the queue, never from arguments the model supplies. A call is reserved
against the tool budget first, so a model that keeps calling forbidden tools still hits the cap.
There are no approval tools and no status setters: PO/lead tools create *proposals* only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.domain import Forbidden, Invalid, Workflow
from app.persistence.models import Project, Ticket, TicketVersion
from app.workers.runtime import RunContext

from .threads import Threads

TOOL_POLICY: dict[str, frozenset[str]] = {
    "po": frozenset({"read_brief", "read_criteria", "propose_ticket", "propose_criteria", "send_message",
                     "request_input"}),
    "technical-lead": frozenset({"read_repo", "propose_decision", "plan_dependencies", "review_candidate",
                                 "answer_message", "send_message"}),
    "developer": frozenset({"read_file", "patch_file", "run_command", "inspect_diff", "submit_candidate",
                            "send_message", "request_decision"}),
    "qa": frozenset({"read_criteria", "propose_tests", "request_test_run", "inspect_app", "report_bug",
                     "send_message"}),
}
# Allowed by policy but implemented by later tickets (workspace/Hermes/QA harness, DEV-010): they fail explicitly.
NOT_WIRED = frozenset({"read_repo", "review_candidate", "read_file", "patch_file", "run_command", "inspect_diff",
                       "submit_candidate", "propose_tests", "request_test_run", "inspect_app", "report_bug"})
FORBIDDEN_ARGS = frozenset({"actor", "role", "user", "sender", "project_id", "job_id", "generation", "lease",
                            "lease_owner", "attempt", "scope_version", "ticket_id_override"})


class NotWired(RuntimeError):
    """The operation is part of the role's policy but its implementation arrives in a later ticket."""


class ToolFacade:
    def __init__(self, db, workflow: Workflow, threads: Threads):
        self.db, self.workflow, self.threads = db, workflow, threads
        self._handlers: dict[str, Callable[[RunContext, dict[str, Any], dict[str, Any]], Any]] = {
            "read_brief": self._read_brief, "read_criteria": self._read_criteria,
            "propose_ticket": self._propose_ticket, "propose_criteria": self._propose_criteria,
            "send_message": self._send_message, "request_input": self._request_input,
            "request_decision": self._request_decision, "propose_decision": self._propose_decision,
            "plan_dependencies": self._plan_dependencies, "answer_message": self._answer_message,
        }

    def allowed(self, role: str) -> frozenset[str]:
        return TOOL_POLICY.get(role, frozenset())

    def call(self, ctx: RunContext, name: str, args: dict[str, Any] | None = None) -> Any:
        """Run one tool call as the attempt behind ctx. The budget is reserved before anything else."""
        args = {} if args is None else args
        return ctx.tool_call(name, lambda: self._execute(ctx, name, args))

    def _execute(self, ctx: RunContext, name: str, args: dict[str, Any]) -> Any:
        identity = ctx.queue.verify(ctx.lease)  # fence: revoked/expired/replaced attempts stop here
        if name not in self.allowed(identity["role"]):
            raise Forbidden(f"role {identity['role']} has no tool {name}")
        if not isinstance(args, dict):
            raise Invalid("tool arguments must be an object")
        if FORBIDDEN_ARGS & set(args):
            raise Invalid("identity and permissions come from the run, not from tool arguments")
        if name in NOT_WIRED:
            raise NotWired(f"{name} is wired in DEV-010 (workspace/QA harness)")
        args = self.threads.redactor.redact_value(args)
        return self.threads.redactor.redact_value(self._handlers[name](ctx, identity, args))

    @staticmethod
    def _key(identity: dict[str, Any], name: str, args: dict[str, Any]) -> str:
        """Retry-stable key: the same call from a retried job (same root job) maps to the same effect."""
        digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:24]
        return f"tool:{identity['root_job_id']}:{name}:{digest}"

    # -- reads ---------------------------------------------------------------------------------------------------
    def _read_brief(self, ctx, identity, args):
        with self.db.read() as s:
            project = s.get(Project, identity["project_id"])
            return {"brief": project.brief, "brief_version": project.brief_version, "mode": project.mode}

    def _read_criteria(self, ctx, identity, args):
        if identity["ticket_id"] is None:
            raise Invalid("this run has no ticket")
        with self.db.read() as s:
            version = s.query(TicketVersion).filter_by(ticket_id=identity["ticket_id"],
                                                       version=identity["scope_version"]).one()
            return {"ticket_id": identity["ticket_id"], "scope_version": version.version, "title": version.title,
                    "uac": version.uac}

    # -- proposals (never decisions) -------------------------------------------------------------------------------
    def _propose_ticket(self, ctx, identity, args):
        ticket = self.workflow.create_ticket(ctx.actor(), args, idempotency_key=self._key(identity, "propose_ticket", args))
        return {"ticket_id": ticket.id, "phase": ticket.phase, "scope_version": ticket.current_version,
                "approved": False}

    def _propose_criteria(self, ctx, identity, args):
        if identity["ticket_id"] is None or set(args) != {"uac"}:
            raise Invalid("propose_criteria needs a ticket run and only a 'uac' list")
        with self.db.read() as s:
            t = s.get(Ticket, identity["ticket_id"])
            version = s.query(TicketVersion).filter_by(ticket_id=t.id, version=t.current_version).one()
            current, revision = {"title": version.title, "description": version.description,
                                 "dependencies": version.scope.get("dependencies", []), "uac": args["uac"]}, t.revision
        proposal_id = self.workflow.propose_scope(ctx.actor(), identity["ticket_id"], revision, current,
                                                  idempotency_key=self._key(identity, "propose_criteria", args))
        return {"proposal_id": proposal_id, "approved": False}

    def _plan_dependencies(self, ctx, identity, args):
        if set(args) - {"depends_on_ticket_ids", "reason"} or not isinstance(args.get("depends_on_ticket_ids"), list):
            raise Invalid("plan_dependencies needs depends_on_ticket_ids (and optionally reason)")
        # A dependency change is a scope change: the lead records a proposal for the PO/user, never applies it.
        result = self.threads.send(identity, to_role=None, thread_id=f"plan:{identity['ticket_id'] or identity['project_id']}",
                                   category="note", body=json.dumps(args, sort_keys=True), intent="dependency_plan",
                                   idempotency_key=self._key(identity, "plan_dependencies", args))
        return {"message_id": result.message_id, "applied": False}

    def _propose_decision(self, ctx, identity, args):
        if set(args) != {"title", "rationale"}:
            raise Invalid("propose_decision needs title and rationale")
        return {"proposal_id": self.threads.propose_decision(
            identity, title=args["title"], rationale=args["rationale"], key=self._key(identity, "propose_decision", args)),
            "accepted": False}

    # -- messages ----------------------------------------------------------------------------------------------------
    def _send_message(self, ctx, identity, args):
        allowed = {"to_role", "category", "body", "thread_id", "needs_reply", "expected", "refs"}
        if set(args) - allowed or {"category", "body"} - set(args):
            raise Invalid("send_message needs category and body; allowed fields: " + ", ".join(sorted(allowed)))
        thread = args.get("thread_id") or f"ticket:{identity['ticket_id'] or identity['project_id']}"
        result = self.threads.send(identity, to_role=args.get("to_role"), thread_id=thread, category=args["category"],
                                   body=args["body"], refs=args.get("refs"), needs_reply=bool(args.get("needs_reply")),
                                   expected=args.get("expected", "short"),
                                   idempotency_key=self._key(identity, "send_message", args))
        return {"message_id": result.message_id, "reply_job_id": result.reply_job_id}

    def _request_input(self, ctx, identity, args):
        if set(args) != {"question"}:
            raise Invalid("request_input needs a question")
        ctx.request_input(args["question"], {"tool": "request_input"}, self._key(identity, "request_input", args))

    def _request_decision(self, ctx, identity, args):
        if set(args) - {"question", "to_role", "checkpoint"} or "question" not in args:
            raise Invalid("request_decision needs a question (optional to_role, checkpoint)")
        self.threads.ask(ctx, args.get("to_role", "technical-lead"), args["question"],
                         key=self._key(identity, "request_decision", args), checkpoint=args.get("checkpoint", {}))

    def _answer_message(self, ctx, identity, args):
        if set(args) != {"request_id", "answer"}:
            raise Invalid("answer_message needs request_id and answer")
        message_id, resumed = self.threads.answer_request(identity, args["request_id"], body=args["answer"],
                                                          answer_key=self._key(identity, "answer_message", args))
        return {"answer_id": message_id, "resumed": resumed}
