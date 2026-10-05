"""Supervisor-owned integration of accepted UAT candidates into the managed `refs/heads/accepted`.

Git and SQLite are not atomic together. `accept_uat` already persisted a pending operation (expected base SHA,
target SHA, approval). This integrator, under a per-project lock and the broker's ref lock, reads the ACTUAL ref and:

  ref == expected base  -> compare-and-swap fast-forward to the target, then finalise in the DB;
  ref == target         -> the ref moved before a crash: finalise only (no second update);
  ref == DB tip (other) -> another integration finished first: this candidate is stale, rebase + new QA/UAT;
  anything else         -> blocked with evidence. Nothing is reset, and an unknown tip is never adopted.

Every outcome records an evidence report (pinned via a message attachment). The developer broker cannot reach this
code path: it only writes attempt refs, and accepted only moves here.
"""
from __future__ import annotations

import fcntl
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select

from app.domain import Actor
from app.domain.types import Conflict, Invalid
from app.persistence import ArtifactUnavailable, NotFound, RevisionConflict, append_message
from app.persistence.columns import utcnow
from app.persistence.models import Approval, Candidate, Job, Ticket
from app.persistence.transactions import bind_service
from app.workspace import WorkspaceSupervisor
from app.workspace.errors import GitBrokerError
from app.workspace.gitbroker import ACCEPTED_REF, is_sha


class SimulatedCrash(RuntimeError):
    """Raised by the test fault hook to stop the integrator at an exact point."""


class Integrator:
    def __init__(self, db, store, workflow, root, *, owner: str | None = None, fault=None):
        self.db, self.store, self.workflow = db, store, workflow
        self.workspace = WorkspaceSupervisor(Path(root))
        self.owner = owner or f"integrator:{socket.gethostname()}"
        self.fault = fault  # test hook: fault(point, plan) may raise SimulatedCrash
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="integrator")
        self._busy = threading.Lock()

    # -- scheduling ----------------------------------------------------------------------------
    def tick(self) -> None:
        """Supervisor maintenance hook; Git work runs on one background thread."""
        if not self._busy.acquire(blocking=False):
            return

        def work():
            try:
                self.run_once()
            finally:
                self._busy.release()
        try:
            self._pool.submit(work)
        except BaseException:
            self._busy.release()
            raise

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)

    def pending(self) -> list[str]:
        """Tickets with a pending operation, oldest acceptance first (serial per project)."""
        with self.db.read() as s:
            rows = []
            frozen = set(s.scalars(select(Job.project_id).where(
                Job.stage == "release", Job.status.in_(("queued", "running", "waiting_input", "waiting_quota")))))
            for t in s.scalars(select(Ticket).where(Ticket.phase == "integrating")):
                if t.project_id in frozen:
                    continue  # release freeze: the operation stays pending and its ticket joins the next release
                c = s.get(Candidate, t.workflow.get("candidate_id")) if t.workflow.get("candidate_id") else None
                if c is not None and (c.integration or {}).get("status") == "pending":
                    approval = s.get(Approval, c.integration.get("approval_id"))
                    rows.append((approval.created_at if approval else t.created_at, t.id))
        return [tid for _, tid in sorted(rows)]

    def run_once(self) -> list[tuple[str, str]]:
        outcomes = []
        for ticket_id in self.pending():
            try:
                outcomes.append((ticket_id, self.integrate(ticket_id)))
            except SimulatedCrash:
                raise
            except RevisionConflict:
                outcomes.append((ticket_id, "retry"))  # the ticket changed meanwhile; re-plan next tick
        return outcomes

    # -- one operation -------------------------------------------------------------------------
    def _actor(self, project_id: str) -> Actor:
        return Actor(self.owner, "integrator", project_id)

    def _fault(self, point: str, plan: dict) -> None:
        if self.fault is not None:
            self.fault(point, plan)

    @contextmanager
    def _project_lock(self, project_id: str):
        directory = self.workspace.root / project_id
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "integration.lock", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def integrate(self, ticket_id: str) -> str:
        with self.db.read() as s:
            t = s.get(Ticket, ticket_id)
            if t is None or t.phase != "integrating":
                return "skipped"
            project_id = t.project_id
            raw = s.get(Candidate, t.workflow.get("candidate_id"))
            raw_op = dict(raw.integration or {})
            has_repo = (self.workspace.root / project_id / "repo.git").exists()
        actor = self._actor(project_id)
        identity = {"ticket_id": ticket_id, "candidate_id": raw.id, "operation_id": raw_op.get("operation_id"),
                    "expected_base": raw_op.get("expected_base"), "target_sha": raw_op.get("target_sha")}
        with self._project_lock(project_id):
            try:
                plan = self.workflow.integration_plan(actor, ticket_id)
            except (Conflict, Invalid, ArtifactUnavailable, NotFound) as exc:
                return self._block(actor, identity, f"integration preflight failed: {exc}", None,
                                   {"check": "preflight", "error": str(exc)[:500]})
            if not has_repo:
                return self._block(actor, identity, "project has no managed repository", None, {"check": "repository"})
            broker = self.workspace.broker(project_id)
            facts = {"db_tip_before": plan["db_tip"], "expected_base": plan["expected_base"], "target_sha": plan["target_sha"]}
            with broker._ref_lock():  # the same lock attempt commits take: no ref moves underneath us
                try:
                    observed = broker.accepted_sha()
                except (GitBrokerError, subprocess.SubprocessError, OSError) as exc:
                    return self._block(actor, plan, f"cannot read accepted ref: {exc}", None, facts)
                facts["observed_before"] = observed
                if observed == plan["target_sha"]:
                    if plan["db_tip"] != plan["expected_base"]:
                        return self._block(actor, plan, "recovery database base differs from the pending operation", observed, facts)
                    if not self._is_ancestor(broker, plan["expected_base"], observed):
                        return self._block(actor, plan, "recovery target is not a fast-forward of the accepted base", observed, facts)
                    outcome = "recovered"  # the ref already moved (crash after update): finalise only
                elif observed == plan["expected_base"]:
                    if plan["db_tip"] != plan["expected_base"]:
                        return self._block(actor, plan, "database accepted tip moved but Git did not", observed, facts)
                    if not self._commit_exists(broker, plan["target_sha"]):
                        return self._block(actor, plan, "approved candidate commit is missing from the managed repository",
                                           observed, facts)
                    if not self._is_ancestor(broker, plan["expected_base"], plan["target_sha"]):
                        return self._block(actor, plan, "candidate is not a fast-forward of the accepted base", observed, facts)
                    self._fault("before_ref_update", plan)
                    # Compare-and-swap: git refuses unless accepted still points at the expected base.
                    broker._bare("update-ref", "-m", f"integrate {plan['operation_id']}", ACCEPTED_REF,
                                 plan["target_sha"], plan["expected_base"])
                    self._fault("after_ref_update", plan)
                    observed, outcome = plan["target_sha"], "updated"
                elif observed == plan["db_tip"] and plan["db_tip"] != plan["expected_base"]:
                    facts.update(outcome="stale_base")
                    with self.db.write() as s:
                        report = self._evidence(s, plan, project_id, "stale_base", facts)
                        bind_service(self.workflow, s).integration_diverged(actor, ticket_id, plan["revision"],
                            plan["candidate_id"], plan["operation_id"], observed)
                        self._record(s, plan, project_id, report, "Accepted base moved first; candidate goes back for rebase.")
                    return "stale_base"
                else:
                    if self._awaiting_recovery(project_id, plan, observed):
                        return "retry"
                    return self._block(actor, plan, "accepted ref diverged from both the expected base and the target",
                                       observed, facts)
                facts.update(observed_after=broker.accepted_sha(), outcome=outcome, finalised_at=utcnow().isoformat())
                with self.db.write() as s:
                    report = self._evidence(s, plan, project_id, outcome, facts)
                    bind_service(self.workflow, s).finish_integration(actor, ticket_id, plan["revision"],
                        plan["candidate_id"], plan["operation_id"], observed)
                    self._record(s, plan, project_id, report, "Accepted ref fast-forwarded and recorded.")
            return outcome

    # -- helpers -------------------------------------------------------------------------------
    @staticmethod
    def _git(broker, *args) -> int:
        return subprocess.run([broker.git, "--git-dir", str(broker.repo), *args], env=broker._env(),
                              capture_output=True, timeout=60).returncode

    def _commit_exists(self, broker, sha: str) -> bool:
        return is_sha(sha) and self._git(broker, "cat-file", "-e", f"{sha}^{{commit}}") == 0

    def _is_ancestor(self, broker, ancestor: str, descendant: str) -> bool:
        return self._git(broker, "merge-base", "--is-ancestor", ancestor, descendant) == 0

    def _awaiting_recovery(self, project_id, plan, observed):
        """Another pending operation owns the Git-ahead-of-DB state; reconcile it before this ticket."""
        with self.db.read() as s:
            for ticket in s.scalars(select(Ticket).where(Ticket.project_id == project_id, Ticket.phase == "integrating")):
                c = s.get(Candidate, ticket.workflow.get("candidate_id"))
                op = (c.integration or {}) if c else {}
                if (ticket.id != plan["ticket_id"] and op.get("status") == "pending"
                        and op.get("expected_base") == plan["db_tip"] and op.get("target_sha") == observed):
                    return True
        return False

    def _evidence(self, s, plan: dict, project_id: str, outcome: str, facts: dict) -> str:
        artifact = self.store.put_json(s, project_id=project_id, kind="report", name="integration.json",
            document={"kind": "integration", "outcome": outcome, "integrator": self.owner,
                      **{k: plan.get(k) for k in ("ticket_id", "candidate_id", "operation_id", "approval_id")},
                      **facts}, meta={"producer": "integrator"})
        return artifact.id

    def _record(self, s, plan, project_id, evidence_id, body):
        candidate = s.get(Candidate, plan["candidate_id"])
        candidate.integration = {**candidate.integration, "evidence_artifact_id": evidence_id}
        self._note(s, project_id, plan["ticket_id"], evidence_id, body)

    def _note(self, s, project_id: str, ticket_id: str, evidence_id: str, body: str) -> None:
        # The attachment pins the evidence in the same transaction as the terminal operation.
        append_message(s, project_id=project_id, ticket_id=ticket_id, thread_id="integration:" + ticket_id,
                       sender=self.owner, kind="system", body=body, attachment_ids=[evidence_id],
                       meta={"intent": "integration_evidence", "evidence_artifact_id": evidence_id})

    def _block(self, actor, plan: dict, reason: str, observed, facts: dict) -> str:
        facts = {**facts, "outcome": "blocked", "reason": reason}
        project_id = actor.project_id
        with self.db.write() as s:
            evidence = self._evidence(s, plan, project_id, "blocked", facts)
            revision = s.get(Ticket, plan["ticket_id"]).revision
            bind_service(self.workflow, s).integration_blocked(actor, plan["ticket_id"], revision, plan["candidate_id"],
                plan["operation_id"], reason=reason, observed_tip=observed, evidence_artifact_id=evidence)
            self._record(s, plan, project_id, evidence, "Integration blocked: " + reason)
        return "blocked"
