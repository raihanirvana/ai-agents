"""Agent-layer tests: real SQLite/artifacts/threads/supervisor; the model is the LABELLED FakeProvider.

Nothing here proves the quality of a real model or provider; it proves the contracts around them.
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

from app.agents import (ContextBuilder, ContextLimits, FakeProvider, ModelClient, ModelRegistry, Redactor,
                        StructuredAgentRuntime, Threads, ToolFacade, load_agents)
from app.persistence.models import Event, Job, Message
from app.workers import JobQueue, Outcome, ProviderLimiter, Supervisor, WorkerConfig
from app.workers.runtime import RunContext
from tests.domain.conftest import SCOPE, World
from tests.persistence.conftest import db, db_path, store  # noqa: F401  (fixtures)

LIMITS = {"model_calls": 6, "tool_calls": 8, "active_s": 60, "output_tokens": 1024}
CONFIG = {"default": {"provider": "fake", "model": "fake-model", "timeout_s": 5, "max_output_tokens": 512}}
SECRET = "sk-test-0123456789abcdefghijkl"


def proposal(*tickets, summary="Breakdown", assumptions=()):
    return {"kind": "proposal", "summary": summary, "assumptions": list(assumptions), "tickets": list(tickets)}


def ticket_spec(key, title="Menu", deps=(), uac=("Shows the menu",)):
    return {"key": key, "title": title, "description": f"{title} details",
            "uac": [{"id": f"UAC-{i + 1}", "text": text} for i, text in enumerate(uac)],
            "depends_on_keys": list(deps)}


class DeveloperRuntime:
    """Stands in for the Hermes developer: asks the lead through the real tool facade, then finishes."""

    name = "fake"

    def __init__(self, tools: ToolFacade):
        self.tools = tools

    def run(self, ctx):
        if ctx.answer is None:
            self.tools.call(ctx, "request_decision", {"question": "Should the row be removed at quantity zero?"})
        return Outcome("succeeded", {"lead_answer": ctx.answer})

    def stop(self, ctx):
        ctx.cancelled.set()


class AgentEnv:
    def __init__(self, db, store):
        self.db, self.store = db, store
        self.world = World(db, store)
        self.project = self.world.project
        self.agents = load_agents()
        self.redactor = Redactor([SECRET])
        self.provider = FakeProvider()
        self.client = ModelClient(ModelRegistry.from_dict(CONFIG), {"fake": self.provider}, self.redactor)
        self.queue = JobQueue(db, lease_s=30, retry_backoff_s=0, startable=self.world.w.startable)
        self.limiter = ProviderLimiter(max_concurrent=3, reserved_interactive=1)
        self.threads = Threads(db, self.queue)
        self.tools = ToolFacade(db, self.world.w, self.threads)
        self.builder = ContextBuilder(db, store, self.agents, self.redactor)
        self.runtime = StructuredAgentRuntime(db=db, workflow=self.world.w, threads=self.threads, builder=self.builder,
                                              client=self.client, redactor=self.redactor, fake=True)
        self.supervisors: list[Supervisor] = []

    def script(self, *replies):
        self.provider._replies = list(replies)

    def supervisor(self, worker_id="worker:agents", **runtimes) -> Supervisor:
        sup = Supervisor(self.db, self.store, {"structured:fake": self.runtime, "fake": DeveloperRuntime(self.tools),
                                               **runtimes}, queue=self.queue, limiter=self.limiter,
                         workflow=self.world.w, config=WorkerConfig(worker_id=worker_id, heartbeat_s=0.05,
                                                                    interactive_slots=3))
        sup.maintenance.append(self.threads.ensure_reply_jobs)
        self.supervisors.append(sup)
        return sup

    def job(self, role, task, *, ticket=None, stage=None, lane="interactive", payload=None, key=None, limits=None,
            runtime="structured:fake"):
        return self.queue.enqueue(
            project_id=self.project.id, lane=lane, stage=stage or ("chat" if role == "po" else "plan"), role=role,
            idempotency_key=key or f"job-{uuid.uuid4().hex}", limits=limits or LIMITS, runtime=runtime,
            ticket_id=ticket.id if ticket else None, payload={"task": task, **(payload or {})})

    def get(self, job_id) -> Job:
        with self.db.read() as s:
            return s.get(Job, job_id)

    def messages(self, **filters):
        with self.db.read() as s:
            rows = list(s.scalars(select(Message).where(Message.project_id == self.project.id)
                                  .order_by(Message.created_at, Message.id)))
        return [m for m in rows if all(getattr(m, k) == v if k != "intent" else (m.meta or {}).get("intent") == v
                                       for k, v in filters.items())]

    def events(self, prefix="job."):
        with self.db.read() as s:
            return [e for e in s.scalars(select(Event).order_by(Event.cursor)) if e.type.startswith(prefix)]

    def run_until(self, sup, predicate, timeout_s=20.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            sup.tick()
            if predicate():
                return True
            time.sleep(0.02)
        raise AssertionError("condition not reached in time")

    def finish(self, sup, job_id, *statuses, timeout_s=20.0):
        statuses = statuses or ("succeeded",)
        self.run_until(sup, lambda: self.get(job_id).status in statuses, timeout_s)
        sup.wait_idle(5)
        return self.get(job_id)

    def ctx(self, job, owner="worker:ctx"):
        """Claim the job and build the RunContext the supervisor would hand to a runtime."""
        capacity = 1 if job.lane == "execution" else 5  # the MVP has exactly one execution slot
        lease = self.queue.claim(owner, job.lane, capacity=capacity, runtimes=(job.runtime_ref["runtime"],))
        assert lease is not None and lease.job_id == job.id, "job could not be claimed"
        current = self.get(job.id)
        snapshot = {"id": current.id, "project_id": current.project_id, "ticket_id": current.ticket_id,
                    "scope_version": current.scope_version, "lane": current.lane, "stage": current.stage,
                    "runtime_ref": dict(current.runtime_ref), "limits": dict(current.limits)}
        return RunContext(queue=self.queue, limiter=self.limiter, lease=lease, job=snapshot)

    def approved_ticket(self, document=None):
        return self.world.approve(self.world.new(document or SCOPE))

    def close(self):
        for sup in self.supervisors:
            sup.shutdown(timeout_s=5)


@pytest.fixture
def agent_env(db, store):
    env = AgentEnv(db, store)
    yield env
    env.close()
