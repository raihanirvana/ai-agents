"""Run and inspect Alembic migrations programmatically (the CLI and tests use this)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from .db import make_engine
from .models import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path | str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["db_path"] = str(db_path)
    return config


def upgrade(db_path: Path | str, revision: str = "head") -> None:
    command.upgrade(_config(db_path), revision)


def downgrade(db_path: Path | str, revision: str = "base") -> None:
    command.downgrade(_config(db_path), revision)


def head_revision() -> str:
    return ScriptDirectory(str(BACKEND_DIR / "migrations")).get_current_head()


def current_revision(db_path: Path | str) -> str | None:
    engine = make_engine(db_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def schema_drift(db_path: Path | str) -> list:
    """Differences between the migrated database and the ORM models (empty means in sync)."""
    engine = make_engine(db_path)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            return compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()


def integrity_problems(db_path: Path | str) -> list[str]:
    """SQLite integrity_check plus foreign_key_check; an empty list means healthy."""
    engine = make_engine(db_path)
    try:
        with engine.connect() as connection:
            problems = [row[0] for row in connection.exec_driver_sql("PRAGMA integrity_check") if row[0] != "ok"]
            problems += [f"foreign key violation in {row[0]} rowid {row[1]}"
                         for row in connection.exec_driver_sql("PRAGMA foreign_key_check")]
            return problems
    finally:
        engine.dispose()
