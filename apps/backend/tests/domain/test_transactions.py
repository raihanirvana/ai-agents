"""Recovery/concurrency and upgrade from released DEV-002 0001; no runtime claims."""
import sqlite3
import threading
from unittest.mock import patch
import pytest
from sqlalchemy import select
from app.domain import ApprovalItem, Workflow
from app.persistence import Database, RevisionConflict, migrate, read_events
from app.persistence.models import Approval, Job, Ticket
from .conftest import SCOPE


def test_batch_failure_after_mutation_rolls_back_state_approval_and_events(world):
    from app.persistence import changes
    one, two = world.new(), world.new()
    with world.db.read() as s:
        before = [e.cursor for e in read_events(s)]
    original = changes.append_event
    calls = []
    def fail_second(*args, **kw):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("event insert failure")
        return original(*args, **kw)
    with patch.object(changes, "append_event", side_effect=fail_second):
        with pytest.raises(RuntimeError):
            world.w.approve_scope(world.user, [ApprovalItem(one.id, 1, one.revision), ApprovalItem(two.id, 1, two.revision)])
    with world.db.read() as s:
        assert not s.scalars(select(Approval)).all()
        assert [e.cursor for e in read_events(s)] == before
        assert s.get(Ticket, one.id).phase == s.get(Ticket, two.id).phase == "scope_review"


def test_two_concurrent_approvers_one_commit_one_conflict(world):
    t = world.new()
    barrier = threading.Barrier(2)
    results = []
    def approve():
        barrier.wait(timeout=5)
        try:
            world.w.approve_scope(world.user, [ApprovalItem(t.id, 1, t.revision)])
            results.append("approved")
        except RevisionConflict:
            results.append("conflict")
    workers = [threading.Thread(target=approve) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert sorted(results) == ["approved", "conflict"]
    with world.db.read() as s:
        assert len(s.scalars(select(Approval)).all()) == 1


def test_restart_preserves_approval_and_scope_attempt_fence(world):
    t = world.approve(world.new())
    actor, attempt = world.job(t, "developer")
    with world.db.write() as s:
        job = s.get(Job, attempt.job_id)
        job.usage = {"model_calls": 7, "cost_usd": 0.01, "_unknown": ["prompt_tokens"]}
    current = world.ticket(t.id)
    world.w.edit_scope(world.user, t.id, current.revision, {**SCOPE, "title": "Version 2"})
    reopened = Database(world.db.path)
    try:
        w = Workflow(reopened, world.store)
        assert not w.eligible(world.user, t.id)
        with reopened.read() as s:
            assert s.get(Ticket, t.id).current_version == 2
            assert s.get(Job, attempt.job_id).lease_generation == 2
            assert s.get(Job, attempt.job_id).usage == {"model_calls": 7, "cost_usd": 0.01, "_unknown": ["prompt_tokens"]}
            assert s.scalar(select(Approval.id).where(Approval.scope_version == 1))
    finally:
        reopened.dispose()


def test_migrate_populated_0001_preserves_rows_cursor_and_triggers(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    migrate.upgrade(path, "0001")
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("INSERT INTO projects (id,name,mode,brief,brief_version,revision,created_at,updated_at) "
                  "VALUES ('p','Old','new','brief',1,1,'2026-10-05','2026-10-05')")
        c.execute("INSERT INTO tickets (id,project_id,number,title,phase,revision,priority,created_at,updated_at) "
                  "VALUES ('t','p',1,'Old ticket','draft',1,0,'2026-10-05','2026-10-05')")
        c.execute("INSERT INTO events (project_id,type,actor,payload,created_at) VALUES ('p','old','user','{}','2026-10-05')")
        before = c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    migrate.upgrade(path)
    assert migrate.current_revision(path) == "0002"
    assert migrate.schema_drift(path) == []
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT workflow FROM projects WHERE id='p'").fetchone() == ("{}",)
        assert c.execute("SELECT workflow FROM tickets WHERE id='t'").fetchone() == ("{}",)
        assert c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == before
        assert c.execute("SELECT cursor FROM events").fetchall() == [(1,)]
        with pytest.raises(sqlite3.IntegrityError, match="revision"):
            c.execute("UPDATE tickets SET title='unsafe' WHERE id='t'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE events SET type='unsafe'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE projects SET workflow='[]', revision=2 WHERE id='p'")
    migrate.downgrade(path, "0001")
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == before
        assert c.execute("SELECT cursor FROM events").fetchall() == [(1,)]
    migrate.upgrade(path)
    assert migrate.integrity_problems(path) == []
