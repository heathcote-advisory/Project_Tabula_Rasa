"""Candidate-image fixture creation; disposable PostgreSQL only."""

import os
import sys

from alembic import command

from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.runtime import Database
from tabula_rasa.infrastructure.postgres.migrations import migration_config
from tabula_rasa.infrastructure.postgres.audit import AuditContext, audit_context
from tabula_rasa.infrastructure.postgres.recurring_jobs import set_authority
from tests.postgres.core_support import seed_core
from tests.postgres.secondary_support import schedule


def prepare(revision):
    database = Database(DatabaseSettings.from_environment(required=True))
    try:
        with database.engine.begin() as connection:
            command.upgrade(migration_config(connection), revision)
        with database.transaction() as connection:
            # The generated password is a hex UUID; no deployment credentials enter this fixture.
            password = os.environ["PROBE_PASSWORD"]
            if len(password) != 32 or any(c not in "0123456789abcdef" for c in password):
                raise ValueError("Invalid synthetic password")
            connection.exec_driver_sql(f"CREATE ROLE probe_worker LOGIN PASSWORD '{password}'")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO probe_worker")
            connection.exec_driver_sql("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO probe_worker")
            connection.exec_driver_sql("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO probe_worker")
        alpha, beta = seed_core(database)
        for scope, service, amount in [("alpha", alpha, "12.50"), ("beta", beta, "99.00")]:
            service.recurring.create(schedule_id="same", **schedule(amount=amount))
            with audit_context(database, AuditContext("operator", "synthetic-fixture", "recurring-authority", scope)):
                set_authority(database, scope, "worker", "America/New_York")
        with database.transaction() as connection:
            connection.exec_driver_sql("UPDATE job_definitions SET next_due=clock_timestamp()+interval '1 day'")
    finally:
        database.dispose()


if __name__ == "__main__":
    prepare(sys.argv[1])
