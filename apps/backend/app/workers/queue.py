"""Persistent job queue: claim/lease/generation, heartbeat, bounded retry, waiting states.

Every write is one short BEGIN IMMEDIATE transaction, so two workers can never hold the same
job, and the single execution slot is counted in the database, not in a process's memory.
A Lease (job, owner, generation) is the attempt's capability: anything an attempt does after
its generation was revoked or its lease expired fails with StaleLease and changes nothing.

Budgets are cumulative per budget key (ticket + scope version, or the first attempt of a
job without a ticket), so a retry continues from the usage already spent instead of
resetting it. Waiting does not consume active time.
"""
from __future__ import annotations

import math
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, or_, select

from app.persistence import (Database, EventSpec, answer_input_request, append_event, append_message,
                             record_usage)
from app.persistence.columns import utcnow
from app.persistence.models import ACTIVE_JOB_STATUSES, Artifact, Job, Message, Ticket

LIMIT_KEYS = ("model_calls", "tool_calls", "active_s")  # always finite and required
OPTIONAL_LIMIT_KEYS = ("output_tokens", "total_tokens")
BIND_STAGES = ("development", "technical_review", "qa")
TERMINAL_STATUSES = ("stopped", "failed", "cancelled", "succeeded")


class QueueError(RuntimeError):
    pass


class StaleLease(QueueError):
    """The attempt no longer owns the job (revoked, replaced, expired or finished)."""


class BudgetExhausted(QueueError):
    def __init__(self, limit: str):
        super().__init__(f"budget exhausted: {limit}")
        self.limit = limit


class QuotaWait(QueueError):
    def __init__(self, retry_at: datetime, reason: str):
        super().__init__(f"provider quota: retry at {retry_at.isoformat()} ({reason})")
        self.retry_at, self.reason = retry_at, reason


@dataclass(frozen=True)
class Lease:
    job_id: str
    owner: str
    generation: int


def _validate_limits(limits: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(limits, dict) or set(limits) - set(LIMIT_KEYS) - set(OPTIONAL_LIMIT_KEYS) - {"budget_key"}:
        raise ValueError(f"limits accept {LIMIT_KEYS + OPTIONAL_LIMIT_KEYS}")
    for key in LIMIT_KEYS:
        if key not in limits:
            raise ValueError(f"finite limit {key} is required")
    for key in LIMIT_KEYS + OPTIONAL_LIMIT_KEYS:
        value = limits.get(key)
        if value is None and key in OPTIONAL_LIMIT_KEYS:
            continue
        integral = key != "active_s"
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value <= 0 or (integral and not isinstance(value, int))):
            raise ValueError(f"limit {key} must be a finite positive {'integer' if integral else 'number'}")
    return dict(limits)


class JobQueue:
    def __init__(self, db: Database, *, lease_s: float = 30.0, max_attempts: int = 2,
                 retry_backoff_s: float = 5.0, startable: Callable | None = None,
                 clock: Callable[[], datetime] = utcnow):
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a finite positive integer")
        if not math.isfinite(lease_s) or lease_s <= 0 or not math.isfinite(retry_backoff_s) or retry_backoff_s < 0:
            raise ValueError("lease and retry timing must be finite and bounded")
        self.db, self.lease_s, self.max_attempts = db, lease_s, max_attempts
        self.retry_backoff_s, self.clock = retry_backoff_s, clock
        # Domain rule for ticket stages (Workflow.startable); claims never bypass it.
        self.startable = startable

    # -- helpers ------------------------------------------------------------------------
    @staticmethod
    def _event(s, job: Job, kind: str, actor: str, **payload) -> None:
        ref = job.runtime_ref or {}
        append_event(s, job.project_id, EventSpec(
            "job." + kind, actor, {**payload, "lane": job.lane, "stage": job.stage, "attempt": job.attempt,
                                    "generation": job.lease_generation, "runtime": ref.get("runtime"),
                                    "fake": bool(ref.get("fake"))},
            entity_type="jobs", entity_id=job.id))

    def _fenced(self, s, lease: Lease) -> Job:
        job = s.get(Job, lease.job_id)
        if (job is None or job.status != "running" or job.lease_owner != lease.owner
                or job.lease_generation != lease.generation):
            raise StaleLease(f"job {lease.job_id} generation {lease.generation} is not the active attempt")
        if job.lease_expires_at is None or job.lease_expires_at <= self.clock():
            raise StaleLease(f"job {lease.job_id} lease expired")
        return job

    @staticmethod
    def _release_lease(job: Job) -> None:
        job.lease_owner, job.lease_expires_at = None, None
        job.revision += 1

    @staticmethod
    def budget_usage(s, job: Job) -> dict[str, float]:
        """Usage summed over every attempt sharing this job's budget key (never reset by retries)."""
        key = job.limits["budget_key"]
        totals: dict[str, float] = {}
        rows = s.scalars(select(Job).where(Job.project_id == job.project_id,
                                          func.json_extract(Job.limits, "$.budget_key") == key))
        for row in rows:
            for name, value in (row.usage or {}).items():
                if name != "_unknown":
                    totals[name] = totals.get(name, 0) + value
        return totals

    @staticmethod
    def _cleanup(job):
        return (job.runtime_ref or {}).get("cleanup")

    @staticmethod
    def _retry_ref(job):
        return {k: v for k, v in job.runtime_ref.items() if k not in ("processes", "resources", "cleanup")}

    def begin_run(self, lease: Lease, host: dict) -> None:
        """Persist ownership before executing. A released lease alone never frees this slot."""
        with self.db.write() as s:
            job = self._fenced(s, lease)
            job.runtime_ref = {**self._retry_ref(job), "cleanup": {
                "generation": lease.generation, "owner": lease.owner, "host": host,
                "expires_at": job.lease_expires_at.isoformat()}}

    def register_resource(self, lease: Lease, resource: dict) -> None:
        with self.db.write() as s:
            job = self._fenced(s, lease)
            self._ensure_cleanup(job)
            resources = list(job.runtime_ref.get("resources", []))
            if resource not in resources:
                resources.append(resource)
            job.runtime_ref = {**job.runtime_ref, "resources": resources}

    def _ensure_cleanup(self, job: Job) -> None:
        # Direct queue/harness callers registering resources must retain ownership too.
        # Supervisor.begin_run additionally records the executing worker's host identity.
        if not self._cleanup(job):
            job.runtime_ref = {**job.runtime_ref, "cleanup": {
                "generation": job.lease_generation, "owner": job.lease_owner,
                "expires_at": job.lease_expires_at.isoformat()}}

    def finish_cleanup(self, job_id: str, generation: int, *, recovery_token: str | None = None) -> str | None:
        with self.db.write() as s:
            job = s.get(Job, job_id)
            cleanup = self._cleanup(job)
            if not cleanup or cleanup["generation"] != generation:
                return None
            if recovery_token is not None and cleanup.get("recovery_token") != recovery_token:
                return None
            job.runtime_ref = {k: v for k, v in job.runtime_ref.items() if k != "cleanup"}
            if (job.result or {}).get("cleanup_error"):
                job.result = {**job.result, "cleanup_error": None,
                              "needs_human": job.result.get("reason") == "budget_exhausted"}
            self._event(s, job, "cleanup_completed", "system:supervisor", cleaned_generation=generation)
            pending = (job.result or {}).get("retry_pending")
            if pending and job.status == "failed":
                job.result = {**job.result, "retry_pending": None}
                return self._after_failure(s, job, pending["error"], pending["retryable"], "system:supervisor")
            return None

    def cleanup_failed(self, job_id: str, error: str) -> None:
        with self.db.write() as s:
            job = s.get(Job, job_id)
            job.result = {**(job.result or {}), "needs_human": True, "cleanup_error": error[:500]}
            self._event(s, job, "cleanup_failed", "system:supervisor", error=error[:500])

    def log_line(self, job_id: str, generation: int, line: str) -> None:
        """Append-only durable log, also accepted after revocation; never a domain result."""
        with self.db.write() as s:
            job = s.get(Job, job_id)
            if job is None or not 1 <= generation <= job.lease_generation:
                raise StaleLease("unknown job generation")
            append_message(s, project_id=job.project_id, thread_id=f"job:{job_id}:g{generation}",
                           ticket_id=job.ticket_id, sender="system:supervisor", body=line,
                           meta={"runtime_log": True, "generation": generation})

    def log_bytes(self, job_id: str, generation: int) -> bytes:
        with self.db.read() as s:
            lines = s.scalars(select(Message).where(Message.thread_id == f"job:{job_id}:g{generation}")
                              .order_by(Message.seq)).all()
            return ("\n".join(m.body for m in lines) + "\n").encode()

    @staticmethod
    def _budget_limit(s, job, totals, *, calls=False):
        # output_tokens is a per-request cap; total_tokens is cumulative input + output.
        keys = LIMIT_KEYS + ("total_tokens",) if calls else ("active_s", "total_tokens")
        return next((key for key in keys if job.limits.get(key) is not None
                     and totals.get(key, 0) >= job.limits[key]), None)

    def _stop_for_budget(self, s, job: Job, limit: str, actor: str) -> None:
        job.status, job.finished_at = "stopped", self.clock()
        job.result = {**(job.result or {}), "reason": "budget_exhausted", "limit": limit, "needs_human": True}
        job.lease_generation += 1  # revoke the run before anything else can act on it
        self._release_lease(job)
        self._event(s, job, "budget_exhausted", actor, limit=limit, usage=self.budget_usage(s, job))

    # -- enqueue / claim ------------------------------------------------------------------
    def enqueue(self, *, project_id: str, lane: str, stage: str, role: str, idempotency_key: str,
                limits: dict[str, Any], runtime: str, ticket_id: str | None = None,
                payload: dict[str, Any] | None = None, actor: str = "system:scheduler") -> Job:
        """Queue work once per idempotency key. runtime='fake' labels the job and its events."""
        limits = _validate_limits(limits)
        if "budget_key" in limits:
            raise QueueError("budget identity is assigned by the scheduler, never by the caller")
        if not runtime:
            raise ValueError("runtime label is required")
        with self.db.write() as s:
            scope_version = None
            if ticket_id is not None:
                ticket = s.get(Ticket, ticket_id)
                if ticket is None or ticket.project_id != project_id or ticket.current_version is None:
                    raise QueueError("ticket must exist in this project and have a scope version")
                scope_version = ticket.current_version
            existing = s.scalar(select(Job).where(Job.project_id == project_id, Job.idempotency_key == idempotency_key))
            ref = {"role": role, "runtime": runtime, "fake": runtime == "fake", "payload": dict(payload or {})}
            budget_key = f"ticket:{ticket_id}:v{scope_version}" if ticket_id else f"job:{idempotency_key}"
            if existing is not None:
                if ((existing.lane, existing.stage, existing.ticket_id, existing.scope_version) !=
                        (lane, stage, ticket_id, scope_version)
                        or any(existing.runtime_ref.get(k) != v for k, v in ref.items())
                        or existing.runtime_ref.get("enqueue_limits", {
                            k: v for k, v in existing.limits.items() if k != "budget_key"}) != limits):
                    raise QueueError("idempotency key reused for different work")
                return existing
            peer = s.scalar(select(Job).where(Job.project_id == project_id,
                            func.json_extract(Job.limits, "$.budget_key") == budget_key).order_by(Job.created_at.desc()))
            if peer is not None and peer.limits != {**limits, "budget_key": budget_key}:
                raise QueueError("scope budget caps must match the existing authorized policy")
            job = Job(project_id=project_id, ticket_id=ticket_id, scope_version=scope_version, lane=lane,
                      stage=stage, idempotency_key=idempotency_key, attempt=1,
                      runtime_ref={**ref, "enqueue_limits": limits}, limits={**limits, "budget_key": budget_key})
            s.add(job)
            s.flush()
            self._event(s, job, "enqueued", actor)
            return job

    def _claimable(self, s, job: Job) -> str:
        if job.ticket_id is None:
            return "ok"
        ticket = s.get(Ticket, job.ticket_id)
        if ticket is None or ticket.current_version != job.scope_version or ticket.phase in ("cancelled", "accepted"):
            return "stale"
        if job.stage in BIND_STAGES:
            if self.startable is None:
                return "wait"  # without the domain rule, ticket work is never started
            return "ok" if self.startable(s, job) else "wait"
        return "ok"

    def claim(self, owner: str, lane: str, *, capacity: int, runtimes: tuple[str, ...]) -> Lease | None:
        """Atomically take the oldest claimable job of a lane if the lane has free capacity.

        Only jobs for a runtime this worker actually has are considered.
        """
        if not runtimes:
            return None
        if capacity < 1 or (lane == "execution" and capacity != 1):
            raise ValueError("MVP execution capacity must be exactly one")
        with self.db.write() as s:
            now = self.clock()
            running = s.scalar(select(func.count()).select_from(Job).where(Job.lane == lane, or_(
                Job.status == "running", func.json_extract(Job.runtime_ref, "$.cleanup").is_not(None))))
            if running >= capacity:
                return None
            queued = s.scalars(select(Job).where(
                Job.status == "queued", Job.lane == lane,
                func.json_extract(Job.runtime_ref, "$.runtime").in_(runtimes),
                or_(Job.available_at.is_(None), Job.available_at <= now)).order_by(Job.created_at, Job.id).limit(100))
            for job in list(queued):
                if self._cleanup(job):
                    continue
                verdict = self._claimable(s, job)
                if verdict == "stale":
                    job.status, job.finished_at = "cancelled", now
                    job.lease_generation += 1
                    job.result = {**(job.result or {}), "reason": "scope_changed"}
                    self._event(s, job, "cancelled", owner, reason="scope_changed")
                    continue
                if verdict != "ok":
                    continue
                limit = self._budget_limit(s, job, self.budget_usage(s, job), calls=True)
                if limit:
                    self._stop_for_budget(s, job, limit, owner)
                    continue
                job.status, job.lease_owner = "running", owner
                job.lease_generation += 1  # every claim is a new generation
                job.lease_expires_at, job.heartbeat_at = now + timedelta(seconds=self.lease_s), now
                job.started_at = job.started_at or now
                job.available_at = None
                job.revision += 1
                s.flush()
                self._event(s, job, "claimed", owner, owner=owner)
                return Lease(job.id, owner, job.lease_generation)
            return None

    def release(self, lease: Lease, reason: str, *, backoff_s: float = 0.0) -> None:
        """Give the job back to the queue (bind refused, graceful shutdown). The generation stays
        bumped, so the released attempt cannot act; the next claim gets a new one."""
        with self.db.write() as s:
            job = self._fenced(s, lease)
            job.status, job.available_at = "queued", self.clock() + timedelta(seconds=backoff_s)
            job.lease_generation += 1
            self._release_lease(job)
            self._event(s, job, "released", lease.owner, reason=reason)

    # -- running attempt ---------------------------------------------------------------------
    def heartbeat(self, lease: Lease, active_delta_s: float) -> str:
        """Extend the lease and charge active time. Returns 'ok', 'revoked' or 'budget_exhausted'."""
        if not math.isfinite(active_delta_s) or active_delta_s < 0:
            raise ValueError("active time cannot be negative")
        with self.db.write() as s:
            try:
                job = self._fenced(s, lease)
            except StaleLease:
                return "revoked"
            record_usage(s, job.id, {"active_s": active_delta_s})
            if self.budget_usage(s, job).get("active_s", 0) >= job.limits["active_s"]:
                self._stop_for_budget(s, job, "active_s", lease.owner)
                return "budget_exhausted"
            now = self.clock()
            job.lease_expires_at, job.heartbeat_at = now + timedelta(seconds=self.lease_s), now
            if self._cleanup(job):
                job.runtime_ref = {**job.runtime_ref, "cleanup": {**self._cleanup(job),
                                    "expires_at": job.lease_expires_at.isoformat()}}
            job.revision += 1
            return "ok"

    def reserve(self, lease: Lease, kind: str) -> None:
        """Count a model or tool call BEFORE it is made; refuses once the scope budget is spent."""
        if kind not in ("model", "tool"):
            raise ValueError("kind must be model or tool")
        limit = kind + "_calls"
        exhausted = False
        with self.db.write() as s:
            job = self._fenced(s, lease)
            exhausted_limit = self._budget_limit(s, job, self.budget_usage(s, job))
            if self.budget_usage(s, job).get(limit, 0) >= job.limits[limit]:
                exhausted_limit = limit
            if exhausted_limit:
                limit = exhausted_limit
                self._stop_for_budget(s, job, limit, lease.owner)
                exhausted = True
            else:
                record_usage(s, job.id, {limit: 1})
        if exhausted:
            raise BudgetExhausted(limit)

    def finalize_usage(self, job_id: str, generation: int, usage: dict[str, float | int | None]) -> None:
        """Accounting for a call already reserved. Accepted from a revoked generation too (the tokens
        were spent). Budget enforcement can stop current scope work, never accept an old
        domain result. None means the provider did not report that amount."""
        usage = dict(usage)
        partial_tokens = False
        for value in usage.values():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError("usage must contain finite nonnegative amounts or unknown values")
        if "output_tokens" in usage or "input_tokens" in usage:
            if usage.get("total_tokens") is None:
                known = [usage.get("input_tokens"), usage.get("output_tokens")]
                reported = [v for v in known if v is not None]
                usage["total_tokens"] = sum(reported) if reported else None
                partial_tokens = bool(reported) and any(v is None for v in known)
        with self.db.write() as s:
            job = s.get(Job, job_id)
            if job is None or generation > job.lease_generation or generation < 1:
                raise StaleLease("unknown job generation")
            if partial_tokens:
                record_usage(s, job_id, {"total_tokens": None})
            record_usage(s, job_id, usage)
            # Late accounting from a revoked run can exhaust the scope's current run too.
            current = s.scalars(select(Job).where(Job.project_id == job.project_id, Job.status.in_(ACTIVE_JOB_STATUSES),
                func.json_extract(Job.limits, "$.budget_key") == job.limits["budget_key"])).all()
            for active in current:
                limit = self._budget_limit(s, active, self.budget_usage(s, active))
                if limit:
                    self._stop_for_budget(s, active, limit, "system:usage")

    def register_process(self, lease: Lease, pgid: int, tag: str) -> None:
        """Record a process group owned by this attempt, so recovery can verify and stop it."""
        with self.db.write() as s:
            job = self._fenced(s, lease)
            if isinstance(pgid, bool) or not isinstance(pgid, int) or pgid < 2 or tag != f"{lease.job_id}:{lease.generation}":
                raise QueueError("process ownership must identify this exact job generation")
            self._ensure_cleanup(job)
            ref = dict(job.runtime_ref)
            ref["processes"] = [*ref.get("processes", []), {"pgid": pgid, "tag": tag, "generation": lease.generation}]
            job.runtime_ref = ref
            job.revision += 1

    def attach_evidence(self, job_id: str, generation: int, artifact_id: str, *, session=None) -> None:
        """Archive a log/evidence artifact on the job; allowed after revocation (evidence only)."""
        with (nullcontext(session) if session is not None else self.db.write()) as s:
            job = s.get(Job, job_id)
            if job is None or generation > job.lease_generation or generation < 1:
                raise StaleLease("unknown job generation")
            artifact = s.get(Artifact, artifact_id)
            if (artifact is None or artifact.project_id != job.project_id or artifact.run_id != job.id
                    or artifact.kind != "log" or artifact.meta.get("generation") != generation
                    or artifact.meta.get("producer") != "supervisor"):
                raise QueueError("log evidence must identify this job and generation")
            result = dict(job.result or {})
            result["evidence_artifact_ids"] = list(dict.fromkeys([*result.get("evidence_artifact_ids", []), artifact_id]))
            job.result = result
            job.revision += 1
            self._event(s, job, "evidence_archived", "system:supervisor", artifact_id=artifact_id,
                        archived_generation=generation)

    # -- outcomes ------------------------------------------------------------------------------
    def complete(self, lease: Lease, result: dict[str, Any]) -> None:
        with self.db.write() as s:
            job = self._fenced(s, lease)
            fake = bool(job.runtime_ref.get("fake"))
            job.status, job.finished_at = "succeeded", self.clock()
            # A fake provider can finish a job but never produces real QA evidence.
            job.result = {**(job.result or {}), **result, "fake_provider": fake}
            self._release_lease(job)
            self._event(s, job, "succeeded", lease.owner)

    def fail(self, lease: Lease, error: str, *, retryable: bool) -> str | None:
        """Failed attempt. A retryable failure with attempts left becomes a NEW attempt (job row)
        that shares the budget; otherwise the failure is marked as needing a human."""
        with self.db.write() as s:
            job = self._fenced(s, lease)
            job.status, job.finished_at = "failed", self.clock()
            job.lease_generation += 1
            self._release_lease(job)
            if self._cleanup(job):
                job.result = {**(job.result or {}), "error": error,
                              "retry_pending": {"error": error, "retryable": retryable}}
                self._event(s, job, "failed", lease.owner, error=error, cleanup_pending=True)
                return None
            return self._after_failure(s, job, error, retryable, lease.owner)

    def _after_failure(self, s, job: Job, error: str, retryable: bool, actor: str) -> str | None:
        if (job.result or {}).get("retry_job_id"):
            return job.result["retry_job_id"]
        if retryable and job.attempt < self.max_attempts:
            retry = Job(project_id=job.project_id, ticket_id=job.ticket_id, scope_version=job.scope_version,
                        lane=job.lane, stage=job.stage, parent_job_id=job.id, attempt=job.attempt + 1,
                        idempotency_key=f"{job.idempotency_key}#attempt-{job.attempt + 1}",
                        runtime_ref=self._retry_ref(job),
                        limits=dict(job.limits),
                        available_at=self.clock() + timedelta(seconds=self.retry_backoff_s))
            s.add(retry)
            s.flush()
            job.result = {**(job.result or {}), "error": error, "retry_job_id": retry.id}
            self._event(s, job, "failed", actor, error=error, retry_job_id=retry.id)
            self._event(s, retry, "retry_scheduled", actor, parent_job_id=job.id)
            return retry.id
        job.result = {**(job.result or {}), "error": error, "needs_human": True}
        self._event(s, job, "needs_human", actor, error=error, retryable=retryable)
        return None

    # -- waiting states ----------------------------------------------------------------------
    def request_input(self, lease: Lease, *, question: str, checkpoint: dict[str, Any], request_key: str) -> str:
        """Persist the question and checkpoint, then release the slot: all in one transaction."""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question required")
        with self.db.write() as s:
            job = self._fenced(s, lease)
            message, _ = append_message(
                s, project_id=job.project_id, thread_id=f"job:{job.id}", ticket_id=job.ticket_id,
                sender=f"agent:{job.runtime_ref['role']}", recipient="user", kind="input_request", body=question,
                idempotency_key=f"input:{job.id}:{request_key}",
                meta={"job_id": job.id, "generation": lease.generation, "scope_version": job.scope_version,
                      "checkpoint": checkpoint})
            job.status, job.waiting_request_id = "waiting_input", message.id
            job.result = {**(job.result or {}), "checkpoint": checkpoint}
            self._release_lease(job)
            self._event(s, job, "waiting_input", lease.owner, request_id=message.id)
            return message.id

    def answer(self, request_id: str, *, body: str, answer_key: str, user: str) -> tuple[str, bool]:
        """Persist a user answer once. Only the first answer re-queues the job (resumed as a new
        generation by the next claim); retries are no-ops. A changed scope cancels instead."""
        with self.db.write() as s:
            message, created = answer_input_request(s, request_id=request_id, sender=user, body=body,
                                                    answer_key=answer_key)
            job = s.scalar(select(Job).where(Job.waiting_request_id == request_id))
            if not created or job is None or job.status != "waiting_input":
                return message.id, False
            ticket = s.get(Ticket, job.ticket_id) if job.ticket_id else None
            if ticket is not None and ticket.current_version != job.scope_version:
                job.status, job.finished_at = "cancelled", self.clock()
                job.lease_generation += 1
                job.result = {**(job.result or {}), "reason": "scope_changed_while_waiting"}
                self._event(s, job, "cancelled", user, reason="scope_changed_while_waiting")
                return message.id, False
            job.status, job.available_at = "queued", None
            job.revision += 1
            self._event(s, job, "resumed", user, answer_id=message.id)
            return message.id, True

    def wait_quota(self, lease: Lease, quota: QuotaWait) -> None:
        with self.db.write() as s:
            job = self._fenced(s, lease)
            job.status, job.available_at = "waiting_quota", quota.retry_at
            job.result = {**(job.result or {}), "quota": {"retry_at": quota.retry_at.isoformat(), "reason": quota.reason}}
            job.lease_generation += 1
            self._release_lease(job)
            self._event(s, job, "waiting_quota", lease.owner, retry_at=quota.retry_at.isoformat(), reason=quota.reason)

    def promote_quota_waiters(self) -> list[str]:
        with self.db.write() as s:
            now = self.clock()
            ready = s.scalars(select(Job).where(Job.status == "waiting_quota", Job.available_at <= now)).all()
            for job in ready:
                job.status = "queued"
                job.revision += 1
                self._event(s, job, "quota_retry", "system:scheduler")
            return [job.id for job in ready]

    # -- cancellation and recovery ------------------------------------------------------------
    def cancel(self, job_id: str, *, reason: str, actor: str) -> bool:
        """Revoke first: generation bump + lease removal. Supervisors observe it and stop processes."""
        with self.db.write() as s:
            job = s.get(Job, job_id)
            if job is None or job.status not in ACTIVE_JOB_STATUSES:
                return False
            job.status, job.finished_at = "cancelled", self.clock()
            job.lease_generation += 1
            self._release_lease(job)
            self._event(s, job, "cancellation_requested", actor, reason=reason)
            return True

    def expired(self) -> list[str]:
        with self.db.read() as s:
            return [job.id for job in s.scalars(select(Job)) if (
                job.status == "running" and job.lease_expires_at <= self.clock()) or (
                self._cleanup(job) and datetime.fromisoformat(self._cleanup(job)["expires_at"]) <= self.clock())]

    def recover(self, job_id: str, reap: Callable[[dict[str, Any]], bool], *, actor: str) -> str | None:
        """Reconcile a job whose owner stopped heartbeating.

        1. Fence it (generation bump) so the old executor can no longer act.
        2. Stop leftover processes it recorded, after verifying they are really this job's.
        3. Only then schedule a retry (bounded); if the processes cannot be verified, or no
           attempts are left, the failure is marked as needing a human.
        The cleanup ledger survives a crash between fencing, reaping and retry creation.
        Waiting jobs are cleaned without replaying their unanswered work.
        """
        with self.db.write() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            cleanup = self._cleanup(job)
            if cleanup:
                if datetime.fromisoformat(cleanup["expires_at"]) > self.clock():
                    return None
            elif job.status != "running" or job.lease_expires_at > self.clock():
                return None
            else:
                cleanup = {"generation": job.lease_generation, "owner": job.lease_owner}
            if job.status == "running":
                dead_owner = job.lease_owner
                job.status, job.finished_at = "failed", self.clock()
                job.lease_generation += 1
                self._release_lease(job)
                job.result = {**(job.result or {}), "reason": "lease_expired", "previous_owner": dead_owner,
                              "retry_pending": {"error": "lease_expired", "retryable": True}}
                self._event(s, job, "lease_expired", actor, previous_owner=dead_owner)
            token = uuid.uuid4().hex
            cleanup = {**cleanup, "recovery_token": token,
                       "expires_at": (self.clock() + timedelta(seconds=self.lease_s)).isoformat()}
            job.runtime_ref = {**job.runtime_ref, "cleanup": cleanup}
            snapshot = {"id": job.id, "project_id": job.project_id, "runtime_ref": dict(job.runtime_ref)}
        try:
            reconciled = reap(snapshot)
        except Exception as exc:
            self.cleanup_failed(job_id, f"reconcile failed: {exc!r}")
            return None
        with self.db.write() as s:
            job = s.get(Job, job_id)
            if not self._cleanup(job) or self._cleanup(job).get("recovery_token") != token:
                return None  # another reconciler/finishing owner superseded this operation
            if not reconciled:
                job.result = {**(job.result or {}), "needs_human": True, "error": "could not verify/stop old processes"}
                self._event(s, job, "needs_human", actor, error="reconcile_failed")
                return None
        return self.finish_cleanup(job_id, cleanup["generation"], recovery_token=token)

    # -- budget decisions ---------------------------------------------------------------------
    def extend_budget(self, job_id: str, *, user: str, additions: dict[str, float], authorization_id: str) -> str:
        """User decision after budget exhaustion: a new attempt with raised caps. Usage is kept."""
        if not user.startswith("user:") or not authorization_id:
            raise QueueError("only an explicit user authorization can extend a budget")
        if not additions:
            raise QueueError("budget extension requires a positive addition")
        with self.db.write() as s:
            job = s.get(Job, job_id)
            if job is None or job.status != "stopped" or (job.result or {}).get("reason") != "budget_exhausted":
                raise QueueError("only a budget-exhausted job can be extended")
            key = f"{job.idempotency_key}#budget-{authorization_id}"
            existing = s.scalar(select(Job).where(Job.project_id == job.project_id, Job.idempotency_key == key))
            if existing is not None:
                if existing.runtime_ref.get("budget_authorization") != {
                    "user": user, "additions": additions, "id": authorization_id}:
                    raise QueueError("authorization reused with a different decision")
                return existing.id
            limits = dict(job.limits)
            for name, value in additions.items():
                if (name not in LIMIT_KEYS + OPTIONAL_LIMIT_KEYS or limits.get(name) is None or isinstance(value, bool)
                        or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                    raise QueueError("additions must be positive amounts of known limits")
                limits[name] = limits[name] + value
            _validate_limits(limits)
            retry = Job(project_id=job.project_id, ticket_id=job.ticket_id, scope_version=job.scope_version,
                        lane=job.lane, stage=job.stage, parent_job_id=job.id, attempt=job.attempt + 1,
                        idempotency_key=key, limits=limits,
                        runtime_ref={**self._retry_ref(job), "budget_authorization": {
                            "user": user, "additions": additions, "id": authorization_id}})
            # The scope policy is shared, including concurrently queued stages.
            for peer in s.scalars(select(Job).where(Job.project_id == job.project_id,
                                  func.json_extract(Job.limits, "$.budget_key") == limits["budget_key"])):
                peer.limits = dict(limits)
            s.add(retry)
            s.flush()
            self._event(s, retry, "budget_extended", user, parent_job_id=job.id, additions=additions,
                        authorization_id=authorization_id)
            return retry.id
