"""Alembic environment: online only, using the same pragma/transaction setup as the app.

Database selection: config.attributes["db_path"] (programmatic), then `-x db=PATH`, then
DATABASE_PATH from app.config. SQLite batch mode recreates tables and DROPS their triggers:
a migration that alters a table with triggers (see 0001) must recreate those triggers.
"""
from alembic import context

from app.persistence.db import make_engine
from app.persistence.models import Base

config = context.config


def _db_path():
    path = config.attributes.get("db_path") or context.get_x_argument(as_dictionary=True).get("db")
    if path:
        return path
    from app.config import DATABASE_PATH
    return DATABASE_PATH


def run_migrations_online() -> None:
    engine = make_engine(_db_path()).execution_options(write=True)  # BEGIN IMMEDIATE
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True,
                              transactional_ddl=True, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline SQL generation is not supported; run against a database file")
run_migrations_online()
