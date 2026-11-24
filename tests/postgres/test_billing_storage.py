"""Provider-neutral persistence constraints on real PostgreSQL."""

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tabula_rasa.deployment.schema_migration import rollout_path
from tabula_rasa.infrastructure.postgres.migrations import migration_config, upgrade
from tests.postgres.test_identity import setup


BILLING_TABLES = (
    "billing_plan_versions", "billing_customers", "billing_subscriptions",
    "billing_provider_customers", "billing_provider_subscriptions",
    "billing_event_receipts", "billing_subscription_history",
)


def test_upgrade_leaves_existing_organizations_unenrolled(empty_database):
    database = empty_database
    previous, target = "0026_customer_policies", "0027_billing_storage"
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), previous)
    _, _, _, alpha, beta = setup(database)

    assert rollout_path(previous, target)[0].module.rollout["grants"] == ()
    upgrade(database)

    assert database.health()["revision"] == target
    with database.engine.connect() as connection:
        assert set(connection.scalars(text("SELECT id FROM organizations"))) == {alpha, beta}
        assert set(connection.execute(text("SELECT organization_id, role FROM memberships"))) == {
            (alpha, "owner"), (beta, "owner"),
        }
        assert set(connection.scalars(text("""
            SELECT relname FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relrowsecurity AND relname = ANY(:tables)
        """), {"tables": list(BILLING_TABLES)})) == set(BILLING_TABLES)
        for table in BILLING_TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def _assert_rejected(connection, statement, sqlstate, **params):
    with pytest.raises(DBAPIError) as error, connection.begin_nested():
        connection.execute(text(statement), params)
    assert error.value.orig.sqlstate == sqlstate


def test_plan_versions_provider_event_identity_and_tenant_references(database):
    _, _, _, alpha, beta = setup(database)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("""
            INSERT INTO billing_plan_versions (plan_key, version, entitlements)
            VALUES ('internal-plan', 1, '{"business_limit": 1}'), ('internal-plan', 2, '{"business_limit": 2}')
        """)
        for org, suffix in ((alpha, "alpha"), (beta, "beta")):
            connection.execute(text("INSERT INTO billing_customers (organization_id) VALUES (:org)"), {"org": org})
            connection.execute(text("""
                INSERT INTO billing_subscriptions (organization_id, id, plan_key, plan_version, state)
                VALUES (:org, :subscription, 'internal-plan', 1, 'trial')
            """), {"org": org, "subscription": suffix})
            connection.execute(text("""
                INSERT INTO billing_provider_customers VALUES (:org, 'example', 'sandbox', :customer)
            """), {"org": org, "customer": suffix})
        provider_subscription = """
            INSERT INTO billing_provider_subscriptions
            VALUES (:org, :subscription, 'example', 'sandbox', 'external-subscription')
        """
        _assert_rejected(connection, provider_subscription, "23503", org=alpha, subscription="beta")
        connection.execute(text(provider_subscription), {"org": beta, "subscription": "beta"})
        _assert_rejected(connection, provider_subscription, "23505", org=alpha, subscription="alpha")

        receipt = """
            INSERT INTO billing_event_receipts (organization_id, id, provider, provider_scope, provider_event_id)
            VALUES (:org, :id, 'example', :scope, 'event-1')
        """
        connection.execute(text(receipt), {"org": beta, "id": "receipt", "scope": "sandbox"})
        _assert_rejected(connection, receipt, "23505", org=beta, id="replay", scope="sandbox")
        _assert_rejected(connection, receipt, "23505", org=alpha, id="receipt", scope="sandbox")
        _assert_rejected(connection, receipt, "23503", org=alpha, id="receipt", scope="live")

        history = """
            INSERT INTO billing_subscription_history
                (organization_id, subscription_id, version, plan_key, plan_version, state, event_receipt_id)
            VALUES (:org, :subscription, :version, 'internal-plan', :plan_version, 'active', 'receipt')
        """
        _assert_rejected(connection, history, "23503", org=alpha, subscription="alpha", version=1, plan_version=2)
        _assert_rejected(connection, history, "23503", org=beta, subscription="beta", version=1, plan_version=3)
        connection.execute(text(history), {"org": beta, "subscription": "beta", "version": 1, "plan_version": 2})
        _assert_rejected(connection, history, "23505", org=beta, subscription="beta", version=2, plan_version=2)
        connection.execute(text("""
            UPDATE billing_subscriptions SET plan_version = 2, state = 'active' WHERE organization_id = :org
        """), {"org": beta})
        assert connection.execute(text("""
            SELECT organization_id, plan_version, state FROM billing_subscriptions ORDER BY id
        """)).all() == [(alpha, 1, "trial"), (beta, 2, "active")]

        connection.execute(text("""
            INSERT INTO billing_provider_customers VALUES (:org, 'example', 'other-sandbox', 'beta')
        """), {"org": alpha})
        connection.execute(text(receipt), {"org": alpha, "id": "receipt", "scope": "other-sandbox"})
        assert connection.scalar(text("SELECT count(*) FROM billing_event_receipts")) == 2


def test_dormant_tables_deny_runtime_and_protect_immutable_records(database):
    _, _, _, alpha, _ = setup(database)
    role = "billing_runtime_" + uuid4().hex
    # Closing this uncommitted connection also rolls back the cluster-level role.
    with database.engine.connect() as connection:
        connection.execute(text("INSERT INTO billing_customers (organization_id) VALUES (:org)"), {"org": alpha})
        for table in ("billing_plan_versions", "billing_event_receipts", "billing_subscription_history"):
            _assert_rejected(connection, f"DELETE FROM {table}", "23514")
            _assert_rejected(connection, f"TRUNCATE {table} CASCADE", "23514")
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        relation_list = ", ".join(BILLING_TABLES)
        connection.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {relation_list} TO "{role}"')
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
        assert connection.scalar(text("SELECT count(*) FROM billing_customers")) == 0
        _assert_rejected(
            connection, "INSERT INTO billing_customers (organization_id) VALUES (:org)", "42501", org=alpha,
        )
