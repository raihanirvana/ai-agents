"""Developer wait for QA planning across a TL contract amendment (real SQLite, fake runtime).

Nothing here executes a model, QA harness or provider; job rows are synthetic scheduling state.
"""
import threading
from types import SimpleNamespace

import pytest

from app.agents.outputs import InvalidOutput
from app.pipeline.runtime import PipelineRuntime
from tests.persistence import factories as f
from tests.persistence.conftest import db, db_path, store  # noqa: F401


def runtime(db, suite=None):
    rt = object.__new__(PipelineRuntime)
    rt.db = db
    def read_suite(identity):
        if suite is None:
            raise ValueError('no persisted QA suite for current scope')
        return suite
    rt.workspace = SimpleNamespace(suite=read_suite)
    return rt


def scope(db):
    with db.write() as s:
        p = f.project(s)
        t = f.ticket(s, p)
    return p, t, {'ticket_id': t.id, 'scope_version': t.current_version}


def add(db, p, t, stage, status, result=None, revision=None):
    payload = {'task': stage, **({'ui_contract_revision': revision} if revision else {})}
    with db.write() as s:
        f.job(s, p, t, stage=stage, status=status, result=result or {}, runtime_ref={'payload': payload})


def test_amendment_handoff_gap_is_waiting_not_failure(db):
    p, t, identity = scope(db)
    rt = runtime(db)
    add(db, p, t, 'technical_plan', 'succeeded', {'ui_contract_revision': 1})
    add(db, p, t, 'qa_plan', 'succeeded', {'contract_amendment': {'revision': 1}}, revision=1)
    assert rt._qa_plan_state(identity) == 'waiting'  # TL amendment not yet enqueued
    add(db, p, t, 'technical_plan', 'queued')
    assert rt._qa_plan_state(identity) == 'waiting'
    with db.write() as s:
        from app.persistence.models import Job
        from sqlalchemy import select
        job = s.scalars(select(Job).where(Job.status == 'queued')).one()
        job.status, job.result = 'succeeded', {'ui_contract_revision': 2}
    # The reported race: amended TL plan done, qa_plan for revision 2 not yet enqueued.
    assert rt._qa_plan_state(identity) == 'waiting'
    add(db, p, t, 'qa_plan', 'failed', revision=2)
    assert rt._qa_plan_state(identity) == 'dead'


def test_no_planning_or_failed_planning_is_dead(db):
    p, t, identity = scope(db)
    rt = runtime(db)
    assert rt._qa_plan_state(identity) == 'dead'
    add(db, p, t, 'technical_plan', 'succeeded', {'ui_contract_revision': 1})
    add(db, p, t, 'qa_plan', 'succeeded', {}, revision=1)  # succeeded yet no suite persisted
    assert rt._qa_plan_state(identity) == 'dead'


class Ctx:
    def __init__(self):
        self.cancelled = threading.Event()
        self.queue = SimpleNamespace(verify=lambda lease: None)
        self.lease, self.logs, self.metrics = None, [], []

    def _check(self):
        pass

    def log(self, line):
        self.logs.append(line)

    def record_phase(self, metric):
        self.metrics.append(metric)


def test_wait_tolerates_transient_dead_observations_then_gives_up(db, monkeypatch):
    rt = runtime(db)
    states = iter(['dead', 'waiting', 'dead', 'dead', 'dead'])
    monkeypatch.setattr(rt, '_qa_plan_state', lambda identity: next(states))
    ctx = Ctx()
    ctx.cancelled.wait = lambda timeout: None
    with pytest.raises(InvalidOutput):
        rt._await_qa_plan(ctx, {}, timeout_s=60)
    with pytest.raises(StopIteration):
        next(states)  # Gave up only after three consecutive dead observations.


def test_wait_is_bounded_and_returns_suite_when_ready(db, monkeypatch):
    rt = runtime(db)
    monkeypatch.setattr(rt, '_qa_plan_state', lambda identity: 'waiting')
    ctx = Ctx()
    ctx.cancelled.wait = lambda timeout: None
    assert rt._await_qa_plan(ctx, {}, timeout_s=0) is None  # submit returns qa_plan_pending
    assert runtime(db, suite=('suite', 'id'))._await_qa_plan(Ctx(), {}) == ('suite', 'id')
