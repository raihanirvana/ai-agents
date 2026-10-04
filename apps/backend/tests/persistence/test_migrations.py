"""AC: migration builds a new database; FK/revision/unique constraints live in storage."""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text

from app.persistence import Database, migrate
from app.persistence.__main__ import main as cli
from app.persistence.models import ENTITY_TABLES


def tables(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_sqlite_has_json_functions():
    # The schema relies on JSON1 in CHECK constraints and triggers.
    with sqlite3.connect(":memory:") as conn:
        assert conn.execute("SELECT json_type('[]')").fetchone()[0] == "array"


def test_empty_database_is_built_with_the_twelve_entities(tmp_path):
    path = tmp_path / "new.sqlite3"
    migrate.upgrade(path)
    assert tables(path) - {"alembic_version", "sqlite_sequence"} == set(ENTITY_TABLES)
    assert len(ENTITY_TABLES) == 12
    assert migrate.current_revision(path) == migrate.head_revision()


def test_upgrade_is_repeatable_and_matches_models(db_path):
    migrate.upgrade(db_path)  # already at head: no-op
    assert migrate.schema_drift(db_path) == []
    assert migrate.integrity_problems(db_path) == []


def test_connections_enforce_wal_foreign_keys_and_durability(db):
    with db.read() as session:
        pragma = lambda name: session.connection().exec_driver_sql(f"PRAGMA {name}").scalar()
        assert pragma("journal_mode") == "wal"
        assert pragma("foreign_keys") == 1
        assert pragma("synchronous") == 2  # FULL
        assert pragma("busy_timeout") == 30000


def test_foreign_keys_are_enforced_by_storage(db, rejected):
    rejected(db, lambda s: s.execute(text(
        "INSERT INTO tickets (id, project_id, number, title, phase, revision, priority, created_at, updated_at) "
        "VALUES ('t', 'no-such-project', 1, 'x', 'draft', 1, 0, '2026-01-01', '2026-01-01')")), "FOREIGN KEY")


def test_downgrade_removes_everything_and_upgrade_rebuilds(db_path):
    migrate.downgrade(db_path, "base")
    assert tables(db_path) - {"alembic_version", "sqlite_sequence"} == set()
    migrate.upgrade(db_path)
    assert migrate.schema_drift(db_path) == []


def test_failed_migration_leaves_no_partial_schema(tmp_path):
    path = tmp_path / "conflict.sqlite3"
    with sqlite3.connect(path) as conn:  # a foreign table named like one the migration creates later
        conn.execute("CREATE TABLE tickets (x)")
    with pytest.raises(Exception):
        migrate.upgrade(path)
    assert tables(path) == {"tickets"}  # projects/artifacts/events (created earlier) were rolled back


def test_cli_reports_healthy_missing_and_drifted_databases(db_path, tmp_path, capsys):
    assert cli(["check", "--db", str(db_path)]) == 0
    assert cli(["current", "--db", str(db_path)]) == 0
    assert cli(["check", "--db", str(tmp_path / "absent.sqlite3")]) == 1
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX ix_jobs_claim")
    assert cli(["check", "--db", str(db_path)]) == 1
    assert "schema drift" in capsys.readouterr().err


def test_cli_upgrade_creates_database(tmp_path, capsys):
    path = tmp_path / "cli" / "app.sqlite3"
    assert cli(["upgrade", "--db", str(path)]) == 0
    assert cli(["check", "--db", str(path)]) == 0
    assert "revision" in capsys.readouterr().out


def test_wal_requirement_is_not_silently_ignored():
    from app.persistence import DatabaseConfigurationError, make_engine

    # An in-memory database cannot use WAL: refuse to run instead of degrading durability.
    engine = make_engine(":memory:")
    with pytest.raises(DatabaseConfigurationError, match="WAL"):
        engine.connect()
    engine.dispose()
