"""Alembic entry points with an explicitly supplied infrastructure connection."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import Error as PsycopgError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from .runtime import Database, raise_database_error, safe_database_error

MIGRATION_ROOT = Path(__file__).resolve().parent / "migrations"


def migration_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATION_ROOT))
    config.attributes["connection"] = connection
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(migration_config()).get_current_head()


def current_revision(connection: Connection) -> str | None:
    heads = MigrationContext.configure(connection).get_current_heads()
    if len(heads) > 1:
        from .runtime import DatabaseError
        raise DatabaseError("multiple_revisions")
    return heads[0] if heads else None


def upgrade(
    database: Database, *, require_current_head: bool = False, expected_source: str | None = None,
    verify_upgrade=None,
) -> None:
    # Deployment calls this explicitly; neither app startup nor pool creation
    # changes the schema. Serializes concurrent migrators in this database.
    try:
        with database.engine.begin() as connection:
            if expected_source is not None:
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(text("SET LOCAL statement_timeout = '120s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(244, 1)"))
            if require_current_head and current_revision(connection) != head_revision():
                raise ValueError("Live promotion requires the current image schema head.")
            if expected_source is not None and current_revision(connection) != expected_source:
                raise ValueError("Maintenance source changed; inspect before retrying.")
            command.upgrade(migration_config(connection), "head")
            if verify_upgrade is not None:
                verify_upgrade(connection)
    except (SQLAlchemyError, PsycopgError) as error:
        failure = safe_database_error(error)
    else:
        return
    raise_database_error(failure)


def downgrade(database: Database, revision: str, *, allow_destructive: bool = False) -> None:
    if not allow_destructive:
        raise ValueError("Downgrades require --allow-destructive and a verified recovery plan.")
    try:
        with database.engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(244, 1)"))
            command.downgrade(migration_config(connection), revision)
    except (SQLAlchemyError, PsycopgError) as error:
        failure = safe_database_error(error)
    else:
        return
    raise_database_error(failure)
