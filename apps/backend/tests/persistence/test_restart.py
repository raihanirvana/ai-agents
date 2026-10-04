"""AC: project, conversation, scope and approval data survive a restart; a crash mid-transaction
leaves no partial state."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from sqlalchemy import select

from app.persistence import (
    ArtifactStore, Database, EventSpec, append_event, append_message, migrate, read_events,
)
from app.persistence.models import Approval, Message, Project, Ticket, TicketVersion

from . import factories as f

BACKEND = Path(__file__).resolve().parents[2]


def crash_process(code: str, db_path, expect: int) -> None:
    """Run code in a separate interpreter that dies like a crash (no cleanup, no atexit)."""
    script = "import os, sys\nfrom app.persistence import *\nfrom app.persistence.models import *\n" + textwrap.dedent(code)
    result = subprocess.run([sys.executable, "-c", script, str(db_path)], cwd=BACKEND, capture_output=True, text=True)
    assert result.returncode == expect, result.stderr


def test_reopened_database_has_projects_conversation_scope_and_approvals(db_path, tmp_path):
    first = Database(db_path)
    with first.write() as s:
        proj = f.project(s, name="Coffee shop")
        tick = f.ticket(s, proj, title="Menu")
        append_message(s, project_id=proj.id, thread_id="po", sender="user", body="build a coffee menu")
        append_message(s, project_id=proj.id, thread_id="po", sender="agent:po", body="proposed 3 tickets")
        s.add(Approval(project_id=proj.id, type="scope", user_id="user:local", ticket_id=tick.id, scope_version=1))
        append_event(s, proj.id, EventSpec("scope.approved", "user:local", {"ticket": tick.id}))
    first.dispose()  # restart

    second = Database(db_path)
    with second.read() as s:
        [project] = s.scalars(select(Project)).all()
        [ticket] = s.scalars(select(Ticket)).all()
        assert (project.name, ticket.title, ticket.current_version, ticket.phase) == ("Coffee shop", "Menu", 1, "scope_review")
        assert s.scalars(select(TicketVersion)).one().uac == [{"id": "UAC-1", "text": "x"}]
        assert [m.body for m in s.scalars(select(Message).order_by(Message.seq))] == [
            "build a coffee menu", "proposed 3 tickets"]
        [approval] = s.scalars(select(Approval)).all()
        assert (approval.type, approval.user_id, approval.scope_version) == ("scope", "user:local", 1)
        [event] = read_events(s)
        assert event.payload == {"ticket": ticket.id}
    second.dispose()
    assert migrate.integrity_problems(db_path) == []


def test_state_and_events_stay_consistent_across_restarts(db_path):
    for round_number in range(3):
        with Database(db_path) as database:
            with database.write() as s:
                if round_number == 0:
                    proj = f.project(s, id="p1")
                append_event(s, "p1", EventSpec(f"round.{round_number}", "system:test"))
    with Database(db_path) as database, database.read() as s:
        events = read_events(s)
    assert [e.type for e in events] == ["round.0", "round.1", "round.2"]
    assert [e.cursor for e in events] == [1, 2, 3]  # the cursor continues, it is never reset


def test_committed_work_survives_a_hard_kill(db_path):
    crash_process("""
        db = Database(sys.argv[1])
        with db.write() as s:
            s.add(Project(id="p-kill", name="n", mode="new"))
            s.flush()
            append_event(s, "p-kill", EventSpec("project.created", "system:test"))
        os._exit(0)  # no dispose, no atexit: the process just disappears
    """, db_path, expect=0)
    with Database(db_path) as database, database.read() as s:
        assert s.get(Project, "p-kill") is not None
        assert [e.type for e in read_events(s)] == ["project.created"]
    assert migrate.integrity_problems(db_path) == []


def test_uncommitted_work_vanishes_after_a_hard_kill_and_the_lock_is_released(db_path):
    crash_process("""
        db = Database(sys.argv[1])
        session = db._writer()
        session.add(Project(id="p-lost", name="n", mode="new"))
        session.flush()
        append_event(session, "p-lost", EventSpec("project.created", "system:test"))
        os._exit(7)  # dies holding the write lock, before commit
    """, db_path, expect=7)
    with Database(db_path) as database:
        with database.read() as s:
            assert s.get(Project, "p-lost") is None  # neither the state nor its event
            assert read_events(s) == []
        with database.write() as s:  # the dead writer left no lock behind
            f.project(s, id="p-after")
            append_event(s, "p-after", EventSpec("project.created", "system:test"))
        with database.read() as s:
            assert [e.cursor for e in read_events(s)] == [1]  # and no cursor was burned
    assert migrate.integrity_problems(db_path) == []


def test_artifacts_survive_restart_and_reverify(db_path, tmp_path):
    root = tmp_path / "artifacts"
    with Database(db_path) as database:
        with database.write() as s:
            proj = f.project(s)
            art = ArtifactStore(root).put_json(s, project_id=proj.id, kind="target_manifest",
                                               document={"build_digest": "x"}, name="target.json")
    with Database(db_path) as database:  # new process state, same files
        store = ArtifactStore(root)
        with database.write() as s:
            assert store.verify(s, art.id) == "available"
        with database.read() as s:
            assert store.read_bytes(s, art.id) == b'{"build_digest":"x"}'
