"""Context builder: layered, bounded, hashed, and always rebuilt from the database.

Order follows ARCHITECTURE section 8: role instructions, approved scope, project knowledge (brief and
ACCEPTED decisions only), dependency pins, repository references, then the volatile tail (recent messages,
summary of older history, the task and the run identity). Stable parts come first so a provider cache can
reuse the prefix. Token counts are ESTIMATES (characters/4). Nothing here deletes or rewrites history:
messages that do not fit are replaced by a valid summary that references them, and anything left out
without a summary is reported as a gap instead of being hidden. Resume rebuilds everything from
persistence, never from the memory of an earlier process.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.persistence import ArtifactStore, Database
from app.persistence.artifacts import canonical_json
from app.persistence.models import Approval, Dependency, Message, Project, Ticket, TicketVersion

from .redaction import Redactor
from .souls import AgentDefinition
from .threads import PROPOSAL_INTENTS, Threads

CHAT_KINDS = ("message", "input_request", "input_answer")
SYSTEM_INTENTS = ("scope_decision", "decision_accepted", "decision_rejected")
NEEDS_APPROVED_SCOPE = ("technical-lead", "developer", "qa")
RUNTIME_GAP = {"layer": "runtime_transcript", "reason": "managed by the runtime, not duplicated here"}


class ContextRefused(ValueError):
    """The run must not start with this context (for example work on a scope the user has not approved)."""


class ContextTooLarge(ValueError):
    """The parts that may never be dropped do not fit the token limit."""


@dataclass(frozen=True)
class ContextLimits:
    total_tokens: int = 8000
    decisions_tokens: int = 800
    dependencies_tokens: int = 600
    repo_tokens: int = 1200
    messages_tokens: int = 2400
    summary_tokens: int = 500
    message_chars: int = 1200  # one long message cannot take the whole budget


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass
class _Layer:
    name: str
    header: str
    items: list[tuple[dict[str, Any], str]] = field(default_factory=list)  # (reference, text)
    cap: int | None = None
    droppable: str | None = None  # "oldest" | "last" | None (never dropped)
    omitted: list[dict[str, Any]] = field(default_factory=list)

    def text(self) -> str:
        if not self.items:
            return ""
        return self.header + "\n" + "\n".join(t for _, t in self.items) + "\n"

    def tokens(self) -> int:
        return estimate_tokens(self.text())

    def drop_one(self) -> bool:
        if not self.items or self.droppable is None:
            return False
        ref, _ = self.items.pop(0 if self.droppable == "oldest" else -1)
        self.omitted.append(ref)
        return True

    def fit(self) -> None:
        while self.cap is not None and self.tokens() > self.cap and self.drop_one():
            pass


@dataclass(frozen=True)
class ContextSnapshot:
    system: str
    user: str
    manifest: dict[str, Any]
    sha256: str
    prefix_sha256: str
    estimated_tokens: int
    artifact_id: str | None = None


class ContextBuilder:
    def __init__(self, db: Database, store: ArtifactStore, agents: dict[str, AgentDefinition], redactor: Redactor,
                 *, limits: ContextLimits | None = None):
        self.db, self.store, self.agents = db, store, agents
        self.redactor, self.limits = redactor, limits or ContextLimits()

    # -- public ------------------------------------------------------------------------------------------------
    def build(self, identity: dict[str, Any], *, task: dict[str, Any], repo_refs: list[dict[str, Any]] | None = None,
              answer: str | None = None, lease=None, queue=None) -> ContextSnapshot:
        role, agent = identity["role"], self.agents[identity["role"]]
        with self.db.read() as s:
            scope = self._scope(s, identity)
            if role in NEEDS_APPROVED_SCOPE:
                if scope is None:
                    raise ContextRefused(f"the {role} role works on a ticket; this run has none")
                if not scope.items[0][0]["approved"]:
                    raise ContextRefused(f"the {role} role works only on a scope the user has approved")
            project, decisions = self._project(s, identity), self._decisions(s, identity)
            deps, repo = self._dependencies(s, identity), self._repo(repo_refs or [])
            messages, summaries, invalid = self._history(s, identity)
        summary = _Layer("history_summary", "## Summary of older messages (derived; originals are kept)",
                         cap=self.limits.summary_tokens, droppable="oldest")
        stable = [layer for layer in (scope, project, decisions, deps, repo) if layer is not None]
        tail = [summary, messages]
        task_text = self._task(task, answer)
        run_text = (f"## Run\njob={identity['job_id']} attempt={identity['attempt']} generation="
                    f"{identity['generation']} stage={identity['stage']} scope_version={identity['scope_version']}\n")
        for layer in (decisions, deps, repo, messages):
            layer.fit()
        self._settle(agent, stable, tail, summaries, task_text + run_text, messages, summary,
                     droppable=(messages, repo, decisions))
        gaps = self._gaps(stable + tail) + [{"layer": "history_summary", "omitted": {"id": i},
                                              "reason": "summary_does_not_match_history"} for i in invalid]
        prefix = "\n".join(layer.text() for layer in stable if layer.items)
        user = self.redactor.redact(f"{prefix}\n" + "\n".join(l.text() for l in tail if l.items)
                                    + f"\n{task_text}\n{run_text}")
        system = self.redactor.redact(agent.text)
        total = estimate_tokens(system) + estimate_tokens(user)
        if total > self.limits.total_tokens:
            raise ContextTooLarge(f"role, scope, dependencies and task need ~{total} tokens, limit "
                                  f"{self.limits.total_tokens}")
        manifest = {
            "schema": 1, "estimated": True, "role": role, "agent_digest": agent.digest,
            "run": {k: identity[k] for k in ("job_id", "project_id", "ticket_id", "scope_version", "stage",
                                              "attempt", "generation")},
            "layers": [{"name": l.name, "estimated_tokens": l.tokens(), "items": [r for r, _ in l.items]}
                       for l in stable + tail],
            "gaps": [*gaps, RUNTIME_GAP], "estimated_tokens": total, "limit_tokens": self.limits.total_tokens}
        digest = hashlib.sha256(canonical_json({"system": system, "user": user})).hexdigest()
        prefix_digest = hashlib.sha256(canonical_json({"system": system, "prefix": self.redactor.redact(prefix)})).hexdigest()
        snapshot = ContextSnapshot(system, user, self.redactor.redact_value(manifest), digest, prefix_digest, total)
        if lease is not None and queue is not None:
            snapshot = self._store(snapshot, identity, lease, queue)
        return snapshot

    # -- fitting -------------------------------------------------------------------------------------------------------
    def _settle(self, agent, stable, tail, summaries, fixed_text, messages, summary, droppable) -> int:
        """Drop the oldest/last droppable items until everything fits; summaries follow what was dropped."""
        while True:
            omitted = {ref["id"] for ref in messages.omitted}
            summary.items, summary.omitted = [], []
            for sm in summaries:
                if omitted & set(sm.meta["message_ids"]):
                    summary.items.append(({"type": "summary", "id": sm.id, "thread": sm.thread_id,
                                           "from_seq": sm.meta["from_seq"], "to_seq": sm.meta["to_seq"],
                                           "message_ids": sm.meta["message_ids"]},
                                          f"- {sm.thread_id}#{sm.meta['from_seq']}-{sm.meta['to_seq']}: {sm.body}"))
            summary.fit()
            total = (estimate_tokens(agent.text) + estimate_tokens(fixed_text)
                     + sum(layer.tokens() for layer in stable + tail))
            if total <= self.limits.total_tokens or not any(layer.drop_one() for layer in droppable):
                return total

    @staticmethod
    def _gaps(layers) -> list[dict[str, Any]]:
        """Omitted items that no valid summary in this context stands in for."""
        summarised = {i for layer in layers if layer.name == "history_summary"
                      for ref, _ in layer.items for i in ref["message_ids"]}
        return [{"layer": layer.name, "omitted": ref, "reason": "token_limit"}
                for layer in layers for ref in layer.omitted
                if not (layer.name == "recent_messages" and ref["id"] in summarised)]

    # -- layers ----------------------------------------------------------------------------------------------------
    def _scope(self, s, identity) -> _Layer | None:
        if not identity["ticket_id"]:
            return None
        ticket = s.get(Ticket, identity["ticket_id"])
        if ticket is None or ticket.project_id != identity["project_id"]:
            raise ContextRefused("ticket does not belong to this project")
        if ticket.current_version != identity["scope_version"]:
            raise ContextRefused("the run's scope version is no longer current")
        version = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == ticket.id,
                                                       TicketVersion.version == identity["scope_version"]))
        approved = s.scalar(select(Approval.id).where(Approval.type == "scope", Approval.ticket_id == ticket.id,
                                                      Approval.scope_version == version.version)) is not None
        lines = [f"Ticket {ticket.id} scope v{version.version} "
                 f"({'APPROVED by the user' if approved else 'NOT approved: a draft for review'}): {version.title}"]
        if version.description:
            lines.append(version.description)
        lines += [f"- {c['id']} [{c.get('mode', 'automated')}]: {c['text']}" for c in version.uac]
        deps = version.scope.get("dependencies", [])
        if deps:
            lines.append("Depends on tickets: " + ", ".join(deps))
        return _Layer("approved_scope" if approved else "draft_scope", "## Scope",
                      [({"type": "ticket_version", "ticket_id": ticket.id, "version": version.version,
                         "digest": version.content_digest, "approved": approved}, "\n".join(lines))])

    @staticmethod
    def _project(s, identity) -> _Layer:
        project = s.get(Project, identity["project_id"])
        text = f"Brief (v{project.brief_version}, mode {project.mode}):\n{project.brief.strip() or '(empty)'}"
        return _Layer("brief", "## Project", [({"type": "project", "id": project.id,
                                                "brief_version": project.brief_version,
                                                "digest": hashlib.sha256(project.brief.encode()).hexdigest()}, text)])

    def _decisions(self, s, identity) -> _Layer:
        layer = _Layer("accepted_decisions", "## Accepted project decisions (user-accepted only)",
                       cap=self.limits.decisions_tokens, droppable="oldest")
        for message in Threads.accepted_decisions(s, identity["project_id"]):
            layer.items.append(({"type": "message", "id": message.id, "intent": "decision_proposal"},
                                f"- [{message.id[:8]}] {message.meta['title']}: {message.meta['rationale']}"))
        return layer

    def _dependencies(self, s, identity) -> _Layer:
        layer = _Layer("dependencies", "## Dependencies (pinned accepted work)", cap=None)
        if not identity["ticket_id"]:
            return layer
        for dep in s.scalars(select(Dependency).where(Dependency.ticket_id == identity["ticket_id"])
                             .order_by(Dependency.id)):
            upstream = s.get(Ticket, dep.depends_on_ticket_id)
            version = s.scalar(select(TicketVersion).where(
                TicketVersion.ticket_id == upstream.id,
                TicketVersion.version == (dep.accepted_scope_version or upstream.current_version)))
            pin = (f"accepted v{dep.accepted_scope_version} candidate {dep.accepted_candidate_id} at {dep.integration_sha}"
                   if dep.accepted_candidate_id else "not accepted yet")
            uac = ", ".join(c["id"] for c in version.uac) if version else "?"
            layer.items.append(({"type": "dependency", "id": dep.id, "upstream": upstream.id, "state": dep.state},
                                f"- {upstream.id} \"{upstream.title}\" [{dep.state}] {pin}; UAC: {uac}"))
        return layer

    def _repo(self, refs) -> _Layer:
        layer = _Layer("repository", "## Repository references (excerpts, not the whole repo)",
                       cap=self.limits.repo_tokens, droppable="last")
        for ref in refs:
            if not isinstance(ref, dict) or not (ref.get("path") or ref.get("artifact_id")):
                raise ContextRefused("a repository reference needs a path or an artifact id")
            label = ref.get("path") or f"artifact {ref['artifact_id']}"
            sha = f" @ {ref['sha']}" if ref.get("sha") else ""
            snippet = str(ref.get("snippet", ""))[:1600]
            text = f"- {label}{sha}" + (f"\n```\n{snippet}\n```" if snippet else "")
            layer.items.append(({"type": "repo", "path": ref.get("path"), "artifact_id": ref.get("artifact_id"),
                                 "sha": ref.get("sha"), "digest": hashlib.sha256(snippet.encode()).hexdigest()}, text))
        return layer

    def _history(self, s, identity):
        """Relevant messages (oldest first), the summaries that match their sources, and the ids of those that do not."""
        ticket_id = identity["ticket_id"]
        rows = list(s.scalars(select(Message).where(Message.project_id == identity["project_id"])
                              .order_by(Message.created_at, Message.thread_id, Message.seq, Message.id)))
        messages = _Layer("recent_messages", "## Recent messages (history is kept in full; proposals are not decisions)",
                          cap=self.limits.messages_tokens, droppable="oldest")
        for m in rows:
            intent = (m.meta or {}).get("intent")
            if intent == "summary":
                continue
            if m.kind not in CHAT_KINDS and not (m.kind == "system" and intent in SYSTEM_INTENTS):
                continue
            # A ticket run sees its own ticket's messages; project-level chat belongs to project runs.
            if m.ticket_id != ticket_id:
                continue
            label = " PROPOSAL, not authoritative" if intent in PROPOSAL_INTENTS else ""
            body = m.body if len(m.body) <= self.limits.message_chars else m.body[: self.limits.message_chars] + " [cut]"
            messages.items.append(({"type": "message", "id": m.id, "thread": m.thread_id, "seq": m.seq},
                                   f"[{m.id[:8]} {m.thread_id}#{m.seq} {m.sender}->{m.recipient or 'all'} "
                                   f"{m.kind}{label}] {body}"))
        valid, invalid = [], []
        for sm in (m for m in rows if (m.meta or {}).get("intent") == "summary" and m.ticket_id == ticket_id):
            ids = sm.meta.get("message_ids", [])
            originals = list(s.scalars(select(Message).where(Message.id.in_(ids)).order_by(Message.seq)))
            digest = hashlib.sha256(json.dumps([[m.id, m.body] for m in originals]).encode()).hexdigest()
            if len(originals) == len(ids) and digest == sm.meta.get("source_digest"):
                valid.append(sm)
            else:
                invalid.append(sm.id)  # reported as a gap: a summary that does not match history is not used
        return messages, valid, invalid

    @staticmethod
    def _task(task: dict[str, Any], answer: str | None) -> str:
        text = "## Task\n" + json.dumps(task, sort_keys=True, ensure_ascii=False, indent=1)
        if answer is not None:
            text += f"\n\n## Answer to your earlier question (valid, resume from your checkpoint)\n{answer}"
        return text

    # -- snapshot artifact ---------------------------------------------------------------------------------------
    def _store(self, snapshot: ContextSnapshot, identity, lease, queue) -> ContextSnapshot:
        document = {"schema": 1, "manifest": snapshot.manifest, "system": snapshot.system, "user": snapshot.user,
                    "sha256": snapshot.sha256, "prefix_sha256": snapshot.prefix_sha256}
        with self.db.write() as s:
            queue.verify_identity(s, identity)
            artifact = self.store.put_json(
                s, project_id=identity["project_id"], kind="context", document=document, run_id=identity["job_id"],
                name=f"context-{identity['job_id'][:12]}-g{identity['generation']}.json",
                meta={"producer": "context-builder", "job_id": identity["job_id"], "generation": identity["generation"],
                      "sha256": snapshot.sha256, "fake": identity["fake"]})
            queue.set_context(lease, artifact.id, session=s)
        return ContextSnapshot(snapshot.system, snapshot.user, snapshot.manifest, snapshot.sha256,
                               snapshot.prefix_sha256, snapshot.estimated_tokens, artifact.id)
