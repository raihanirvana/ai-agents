"""Supervisor: two lanes, non-blocking execution, heartbeat, cancellation and recovery.

Each claimed job runs in its own thread; the supervisor thread only claims, heartbeats and
reconciles, so it keeps serving the interactive lane, heartbeating and noticing
cancellation while a long execution job runs. Cancellation is revoke-first: whoever
cancels bumps the job generation in the database; the heartbeat sees the revoked lease and
then stops the runtime and its process groups, and the available log is archived.
"""
from __future__ import annotations

import socket
import os
import threading
import time
from dataclasses import dataclass, field
import math
from typing import Any

from sqlalchemy import select

from app.persistence import ArtifactStore, Database, RevisionConflict
from app.persistence.models import Job, Message, Ticket

from .limiter import ProviderLimiter
from .queue import BIND_STAGES, BudgetExhausted, JobQueue, Lease, QuotaWait, StaleLease
from .runtime import (Cancelled, Outcome, RunContext, Runtime, WaitingForInput, host_identity,
                      owner_gone, reap_recorded_processes)


@dataclass
class WorkerConfig:
    worker_id: str = field(default_factory=lambda: f"worker:{socket.gethostname()}:{os.getpid()}")
    execution_slots: int = 1  # MVP: one heavy job at a time (counted in the database)
    interactive_slots: int = 2
    heartbeat_s: float = 5.0
    poll_s: float = 0.5
    bind_backoff_s: float = 5.0

    def __post_init__(self):
        if self.execution_slots != 1 or self.interactive_slots < 1:
            raise ValueError("MVP requires one execution slot and positive interactive capacity")
        if any(not math.isfinite(v) or v <= 0 for v in (self.heartbeat_s, self.poll_s)):
            raise ValueError("heartbeat/poll timing must be finite and positive")


class _Handle:
    def __init__(self, lease: Lease, job: dict[str, Any], ctx: RunContext, runtime: Runtime):
        self.lease, self.job, self.ctx, self.runtime = lease, job, ctx, runtime
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.last_charge = time.monotonic()
        self.shutting_down = False
        self.end_state = ""
        self.stop_thread = None
        self.stop_error = None

    def charge_delta(self) -> float:
        with self.lock:
            now = time.monotonic()
            delta, self.last_charge = now - self.last_charge, now
            return delta


class Supervisor:
    def __init__(self, db: Database, store: ArtifactStore, runtimes: dict[str, Runtime], *,
                 queue: JobQueue, limiter: ProviderLimiter, config: WorkerConfig | None = None,
                 workflow=None):
        self.db, self.store, self.runtimes = db, store, dict(runtimes)
        self.queue, self.limiter, self.config, self.workflow = queue, limiter, config or WorkerConfig(), workflow
        self._handles: dict[str, _Handle] = {}
        self._last_heartbeat = 0.0
        self._recovering: dict[str, threading.Thread] = {}

    @property
    def owner(self) -> str:
        return self.config.worker_id

    # -- main loop --------------------------------------------------------------------------
    def tick(self) -> None:
        """One scheduling round. Safe to call repeatedly; never blocks on a running job."""
        self.recover(background=True)
        self.queue.promote_quota_waiters()
        if time.monotonic() - self._last_heartbeat >= self.config.heartbeat_s:
            self.heartbeat()
        self._reap_finished()
        for lane, slots in (("interactive", self.config.interactive_slots),
                            ("execution", self.config.execution_slots)):
            while self.running(lane) < slots:
                lease = self.queue.claim(self.owner, lane, capacity=slots, runtimes=tuple(self.runtimes))
                if lease is None:
                    break
                self._start(lease)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.tick()
            stop.wait(self.config.poll_s)
        self.shutdown()

    def running(self, lane: str | None = None) -> int:
        return sum(1 for h in self._handles.values() if lane is None or h.job["lane"] == lane)

    # -- starting a job ---------------------------------------------------------------------------
    def _snapshot(self, job_id: str) -> tuple[dict[str, Any], str | None]:
        with self.db.read() as s:
            job = s.get(Job, job_id)
            answer = None
            if job.waiting_request_id:
                reply = s.scalar(select(Message).where(Message.reply_to == job.waiting_request_id,
                                                       Message.kind == "input_answer"))
                answer = reply.body if reply else None
            snapshot = {"id": job.id, "project_id": job.project_id, "ticket_id": job.ticket_id,
                        "scope_version": job.scope_version, "lane": job.lane, "stage": job.stage,
                        "runtime_ref": dict(job.runtime_ref), "limits": dict(job.limits)}
            return snapshot, answer

    def _bind(self, lease: Lease, job: dict[str, Any]) -> bool:
        """Bind a ticket stage attempt through the domain (authoritative eligibility)."""
        from app.domain import Actor, Attempt, DomainError
        if self.workflow is None:
            self.queue.release(lease, "no workflow service to bind ticket work", backoff_s=self.config.bind_backoff_s)
            return False
        actor = Actor(f"scheduler:{self.owner}", "scheduler", job["project_id"])
        for _ in range(3):
            with self.db.read() as s:
                revision = s.get(Ticket, job["ticket_id"]).revision
            try:
                self.workflow.bind_attempt(actor, job["ticket_id"], revision,
                                           Attempt(job["id"], lease.generation, job["scope_version"]))
                return True
            except RevisionConflict:
                continue
            except DomainError as exc:
                self.queue.release(lease, f"bind refused: {exc}", backoff_s=self.config.bind_backoff_s)
                return False
        self.queue.release(lease, "ticket kept changing during bind", backoff_s=self.config.bind_backoff_s)
        return False

    def _start(self, lease: Lease) -> None:
        job, answer = self._snapshot(lease.job_id)
        try:
            if job["ticket_id"] and job["stage"] in BIND_STAGES and not self._bind(lease, job):
                return
            self.queue.begin_run(lease, host_identity())
        except StaleLease:
            return
        runtime = self.runtimes[job["runtime_ref"]["runtime"]]
        ctx = RunContext(queue=self.queue, limiter=self.limiter, lease=lease, job=job, answer=answer)
        handle = _Handle(lease, job, ctx, runtime)
        handle.thread = threading.Thread(target=self._execute, args=(handle,), name=f"job-{lease.job_id[:8]}",
                                         daemon=True)
        self._handles[lease.job_id] = handle
        handle.thread.start()

    # -- running a job (job thread) -----------------------------------------------------------------
    def _execute(self, handle: _Handle) -> None:
        ctx, lease = handle.ctx, handle.lease
        try:
            outcome = handle.runtime.run(ctx)
            self._settle(handle, outcome)
        except WaitingForInput:
            handle.end_state = "waiting_input"
        except QuotaWait as quota:
            self._guard(handle, lambda: self.queue.wait_quota(lease, quota), "waiting_quota")
        except BudgetExhausted:
            handle.end_state = "budget_exhausted"
        except (Cancelled, StaleLease):
            handle.end_state = "revoked"
        except Exception as exc:  # a crashing runtime is a transient failure: one bounded retry
            ctx.log(f"runtime crashed: {exc!r}")
            self._guard(handle, lambda: self.queue.fail(lease, f"runtime crashed: {exc!r}"[:500], retryable=True),
                        "failed")
        finally:
            # Cooperative completion does not excuse lingering children.
            try:
                self.queue.finalize_usage(lease.job_id, lease.generation, {"active_s": handle.charge_delta()})
                clean = ctx.stop_resources()
                with handle.lock:
                    stop_thread = handle.stop_thread
                if stop_thread:
                    stop_thread.join()
                archived = self._archive(handle)
                if clean and archived and not handle.stop_error:
                    self.queue.finish_cleanup(lease.job_id, lease.generation)
                else:
                    self.queue.cleanup_failed(lease.job_id, handle.stop_error or "resource cleanup or archive incomplete")
            except Exception as exc:
                self.queue.cleanup_failed(lease.job_id, f"cleanup failed: {exc!r}")

    def _settle(self, handle: _Handle, outcome: Outcome) -> None:
        lease = handle.lease
        delta = handle.charge_delta()
        status = self.queue.heartbeat(lease, delta)
        if status != "ok":  # final active time; may exhaust the budget
            if status == "revoked":
                self.queue.finalize_usage(lease.job_id, lease.generation, {"active_s": delta})
            handle.end_state = "revoked"
            return
        if outcome.status == "succeeded":
            self._guard(handle, lambda: self.queue.complete(lease, outcome.result), "succeeded")
        else:
            self._guard(handle, lambda: self.queue.fail(lease, outcome.error or "failed", retryable=outcome.retryable),
                        "failed")

    def _guard(self, handle: _Handle, action, state: str) -> None:
        try:
            action()
            handle.end_state = state
        except StaleLease:
            # The attempt was revoked meanwhile: its result is rejected, nothing changes.
            handle.end_state = "stale_result_rejected"
            handle.ctx.log("result rejected: attempt no longer holds the lease")

    def _archive(self, handle: _Handle) -> bool:
        """Keep the run log as evidence, whatever way the run ended."""
        handle.ctx.log(f"run ended: {handle.end_state or 'unknown'}")
        return self._archive_snapshot(handle.job, handle.lease.generation, handle.end_state)

    def _archive_snapshot(self, job: dict, generation: int, end_state: str) -> bool:
        ref = job["runtime_ref"]
        try:
            data = self.queue.log_bytes(job["id"], generation)
            with self.db.write() as s:
                artifact = self.store.put_bytes(
                    s, project_id=job["project_id"], kind="log", data=data,
                    name=f"job-{job['id'][:12]}-g{generation}.log", run_id=job["id"],
                    meta={"producer": "supervisor", "job_id": job["id"], "generation": generation,
                          "end_state": end_state, "runtime": ref.get("runtime"), "fake": bool(ref.get("fake"))})
                self.queue.attach_evidence(job["id"], generation, artifact.id, session=s)
            return True
        except Exception as exc:  # evidence loss must be visible, not fatal to the supervisor
            self.queue.cleanup_failed(job["id"], f"archive failed: {exc!r}")
            return False

    def _stop(self, handle: _Handle) -> None:
        """Signal immediately; slow process/container cleanup must not block other heartbeats."""
        def stop():
            try:
                handle.runtime.stop(handle.ctx)
            except Exception as exc:
                handle.stop_error = f"runtime stop failed: {exc!r}"
        with handle.lock:
            if handle.stop_thread:
                return
            handle.ctx.cancelled.set()
            handle.stop_thread = threading.Thread(target=stop, name=f"stop-{handle.lease.job_id[:8]}", daemon=True)
            handle.stop_thread.start()

    # -- supervision ------------------------------------------------------------------------------
    def heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()
        for handle in list(self._handles.values()):
            if not handle.thread.is_alive() or handle.end_state:
                continue
            delta = handle.charge_delta()
            status = self.queue.heartbeat(handle.lease, delta)
            if status == "revoked":
                self.queue.finalize_usage(handle.lease.job_id, handle.lease.generation, {"active_s": delta})
            if status != "ok":  # revoked (cancel/scope change/recovery) or budget exhausted
                handle.ctx.log(f"supervisor stopping run: {status}")
                self._stop(handle)

    def _reap_finished(self) -> None:
        for job_id, handle in list(self._handles.items()):
            if not handle.thread.is_alive():
                self._handles.pop(job_id)

    def _reconcile(self, snapshot: dict) -> bool:
        if not owner_gone(snapshot) or not reap_recorded_processes(snapshot):
            return False
        if snapshot["runtime_ref"].get("resources"):
            runtime = self.runtimes.get(snapshot["runtime_ref"].get("runtime"))
            if not runtime or not hasattr(runtime, "reconcile") or not runtime.reconcile(snapshot):
                return False
        generation = snapshot["runtime_ref"]["cleanup"]["generation"]
        self.queue.log_line(snapshot["id"], generation, "supervisor recovered run after lease/cleanup expiry")
        return self._archive_snapshot(snapshot, generation, "recovered")

    def recover(self, *, background: bool = False) -> list[str]:
        """Reconcile jobs whose owner stopped heartbeating (another worker, or a crashed process)."""
        recovered = []
        self._recovering = {k: t for k, t in self._recovering.items() if t.is_alive()}
        for job_id in self.queue.expired():
            if job_id in self._handles or job_id in self._recovering:
                continue  # ours and still alive: the next heartbeat decides
            if background:
                thread = threading.Thread(target=self.queue.recover, args=(job_id, self._reconcile),
                                          kwargs={"actor": self.owner}, daemon=True)
                self._recovering[job_id] = thread
                thread.start()
            else:
                self.queue.recover(job_id, self._reconcile, actor=self.owner)
            recovered.append(job_id)
        return recovered

    def wait_idle(self, timeout_s: float = 30.0) -> bool:
        """Test/CLI helper: wait until no job thread is running."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._reap_finished()
            if not self._handles:
                return True
            time.sleep(0.02)
        return False

    def shutdown(self, timeout_s: float = 10.0) -> None:
        """Stop our runs and give their jobs back to the queue for another worker."""
        for handle in list(self._handles.values()):
            handle.shutting_down = True
            try:
                self.queue.release(handle.lease, "worker_shutdown")  # revoke before signalling/stopping
            except StaleLease:
                pass
            self._stop(handle)
        for handle in list(self._handles.values()):
            handle.thread.join(timeout_s)
        for thread in self._recovering.values():
            thread.join(timeout_s)
        self._reap_finished()
