from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.persistence import ArtifactStore, Database, migrate

from . import factories as f


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "data" / "app.sqlite3"
    migrate.upgrade(path)
    return path


@pytest.fixture
def db(db_path):
    database = Database(db_path)
    yield database
    database.dispose()


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "data" / "artifacts")


@pytest.fixture
def rejected():
    """rejected(db, fn, match): fn(session) must fail the database rules and persist nothing."""

    def check(database, work, match):
        with pytest.raises(IntegrityError, match=match):
            with database.write() as session:
                work(session)

    return check


@pytest.fixture
def world(db):
    """A project with one ticket that has scope version 1."""
    with db.write() as session:
        proj = f.project(session)
        tick = f.ticket(session, proj)
    return SimpleNamespace(project=proj, ticket=tick)
