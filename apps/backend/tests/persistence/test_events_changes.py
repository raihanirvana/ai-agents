"""AC: state change and event share a transaction; events have a persistent cursor;
short transactions and writer conflicts lose nothing."""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.persistence import (
    EventSpec, NotFound, RevisionConflict, append_event, apply_change, latest_cursor, read_events,
)
from app.persistence.models import Event, Ticket

from . import factories as f


def r0(world):
    """Revision the ticket has after the fixture built it (the ORM bumps it on each update)."""
    return world.ticket.revision


def change(world, *, expected, **values):
    return lambda s: apply_change(s, Ticket, world.ticket.id, expected_revision=expected, values=values,
                                  event=EventSpec("ticket.changed", "user:local", {"changed": sorted(values)}))


def ticket_state(db, world):
    with db.read() as s:
        row = s.get(Ticket, world.ticket.id)
        return row.revision, row.priority, row.phase


def event_types(db):
    with db.read() as s:
        return [e.type for e in read_events(s, limit=1000)]


def test_state_change_and_event_commit_together(db, world):
    with db.write() as s:
        row = change(world, expected=r0(world), phase="ready")(s)
    assert (row.revision, row.phase) == (r0(world) + 1, "ready")
    with db.read() as s:
        [event] = read_events(s)
    assert (event.type, event.actor, event.entity_type, event.entity_id) == \
        ("ticket.changed", "user:local", "tickets", world.ticket.id)
    assert event.project_id == world.project.id
    assert event.payload == {"changed": ["phase"], "revision": r0(world) + 1}


def test_failure_after_change_rolls_back_state_and_event(db, world):
    with pytest.raises(RuntimeError):
        with db.write() as s:
            change(world, expected=r0(world), priority=5)(s)
            raise RuntimeError("crash after the change was applied")
    assert ticket_state(db, world)[:2] == (r0(world), 0)
    assert event_types(db) == []


def test_database_error_after_event_rolls_back_everything(db, world):
    with pytest.raises(Exception, match="UNIQUE"):
        with db.write() as s:
            change(world, expected=r0(world), priority=5)(s)
            f.ticket(s, world.project, number=world.ticket.number)  # violates unique (project, number)
    assert ticket_state(db, world)[:2] == (r0(world), 0)
    assert event_types(db) == []


def test_stale_revision_writes_nothing(db, world):
    with db.write() as s:
        change(world, expected=r0(world), priority=1)(s)
    with pytest.raises(RevisionConflict) as conflict:
        with db.write() as s:
            change(world, expected=r0(world), priority=99)(s)
    assert (conflict.value.expected, conflict.value.actual) == (r0(world), r0(world) + 1)
    assert ticket_state(db, world)[:2] == (r0(world) + 1, 1)
    assert event_types(db) == ["ticket.changed"]


def test_unknown_entity_and_managed_columns(db, world):
    with pytest.raises(NotFound):
        with db.write() as s:
            apply_change(s, Ticket, "missing", expected_revision=r0(world), values={"priority": 1},
                         event=EventSpec("x", "user"))
    with pytest.raises(ValueError):
        with db.write() as s:
            apply_change(s, Ticket, world.ticket.id, expected_revision=r0(world), values={"revision": 9},
                         event=EventSpec("x", "user"))


def test_cursor_is_ordered_and_resumable(db, world):
    with db.write() as s:
        for n in range(5):
            append_event(s, world.project.id, EventSpec(f"step.{n}", "system:test", {"n": n}))
    with db.read() as s:
        first = read_events(s, limit=2)
        second = read_events(s, after=first[-1].cursor, limit=2)
        rest = read_events(s, after=second[-1].cursor)
        assert [e.type for e in first + second + rest] == [f"step.{n}" for n in range(5)]
        assert [e.cursor for e in first + second + rest] == sorted(e.cursor for e in first + second + rest)
        assert latest_cursor(s) == rest[-1].cursor
        assert read_events(s, after=latest_cursor(s)) == []


def test_events_can_be_filtered_by_project(db, world):
    with db.write() as s:
        other = f.project(s, name="other")
        append_event(s, world.project.id, EventSpec("a", "u"))
        append_event(s, other.id, EventSpec("b", "u"))
    with db.read() as s:
        assert [e.type for e in read_events(s, project_id=other.id)] == ["b"]
        assert latest_cursor(s, project_id=world.project.id) < latest_cursor(s, project_id=other.id)


def test_rolled_back_transaction_leaves_no_cursor_gap(db, world):
    with pytest.raises(RuntimeError):
        with db.write() as s:
            append_event(s, world.project.id, EventSpec("lost", "u"))
            raise RuntimeError
    with db.write() as s:
        kept = append_event(s, world.project.id, EventSpec("kept", "u"))
    assert kept.cursor == 1


def test_concurrent_conflicting_writers_lose_no_change(db, world):
    threads, per_thread = 6, 5
    errors, conflicts = [], []

    def worker(n):
        try:
            for _ in range(per_thread):
                while True:
                    with db.read() as s:  # read outside the write lock so writers really conflict
                        row = s.get(Ticket, world.ticket.id)
                        revision, priority = row.revision, row.priority
                    try:
                        with db.write() as s:
                            change(world, expected=revision, priority=priority + 1)(s)
                        break
                    except RevisionConflict:
                        conflicts.append(n)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    pool = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    [t.start() for t in pool]
    [t.join(60) for t in pool]
    assert not errors
    total = threads * per_thread
    assert ticket_state(db, world)[:2] == (r0(world) + total, total)  # every increment landed exactly once
    with db.read() as s:
        cursors = [e.cursor for e in s.scalars(select(Event).order_by(Event.cursor))]
    assert cursors == list(range(1, total + 1))  # one event per change, contiguous, in commit order


def test_transact_retries_a_conflict_with_fresh_state(db, world):
    attempts = []

    def work(s):
        attempts.append(1)
        row = s.get(Ticket, world.ticket.id)
        # First attempt acts on a stale revision (as if another writer got in after our read).
        stale = row.revision - 1 if len(attempts) == 1 else row.revision
        return change(world, expected=stale, priority=7)(s)

    assert db.transact(work).priority == 7
    assert len(attempts) == 2
    assert ticket_state(db, world)[:2] == (r0(world) + 1, 7)
    assert event_types(db) == ["ticket.changed"]  # the conflicting attempt persisted nothing


def test_transact_gives_up_and_does_not_retry_other_errors(db, world):
    calls = []

    def always_stale(s):
        calls.append(1)
        return change(world, expected=99, priority=1)(s)

    with pytest.raises(RevisionConflict):
        db.transact(always_stale, attempts=3, backoff_s=0)
    assert len(calls) == 3

    calls.clear()

    def broken(s):
        calls.append(1)
        raise ValueError("domain rule failed")

    with pytest.raises(ValueError):
        db.transact(broken)
    assert len(calls) == 1
    assert event_types(db) == []


def test_second_writer_waits_for_the_lock_and_readers_are_not_blocked(db, world):
    holding, release, order = threading.Event(), threading.Event(), []

    def first():
        with db.write() as s:
            append_event(s, world.project.id, EventSpec("first", "u"))
            holding.set()
            release.wait(10)
            order.append("first commits")

    def second():
        holding.wait(10)
        with db.write() as s:
            # The first statement takes the lock: BEGIN IMMEDIATE waits instead of failing.
            append_event(s, world.project.id, EventSpec("second", "u"))
            order.append("second runs")

    pool = [threading.Thread(target=first), threading.Thread(target=second)]
    [t.start() for t in pool]
    holding.wait(10)
    time.sleep(0.3)
    assert order == []  # second is still waiting
    assert event_types(db) == []  # a reader sees the last commit and is not blocked
    release.set()
    [t.join(10) for t in pool]
    assert order == ["first commits", "second runs"]
    assert event_types(db) == ["first", "second"]


def test_storage_rejects_updates_that_skip_the_revision_step(db, world):
    raw = lambda **values: update(Ticket).where(Ticket.id == world.ticket.id).values(**values)
    with pytest.raises(IntegrityError, match="exactly one"):
        with db.write() as s:
            s.execute(raw(priority=9))  # no revision bump
    with pytest.raises(IntegrityError, match="exactly one"):
        with db.write() as s:
            s.execute(raw(priority=9, revision=Ticket.revision + 2))
    assert ticket_state(db, world)[:2] == (r0(world), 0)


def test_storage_rejects_a_stale_writer_even_without_apply_change(db, world):
    stale_revision = r0(world)
    with db.write() as s:
        change(world, expected=stale_revision, priority=1)(s)
    # A raw writer that still believes the old revision computes the "next" one from it.
    with pytest.raises(IntegrityError, match="exactly one"):
        with db.write() as s:
            s.execute(update(Ticket).where(Ticket.id == world.ticket.id)
                      .values(priority=99, revision=stale_revision + 1))
    assert ticket_state(db, world)[:2] == (stale_revision + 1, 1)


def test_orm_writer_with_stale_data_cannot_overwrite_a_newer_revision(db, world):
    with db.read() as old_view:
        stale = old_view.get(Ticket, world.ticket.id)  # loaded at the old revision
    with db.write() as s:
        change(world, expected=r0(world), priority=5)(s)
    with pytest.raises(StaleDataError):
        with db.write() as s:
            s.add(stale)
            stale.priority = 77
    assert ticket_state(db, world)[:2] == (r0(world) + 1, 5)


def test_jobs_are_fenced_by_lease_generation_not_revision(db, world):
    from app.persistence.models import Job
    with db.write() as s:
        job = f.job(s, world.project, world.ticket)
    with db.write() as s:  # heartbeat-style update needs no revision handshake
        s.execute(update(Job).where(Job.id == job.id).values(lease_owner="worker-1"))
