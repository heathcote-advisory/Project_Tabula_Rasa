"""Synthetic native PostgreSQL gate, fixture and two-entity deployment isolation."""

from fastapi.testclient import TestClient
import pytest

from tabula_rasa.deployment import database as tooling
from tabula_rasa.http.alpha import password_hash
from tabula_rasa.http.app import create_app
from tabula_rasa.http.config import HttpSettings
from tabula_rasa.infrastructure.postgres.composition import create_postgres_services
from tabula_rasa.infrastructure.postgres.config import DatabaseSettings
from tabula_rasa.infrastructure.postgres.reference_data import PostgresLedgerSetupRepository
from tabula_rasa.infrastructure.postgres.runtime import Database


def test_hosted_gate_native_fixture_isolation_and_repeat_seed_refusal(database, monkeypatch):
    # Only the provider endpoint boundary is substituted, with a disposable native DB.
    monkeypatch.setattr(tooling, "bound_database", lambda _: database)
    tooling.seed({})
    with pytest.raises(Exception):
        tooling.seed({})
    PostgresLedgerSetupRepository.create_entity(database, "other-synthetic", "Other fixture")
    other = create_postgres_services(database, "other-synthetic")
    other.accounts.create("Other environment only", "Bank", "99.00")
    monkeypatch.setattr("tabula_rasa.http.composition.open_alpha_database",
                        lambda _: Database(DatabaseSettings(database.engine.url)))
    configured = HttpSettings("staging", "https://stage.example.test", "test-release", "alpha-synthetic",
                              "stage", password_hash("synthetic-only"))
    with TestClient(create_app(settings=configured), base_url=configured.public_origin) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/session").status_code == 401
        client.auth = ("stage", "synthetic-only")
        assert client.get("/api/v1/session").json()["entity"]["id"] == "alpha-synthetic"
        rows = client.get("/api/v1/entities/alpha-synthetic/accounts").json()["items"]
        assert len(rows) == 1 and rows[0]["name"] == "Deployment connectivity"
        assert rows[0]["opening_balance"] == "0.00"
        assert client.get("/api/v1/entities/other-synthetic/accounts").status_code == 403
        # The hosted gate preserves CSRF and loaded-baseline validation.
        session = client.get("/api/v1/session").json()
        headers = {"Origin": configured.public_origin, "X-CSRF-Token": session["csrf_token"]}
        path = "/api/v1/entities/alpha-synthetic/accounts/" + rows[0]["id"]
        changes = {key: rows[0][key] for key in ("type", "classification", "opening_balance", "active", "parent_id")}
        changes["name"] = "Revised synthetic account"
        missing = client.patch(path, headers=headers, json={"changes": changes})
        assert missing.status_code == 422 and missing.json()["error"]["code"] == "baseline_required"
        payload = {"baseline": rows[0]["baseline"], "changes": changes}
        assert client.patch(path, json=payload).status_code == 403
        saved = client.patch(path, headers=headers, json=payload)
        assert saved.status_code == 200 and saved.json()["baseline"] != rows[0]["baseline"]
        stale = client.patch(path, headers=headers, json=payload)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale"
        assert other.accounts.list()[0]["name"] == "Other environment only"


def test_migration_runs_existing_cli_against_native_current_head(database, monkeypatch):
    monkeypatch.setattr(tooling, "bound_database", lambda _: database)
    from os import environ
    tooling.migrate({**environ, "TABULA_RASA_DATABASE_URL": database.engine.url.render_as_string(hide_password=False)})
    assert database.health()["connected"] is True


def test_operator_bootstrap_requires_blank_native_database(empty_database, monkeypatch):
    from os import environ
    from sqlalchemy import text
    monkeypatch.setattr(tooling, "bound_database", lambda _: empty_database)
    configured = {**environ, "TABULA_RASA_DATABASE_URL": empty_database.engine.url.render_as_string(hide_password=False)}
    with empty_database.transaction() as connection:
        connection.execute(text("CREATE TABLE retained_record (id integer)"))
    with pytest.raises(ValueError, match="blank database"):
        tooling.migrate(configured, bootstrap=True)
    with empty_database.transaction() as connection:
        connection.execute(text("DROP TABLE retained_record"))
    tooling.migrate(configured, bootstrap=True)
    assert empty_database.health()["revision"] == tooling.head_revision()


def test_production_gate_runs_on_native_current_head_and_rejects_a_pending_revision(database, monkeypatch):
    from sqlalchemy import text
    from tabula_rasa.infrastructure.postgres.migrations import current_revision

    monkeypatch.setattr(tooling, "open_bound_neon_database", lambda _: database)
    configured = {"TABULA_RASA_ENVIRONMENT": "production"}
    tooling.migrate(configured)
    with database.engine.begin() as connection:
        assert current_revision(connection) == tooling.head_revision()
        # Change only the disposable fixture's marker. The deployment gate
        # must refuse to run Alembic from this older head.
        connection.execute(text("UPDATE public.alembic_version SET version_num = '0014_document_locations'"))
    with pytest.raises(ValueError, match="current image schema head"):
        tooling.migrate(configured)
    with database.engine.connect() as connection:
        assert current_revision(connection) == "0014_document_locations"


def test_bound_maintenance_migrates_real_source_once_and_checks_runtime_role(empty_database, monkeypatch):
    from uuid import uuid4

    from alembic import command

    from tabula_rasa.deployment import schema_migration
    from tabula_rasa.infrastructure.postgres.migrations import migration_config

    runtime = "window_" + uuid4().hex
    source, target = "0020_book_currency", "0021_customer_lifecycle"
    with empty_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), source)
        connection.exec_driver_sql(f'CREATE ROLE "{runtime}" NOLOGIN')
        connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO "{runtime}"')
        connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO "{runtime}"')
        migration_role = connection.exec_driver_sql("SELECT current_user").scalar_one()
    monkeypatch.setattr(schema_migration, "open_bound_neon_database", lambda _: empty_database)
    environ = {
        "TABULA_RASA_ENVIRONMENT": "staging", "TABULA_RASA_RELEASE_ID": "a" * 40, "WINDOW_RELEASE": "a" * 40,
        "WINDOW_SOURCE_SCHEMA": source, "WINDOW_TARGET_SCHEMA": target, "WINDOW_RUNTIME_ROLE": runtime,
    }
    try:
        with pytest.raises(ValueError, match="separate migration owner"):
            schema_migration.execute({**environ, "WINDOW_RUNTIME_ROLE": migration_role}, apply=True)
        schema_migration.execute(environ, apply=False)
        assert empty_database.health()["revision"] == source
        schema_migration.execute(environ, apply=True)
        assert empty_database.health()["revision"] == target
        with pytest.raises(ValueError, match="approved source revision"):
            schema_migration.execute(environ, apply=True)
    finally:
        with empty_database.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{runtime}"')
            connection.exec_driver_sql(f'DROP ROLE "{runtime}"')
