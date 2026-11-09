"""The explicitly rehearsed forward transitions for the head-only hosted runtime."""

import argparse
import os

from sqlalchemy import text

from tabula_rasa.infrastructure.postgres.hosted import open_bound_neon_database
from tabula_rasa.infrastructure.postgres.migrations import head_revision, upgrade

# An image serving a different revision has no supported online transition.
MAINTENANCE_TRANSITIONS = {("0020_book_currency", "0021_customer_lifecycle")}


def execute(environ, *, apply):
    source = environ["WINDOW_SOURCE_SCHEMA"]
    target = environ["WINDOW_TARGET_SCHEMA"]
    runtime = environ["WINDOW_RUNTIME_ROLE"]
    if (environ.get("TABULA_RASA_ENVIRONMENT") not in {"staging", "production"}
            or not runtime or (source, target) not in MAINTENANCE_TRANSITIONS
            or head_revision() != target
            or environ.get("TABULA_RASA_RELEASE_ID") != environ["WINDOW_RELEASE"]):
        raise ValueError("Maintenance requires the supported transition in the verified image.")
    database = open_bound_neon_database(environ)
    try:
        with database.transaction(read_only=True) as connection:
            permitted = connection.scalar(text("""
                SELECT current_user <> :runtime
                    AND NOT pg_has_role(:runtime, current_user, 'MEMBER')
                    AND has_schema_privilege(current_user, 'public', 'USAGE')
                    AND has_schema_privilege(current_user, 'public', 'CREATE')
                    AND to_regclass('public.alembic_version') IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                          AND NOT pg_has_role(current_user, c.relowner, 'USAGE')
                    )
            """), {"runtime": runtime})
            if permitted is not True:
                raise ValueError("The separate migration owner is required.")
        if database.health() != {"connected": True, "revision": source}:
            raise ValueError("The database does not match the approved source revision.")
        if apply:
            upgrade(database, expected_source=source)
            if database.health() != {"connected": True, "revision": target}:
                raise ValueError("The committed target schema could not be confirmed.")
            with database.transaction(read_only=True) as connection:
                granted = connection.scalar(text("""
                    SELECT has_table_privilege(:runtime, 'public.customer_lifecycle_requests', 'SELECT')
                        AND has_table_privilege(:runtime, 'public.customer_lifecycle_requests', 'INSERT')
                        AND has_table_privilege(:runtime, 'public.customer_lifecycle_requests', 'UPDATE')
                        AND has_table_privilege(:runtime, 'public.customer_lifecycle_events', 'SELECT')
                        AND has_table_privilege(:runtime, 'public.customer_lifecycle_events', 'INSERT')
                        AND has_sequence_privilege(:runtime, 'public.customer_lifecycle_events_sequence_seq', 'USAGE')
                """), {"runtime": runtime})
                if granted is not True:
                    raise ValueError("The migrated runtime grants could not be confirmed.")
    finally:
        database.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "apply"])
    args = parser.parse_args()
    try:
        execute(os.environ, apply=args.command == "apply")
    except Exception:
        raise SystemExit("Bound schema step failed. Inspect database identity, role and revision privately.") from None


if __name__ == "__main__":
    main()
