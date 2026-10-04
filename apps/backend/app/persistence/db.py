"""SQLite engine and short transactions.

Writers use BEGIN IMMEDIATE, so concurrent writers queue on the database lock (up to
busy_timeout) instead of failing halfway through a transaction, and cursor order equals
commit order. Readers use WAL snapshots and never block the writer. Foreign keys are
enforced on every connection; WAL is required and its absence is an error.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .changes import RevisionConflict

T = TypeVar("T")
BUSY_TIMEOUT_MS = 30_000


class DatabaseConfigurationError(RuntimeError):
    pass


def make_engine(path: Path | str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> Engine:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _record):
        # Transactions are emitted explicitly by the "begin" hook below.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
            mode = cursor.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise DatabaseConfigurationError(
                    f"SQLite WAL is unavailable for {path} (journal_mode={mode}); "
                    "use a local filesystem, not a network or 9p mount")
            cursor.execute("PRAGMA synchronous = FULL")
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(conn):
        write = conn.get_execution_options().get("write", False)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")

    return engine


class Database:
    """Owns the engine. read() for snapshots, write() for one short atomic change."""

    def __init__(self, path: Path | str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS):
        self.path = Path(path)
        self.engine = make_engine(self.path, busy_timeout_ms=busy_timeout_ms)
        self._write_engine = self.engine.execution_options(write=True)
        self._reader = sessionmaker(self.engine, expire_on_commit=False)
        self._writer = sessionmaker(self._write_engine, expire_on_commit=False)

    @contextmanager
    def read(self) -> Iterator[Session]:
        session = self._reader()
        try:
            yield session
        finally:
            # close() ends the read snapshot and keeps loaded rows usable after the block
            # (an explicit rollback() would expire them).
            session.close()

    @contextmanager
    def write(self) -> Iterator[Session]:
        """One transaction: commit on success, roll back (state and events) on any error."""
        session = self._writer()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def transact(self, work: Callable[[Session], T], *, attempts: int = 5, backoff_s: float = 0.01) -> T:
        """Run work(session) in write(); retry only on RevisionConflict.

        work must re-read the state it depends on, so a retry decides from fresh data.
        Nothing from a conflicting attempt is persisted.
        """
        for attempt in range(1, attempts + 1):
            try:
                with self.write() as session:
                    return work(session)
            except RevisionConflict:
                if attempt == attempts:
                    raise
                time.sleep(backoff_s * attempt)
        raise AssertionError("unreachable")

    def dispose(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.dispose()
