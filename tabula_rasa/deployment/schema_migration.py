"""Reviewed forward rollout contracts and bound PostgreSQL migration."""

import argparse
import json
import os
import re

from alembic.script import ScriptDirectory
from sqlalchemy import text

from tabula_rasa.infrastructure.postgres.hosted import open_bound_neon_database
from tabula_rasa.infrastructure.postgres.migrations import current_revision, head_revision, migration_config, upgrade

GRANT_CHECKS = {
    "table": ("has_table_privilege(:role, :relation, :privilege)", {"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "column": ("has_column_privilege(:role, :relation, :column, :privilege)", {"SELECT", "INSERT", "UPDATE"}),
    "sequence": ("has_sequence_privilege(:role, :relation, :privilege)", {"USAGE", "SELECT", "UPDATE"}),
}


class ContractError(ValueError):
    """The image does not declare a supported forward rollout."""


def rollout_path(source, target, *, scripts=None):
    scripts = scripts or ScriptDirectory.from_config(migration_config())
    try:
        if scripts.get_revision(source).revision != source or scripts.get_revision(target).revision != target:
            raise ValueError
        path = list(reversed(list(scripts.iterate_revisions(target, source))))
    except Exception:
        raise ContractError("Source is not on a supported Alembic upgrade path.") from None
    parent = source
    for step in path:
        contract = getattr(step.module, "rollout", None)
        if (step.down_revision != parent or step.dependencies or not isinstance(contract, dict)
                or contract.get("mode") != "maintenance" or contract.get("recovery") != "forward_only"
                or not isinstance(contract.get("data"), str) or not contract["data"].strip()
                or not isinstance(contract.get("grants"), tuple)):
            raise ContractError("Each revision needs a reviewed linear maintenance rollout contract.")
        for grant in contract["grants"]:
            if not isinstance(grant, tuple) or len(grant) != 3:
                raise ContractError("A rollout contract has invalid runtime grants.")
            kind, name, privileges = grant
            if (not isinstance(kind, str) or kind not in GRANT_CHECKS or not isinstance(name, str)
                    or not re.fullmatch(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?" if kind == "column"
                                        else r"[a-z_][a-z0-9_]*", name)
                    or (kind == "column" and "." not in name)
                    or not isinstance(privileges, tuple) or not privileges
                    or any(not isinstance(privilege, str) or privilege not in GRANT_CHECKS[kind][1]
                           for privilege in privileges)):
                raise ContractError("A rollout contract has invalid runtime grants.")
        parent = step.revision
    if parent != target:
        raise ContractError("Source is not on a supported Alembic upgrade path.")
    return path


def verify_runtime_grants(connection, runtime, path):
    if connection.scalar(text("SELECT has_schema_privilege(:role, 'public', 'USAGE')"), {"role": runtime}) is not True:
        raise ValueError("The runtime role needs usage on the public schema.")
    for step in path:
        for kind, name, privileges in step.module.rollout["grants"]:
            relation, _, column = name.partition(".")
            for privilege in privileges:
                check = GRANT_CHECKS[kind][0]
                if connection.scalar(text("SELECT " + check), {
                    "role": runtime, "relation": f"public.{relation}", "column": column, "privilege": privilege,
                }) is not True:
                    raise ContractError("The migrated runtime grants could not be confirmed.")


def execute(environ, *, apply, checkpoint=False):
    source = environ["WINDOW_SOURCE_SCHEMA"]
    target = environ["WINDOW_TARGET_SCHEMA"]
    runtime = environ["WINDOW_RUNTIME_ROLE"]
    if (environ.get("TABULA_RASA_ENVIRONMENT") not in {"staging", "production"}
            or not runtime or head_revision() != target
            or environ.get("TABULA_RASA_RELEASE_ID") != environ["WINDOW_RELEASE"]):
        raise ValueError("Migration requires the verified candidate image and its exact schema head.")
    path = rollout_path(source, target)
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
            if current_revision(connection) != source:
                raise ValueError("The database does not match the approved source revision.")
            if not path:
                verify_runtime_grants(connection, runtime, path)
            if checkpoint:
                return {
                    "lsn": connection.scalar(text("SELECT pg_current_wal_flush_lsn()::text")),
                    "at": connection.scalar(text("SELECT clock_timestamp()")).isoformat(),
                }
        if apply:
            if not path:
                raise ValueError("No forward migration is pending.")

            def verify(connection):
                if current_revision(connection) != target:
                    raise ValueError("The target revision could not be confirmed.")
                verify_runtime_grants(connection, runtime, path)

            upgrade(database, expected_source=source, verify_upgrade=verify)
    finally:
        database.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "apply", "checkpoint"])
    args = parser.parse_args()
    try:
        result = execute(os.environ, apply=args.command == "apply", checkpoint=args.command == "checkpoint")
        if result:
            print(json.dumps(result))
    except ContractError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit("Bound schema step failed. Inspect database identity, role and revision privately.") from None


if __name__ == "__main__":
    main()
