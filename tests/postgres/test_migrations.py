import pytest
from sqlalchemy import inspect, text

from tabula_rasa.infrastructure.postgres.migrations import current_revision, downgrade, head_revision, upgrade
from tests.postgres._support import entity
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError


def test_zero_to_head_repeated_upgrade_and_explicit_downgrade(empty_database):
    assert empty_database.health() == {"connected": True, "revision": None}
    upgrade(empty_database)
    assert empty_database.health()["revision"] == head_revision() == "0022_drive_recovery_binding"
    with empty_database.transaction() as connection:
        entity(connection)
    upgrade(empty_database)
    with empty_database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT count(*) FROM business_entities")) == 1
    with pytest.raises(ValueError, match="allow-destructive"):
        downgrade(empty_database, "base")
    with pytest.raises(ValueError, match="forward"):
        downgrade(empty_database, "base", allow_destructive=True)
    assert empty_database.health()["revision"] == head_revision()


@pytest.mark.parametrize('base_revision', ['0010_workspace_lifecycle', '0009_external_connections', '0010_onboarding_routes', '0013_accounting_controls', '0014_document_locations', '0017_customer_exports'])
def test_onboarding_forward_upgrade_preserves_pending_login_and_existing_ledger(empty_database, base_revision):
    from alembic import command
    from tabula_rasa.infrastructure.postgres.migrations import migration_config
    from tabula_rasa.infrastructure.postgres.sessions import PostgresSessionRepository

    with empty_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), base_revision)
    sessions = PostgresSessionRepository(empty_database, 'test', 'a' * 64)
    sessions.configure()
    raw, pending = sessions.begin_login('/onboarding')
    with empty_database.transaction() as connection:
        entity(connection)
    upgrade(empty_database)
    assert sessions.consume_login(raw, pending.state) == pending
    invite_raw, invite = sessions.begin_login('/invitations')
    assert sessions.consume_login(invite_raw, invite.state) == invite
    with empty_database.transaction(read_only=True) as connection:
        assert connection.scalar(text('SELECT count(*) FROM business_entities')) == 1
        assert current_revision(connection) == '0022_drive_recovery_binding'
        assert {'workspace_invitations', 'workspace_bootstraps', 'external_connections', 'drive_oauth_requests', 'legacy_entity_claims'} <= set(inspect(connection).get_table_names())


def test_workspace_upgrade_extends_published_connections_revision(empty_database):
    from alembic import command
    from tabula_rasa.infrastructure.postgres.migrations import migration_config

    with empty_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0009_external_connections')
    with empty_database.transaction() as connection:
        entity(connection)
        original = dict(connection.execute(text('SELECT * FROM business_entities')).mappings().one())
        assert 'external_connections' in inspect(connection).get_table_names()
        assert 'workspace_bootstraps' not in inspect(connection).get_table_names()
    upgrade(empty_database)
    with empty_database.transaction(read_only=True) as connection:
        assert current_revision(connection) == '0022_drive_recovery_binding'
        assert dict(connection.execute(text('SELECT * FROM business_entities')).mappings().one()) == original
        assert {'external_connections', 'drive_oauth_requests', 'legacy_entity_claims', 'document_locations', 'workspace_bootstraps',
                'workspace_invitations'} <= set(inspect(connection).get_table_names())
        assert 'verified_email' in {column['name'] for column in inspect(connection).get_columns('identity_sessions')}


def test_catalog_is_canonical_and_every_relationship_is_entity_qualified(database):
    with database.engine.connect() as connection:
        inspector = inspect(connection)
        control = {'customer_lifecycle_requests', 'customer_lifecycle_events', 'beta_program', 'beta_internal_users', 'beta_operator_grants', 'beta_admissions', 'beta_named_users', 'beta_events', 'identity_users', 'identity_bindings', 'organizations', 'memberships',
                   'organization_entities', 'identity_environments', 'identity_login_transactions', 'identity_sessions',
                   'workspace_invitations', 'workspace_bootstraps',
                   'external_connections', 'drive_oauth_requests', 'legacy_entity_claims', 'document_locations',
                   'customer_export_actions', 'audit_events', 'audit_baseline', 'audit_activation', 'jobs', 'job_attempts', 'job_definitions',
                   'recurring_generation_policy', 'accounting_period_policy', 'accounting_periods', 'entry_corrections',
                   'accounting_period_actions', 'support_grants', 'support_actions'}
        all_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert control <= all_tables
        tables = all_tables - control
        assert len(tables) == 27  # 26 bookkeeping collections plus operator cutover controls.
        assert {"entries", "postings", "business_entities", "migration_id_mappings"} <= tables
        assert "transactions" not in tables
        for table in tables - {"business_entities"}:
            columns = {c["name"]: c for c in inspector.get_columns(table)}
            assert columns["entity_id"]["nullable"] is False
            foreign_keys = inspector.get_foreign_keys(table)
            assert any(fk["referred_table"] == "business_entities" for fk in foreign_keys)
            for fk in foreign_keys:
                assert fk["constrained_columns"][0] == "entity_id"
                if fk["referred_table"] != "business_entities":
                    assert fk["referred_columns"][0] == "entity_id"
        assert str({c["name"]: c for c in inspector.get_columns("postings")}["amount"]["type"]) == "NUMERIC(28, 2)"
        assert not any("password" in c["name"] or "token" in c["name"]
                       for t in tables for c in inspector.get_columns(t))


def test_mileage_order_revision_preserves_populated_rows_and_downgrades(empty_database):
    from alembic import command
    from tabula_rasa.infrastructure.postgres.migrations import migration_config
    database = empty_database
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0001_initial')
    with database.transaction() as connection:
        entity(connection)
        connection.execute(text("""INSERT INTO mileage_records(
            entity_id,id,incarnation_id,trip_date,miles,start_location,destination,business_purpose)
            VALUES ('alpha','trip','trip','2026-10-05',12.3,'Office','Client','Meeting')"""))
        original = dict(connection.execute(text('SELECT * FROM mileage_records')).mappings().one())
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0005_bookkeeping_cutover')
    with database.transaction(read_only=True) as connection:
        loaded = dict(connection.execute(text('SELECT * FROM mileage_records')).mappings().one())
        assert loaded.pop('insertion_order') > 0
        assert loaded == original
    downgrade(database, '0001_initial', allow_destructive=True)
    with database.transaction(read_only=True) as connection:
        assert dict(connection.execute(text('SELECT * FROM mileage_records')).mappings().one()) == original
    upgrade(database)


def test_raw_driver_migration_errors_are_safe_and_roll_back(empty_database, monkeypatch):
    from tabula_rasa.infrastructure.postgres import migrations

    def fail(config, target):
        with config.attributes["connection"].connection.driver_connection.cursor() as cursor:
            cursor.execute("CREATE TABLE synthetic_partial(id integer)")
            cursor.execute("SELECT 'private synthetic migration content'::integer")

    monkeypatch.setattr(migrations.command, "upgrade", fail)
    with pytest.raises(DatabaseError) as caught:
        upgrade(empty_database)
    assert str(caught.value) == "PostgreSQL operation failed (unavailable)."
    assert caught.value.__context__ is None
    with empty_database.engine.connect() as connection:
        assert "synthetic_partial" not in inspect(connection).get_table_names()


def test_baseline_refuses_unknown_existing_schema_without_partial_install(empty_database):
    with empty_database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE accounts (synthetic integer)"))
    with pytest.raises(DatabaseError):
        upgrade(empty_database)
    with empty_database.engine.connect() as connection:
        assert current_revision(connection) is None
        assert inspect(connection).get_table_names() == ["accounts"]


def test_operator_cli_readiness_and_successful_explicit_downgrade(empty_database, monkeypatch, capsys):
    import json
    from tabula_rasa.infrastructure.postgres.__main__ import main

    monkeypatch.setenv("TABULA_RASA_DATABASE_URL", empty_database.engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr("sys.argv", ["postgres", "current"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["revision"] is None
    monkeypatch.setattr("sys.argv", ["postgres", "health"])
    assert main() == 1
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["postgres", "upgrade"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["at_head"] is True
    monkeypatch.setattr("sys.argv", ["postgres", "downgrade", "--revision", "base"])
    assert main() == 1
    assert "allow-destructive" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["postgres", "downgrade", "--revision", "base", "--allow-destructive"])
    assert main() == 1
    assert "forward" in capsys.readouterr().out.lower()


def test_operations_forward_upgrade_preserves_two_business_ledgers(empty_database):
    from alembic import command
    from tabula_rasa.infrastructure.postgres.migrations import migration_config
    from tests.postgres.core_support import seed_core, expense

    with empty_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), '0012_operations')
    alpha, beta = seed_core(empty_database)
    records = [alpha.transactions.create(transaction_id='existing-a', **expense()),
               beta.transactions.create(transaction_id='existing-b', **expense(amount='22.22'))]
    upgrade(empty_database)
    assert empty_database.health()['revision'] == head_revision()
    assert alpha.transactions.list() == [records[0]]
    assert beta.transactions.list() == [records[1]]
    assert alpha.periods.list() == beta.periods.list() == {'enabled': False, 'items': []}
