"""Worker tests: real SQLite/artifacts and real threads; the runtime is the LABELLED fake.

Fake results only exercise the scheduling contract, never real provider/QA behaviour.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.adapters.runtime.fake import FakeRuntime
from app.domain import Workflow
from app.persistence.columns import utcnow
from app.persistence.models import Event, Job
from app.workers import JobQueue, ProviderLimiter, Supervisor, WorkerConfig
from tests.persistence import factories as f
from tests.persistence.conftest import db, db_path, store  # noqa: F401  (fixtures)

LIMITS = {"model_calls": 5, "tool_calls": 5, "active_s": 60}


class Clock:
    """Controllable clock for lease-expiry tests at the queue level."""

    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Env:
    def __init__(self, db, store, *, clock=None):
        self.db, self.store = db, store
        with db.write() as s:
            self.project = f.project(s)
        self.workflow = Workflow(db, store)
        self.queue = JobQueue(db, lease_s=30, retry_backoff_s=0, startable=self.workflow.startable,
                              **({"clock": clock} if clock else {}))
        self.limiter = ProviderLimiter(max_concurrent=3, reserved_interactive=1)
        self.runtime = FakeRuntime()
        self.supervisors: list[Supervisor] = []

    def supervisor(self, worker_id="worker:test", runtime=None, **config) -> Supervisor:
        sup = Supervisor(self.db, self.store, {"fake": runtime or self.runtime}, queue=self.queue,
                         limiter=self.limiter, workflow=self.workflow,
                         config=WorkerConfig(worker_id=worker_id, heartbeat_s=0.05, **config))
        self.supervisors.append(sup)
        return sup

    def enqueue(self, script=(), *, lane="execution", stage="work", role="developer", limits=None, key=None,
                ticket_id=None, resume_script=None, runtime="fake"):
        payload = {"script": list(script)}
        if resume_script is not None:
            payload["resume_script"] = list(resume_script)
        return self.queue.enqueue(project_id=self.project.id, lane=lane, stage=stage, role=role,
                                  idempotency_key=key or f"job-{uuid.uuid4().hex}",
                                  limits=LIMITS if limits is None else limits,
                                  runtime=runtime, ticket_id=ticket_id, payload=payload)

    def job(self, job_id) -> Job:
        with self.db.read() as s:
            return s.get(Job, job_id)

    def events(self, job_id=None, kind=None) -> list[Event]:
        with self.db.read() as s:
            query = select(Event).where(Event.type.like("job.%")).order_by(Event.cursor)
            rows = [e for e in s.scalars(query) if job_id is None or e.entity_id == job_id]
        return [e for e in rows if kind is None or e.type == "job." + kind]

    def run_until(self, sup: Supervisor, predicate, timeout_s=15.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            sup.tick()
            if predicate():
                return True
            time.sleep(0.02)
        raise AssertionError("condition not reached in time")

    def close(self):
        for sup in self.supervisors:
            sup.shutdown(timeout_s=5)


@pytest.fixture
def env(db, store):
    environment = Env(db, store)
    yield environment
    environment.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def clocked(db, store, clock):
    environment = Env(db, store, clock=clock)
    yield environment
    environment.close()
