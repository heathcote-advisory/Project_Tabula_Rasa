"""Drain a dedicated PostgreSQL worker login during a schema release."""

import re
import time

from sqlalchemy import text

from tabula_rasa.infrastructure.postgres.hosted import open_bound_neon_database
from tabula_rasa.infrastructure.postgres.migrations import current_revision

from .release import ReleaseError


class WorkerLoginFence:
    def __init__(self, environ, runtime_role, source, target):
        role = environ.get("WINDOW_WORKER_ROLE", "")
        schemas = set(environ.get("WINDOW_WORKER_COMPATIBLE_SCHEMAS", "").split(","))
        if (not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or role == runtime_role
                or not {source, target} <= schemas):
            raise ReleaseError("Bind a dedicated worker login and verify its pinned image on both schemas.")
        self.environ = environ
        self.role = role
        self.source = source
        self.target = target

    def _database(self):
        return open_bound_neon_database({
            **self.environ, "TABULA_RASA_DATABASE_URL": self.environ["WINDOW_MIGRATION_DATABASE_URL"],
        })

    def _set_login(self, database, *, previous, enabled, schema):
        with database.engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            state = connection.execute(text("""
                SELECT rolcanlogin, rolname <> current_user
                    AND NOT (rolsuper OR rolreplication OR rolcreaterole OR rolcreatedb OR rolbypassrls)
                FROM pg_roles WHERE rolname = :role
            """), {"role": self.role}).one_or_none()
            if state != (previous, True) or current_revision(connection) != schema:
                raise ReleaseError("Worker login or schema state changed. Inspect before continuing.")
            connection.execute(text("""
                SELECT release_control.set_worker_login(CAST(:role AS name), :enabled)
            """), {"role": self.role, "enabled": enabled})
            if connection.scalar(text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :role"),
                                 {"role": self.role}) is not enabled:
                raise ReleaseError("The scoped worker login change could not be verified.")

    def drain(self, *, sleep=time.sleep, monotonic=time.monotonic):
        database = self._database()
        try:
            self._set_login(database, previous=True, enabled=False, schema=self.source)
            deadline = monotonic() + 90
            while True:
                with database.transaction(read_only=True) as connection:
                    # Role login state is branch-wide. Count sessions in every database.
                    active = connection.scalar(text("""
                        SELECT count(*) FROM pg_stat_activity WHERE usename = :role
                    """), {"role": self.role})
                if active == 0:
                    return
                if monotonic() >= deadline:
                    raise ReleaseError("Worker sessions did not drain. Keep its login disabled and inspect.")
                sleep(2)
        finally:
            database.dispose()

    def restore(self):
        database = self._database()
        try:
            self._set_login(database, previous=False, enabled=True, schema=self.target)
        finally:
            database.dispose()
