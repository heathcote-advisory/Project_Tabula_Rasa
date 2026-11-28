"""Exercise the dormant billing-account foundation on PostgreSQL."""

import pytest
from alembic import command
from sqlalchemy import text

from tabula_rasa.application.billing_policy import FreeBusinessLimits
from tabula_rasa.infrastructure.postgres.billing_accounts import MembershipOrderUnavailable, PostgresBillingAccounts
from tabula_rasa.infrastructure.postgres.migrations import migration_config, upgrade
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tests.postgres._support import entity
from tests.postgres.test_identity import setup, verified


def _account(connection, account, payer):
    connection.execute(text("""
        INSERT INTO billing_accounts (id, payer_user_id) VALUES (:account, :payer)
    """), {"account": account, "payer": payer})


def _link(connection, account, org):
    connection.execute(text("""
        INSERT INTO billing_account_links (organization_id, billing_account_id) VALUES (:org, :account)
    """), {"org": org, "account": account})


def _joined_at(database, org, user):
    with database.transaction(read_only=True) as connection:
        return connection.scalar(text("""
            SELECT joined_at FROM memberships WHERE organization_id=:org AND user_id=:user
        """), {"org": org, "user": user})


def test_upgrade_leaves_existing_memberships_unenrolled_with_unknown_dates(empty_database):
    database = empty_database
    with database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "0027_billing_storage")
    identity, alice, bob, alpha, beta = setup(database)
    original = identity.allowed_entities(alice.id)

    upgrade(database)
    accounts = PostgresBillingAccounts(database)
    assert database.health()["revision"] == "0028_billing_account_links"
    assert identity.allowed_entities(alice.id) == original
    assert _joined_at(database, alpha, alice.id) is None
    assert _joined_at(database, beta, bob.id) is None
    assert accounts.free_limits_for_organization(alpha) is None

    identity.set_membership(alpha, alice.id, "owner")
    assert _joined_at(database, alpha, alice.id) is None
    with database.transaction() as connection:
        _account(connection, "legacy-test", alice.id)
        _link(connection, "legacy-test", alpha)
    with pytest.raises(MembershipOrderUnavailable):
        accounts.free_limits_for_organization(alpha)
    assert accounts.free_limits_for_organization(beta) is None


def test_join_times_survive_role_edits_and_rejoining_uses_a_new_time(database):
    identity, alice, bob, alpha, _ = setup(database)
    assert _joined_at(database, alpha, alice.id) is not None
    identity.set_membership(alpha, bob.id, "editor", active=False)
    assert _joined_at(database, alpha, bob.id) is None
    identity.set_membership(alpha, bob.id, "editor")
    joined = _joined_at(database, alpha, bob.id)
    assert joined is not None
    identity.set_membership(alpha, bob.id, "admin")
    assert _joined_at(database, alpha, bob.id) == joined
    identity.set_membership(alpha, bob.id, "admin", active=False)
    identity.set_membership(alpha, bob.id, "admin")
    assert _joined_at(database, alpha, bob.id) > joined


def test_projection_uses_link_order_and_only_the_selected_business_roster(database):
    identity, alice, bob, alpha, beta = setup(database)
    editor = identity.resolve_user(verified("editor"))
    viewer = identity.resolve_user(verified("viewer"))
    disabled = identity.resolve_user(verified("disabled"))
    identity.set_membership(beta, alice.id, "read_only")
    identity.set_membership(beta, editor.id, "editor")
    identity.set_membership(beta, viewer.id, "read_only")
    identity.set_membership(beta, disabled.id, "admin")
    identity.set_user_state(disabled.id, "disabled")
    gamma = identity.create_organization(alice.id, "Independent")
    unbound = identity.create_organization(alice.id, "Unbound")
    with database.transaction() as connection:
        entity(connection, "gamma")
    identity.bind_entity(gamma, "gamma")
    with database.transaction() as connection:
        _account(connection, "shared-account", alice.id)
        _account(connection, "independent-account", alice.id)
        _link(connection, "shared-account", alpha)
        _link(connection, "shared-account", beta)
        _link(connection, "independent-account", gamma)
    accounts = PostgresBillingAccounts(database)
    assert accounts.free_limits_for_organization(alpha) == FreeBusinessLimits(True, frozenset({alice.id}))
    assert accounts.free_limits_for_organization(beta) == FreeBusinessLimits(
        False, frozenset({bob.id, alice.id, editor.id}),
    )
    assert accounts.free_limits_for_organization(gamma) == FreeBusinessLimits(True, frozenset({alice.id}))
    assert accounts.free_limits_for_organization(unbound) is None

    with pytest.raises(DatabaseError, match="duplicate"), database.transaction() as connection:
        _link(connection, "independent-account", beta)
    with pytest.raises(DatabaseError, match="reference"), database.transaction() as connection:
        _link(connection, "shared-account", unbound)
