"""Private account authorization and synthetic verified subscription facts."""

from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest
from alembic import command
from sqlalchemy import text

from tabula_rasa.application.billing_policy import SubscriptionTerms
from tabula_rasa.application.billing_reconciliation import (
    ReconciledSubscription, VerifiedSubscriptionEvent, reconcile_subscription_event,
)
from tabula_rasa.application.identity import IdentityError
from tabula_rasa.infrastructure.postgres.billing_accounts import PostgresBillingAccounts
from tabula_rasa.infrastructure.postgres.billing_subscriptions import PostgresBillingSubscriptions
from tabula_rasa.infrastructure.postgres.migrations import migration_config, upgrade
from tabula_rasa.infrastructure.postgres.runtime import DatabaseError
from tests.postgres.test_identity import setup, verified


def test_rekey_refuses_legacy_customer_records_without_discarding_them(empty_database):
    with empty_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "0028_billing_account_links")
    _, _, _, alpha, _ = setup(empty_database)
    with empty_database.transaction() as connection:
        connection.execute(text("INSERT INTO billing_customers (organization_id) VALUES (:org)"), {"org": alpha})
    with pytest.raises(DatabaseError, match="invariant"):
        upgrade(empty_database)
    assert empty_database.health()["revision"] == "0028_billing_account_links"
    with empty_database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT organization_id FROM billing_customers")) == alpha


def test_payer_request_requires_current_business_approval_and_never_grants_membership(database):
    identity, alice, bob, alpha, beta = setup(database)
    admin = identity.resolve_user(verified("link-admin"))
    identity.set_membership(beta, admin.id, "admin")
    accounts = PostgresBillingAccounts(database)
    accounts.create_account(alice.id, "payer")
    accounts.create_account(alice.id, "payer")
    with pytest.raises(IdentityError, match="forbidden"):
        accounts.create_account(bob.id, "payer")
    with pytest.raises(IdentityError, match="forbidden"):
        accounts.request_link(bob.id, "payer", beta)

    request = accounts.request_link(alice.id, "payer", beta)
    assert accounts.request_link(alice.id, "payer", beta) == request
    assert accounts.free_limits_for_organization(beta) is None
    with pytest.raises(IdentityError, match="forbidden"):
        accounts.approve_link(alice.id, beta, request)
    with pytest.raises(IdentityError, match="forbidden"):
        accounts.approve_link(admin.id, alpha, request)
    identity.set_membership(beta, admin.id, "editor")
    with pytest.raises(IdentityError, match="forbidden"):
        accounts.approve_link(admin.id, beta, request)
    identity.set_membership(beta, admin.id, "admin")
    accounts.approve_link(admin.id, beta, request)
    accounts.approve_link(bob.id, beta, request)
    accounts.approve_link(alice.id, alpha, accounts.request_link(alice.id, "payer", alpha))
    assert [item.organization_id for item in identity.allowed_entities(alice.id)] == [alpha]
    assert accounts.free_limits_for_organization(beta).editable
    assert not accounts.free_limits_for_organization(alpha).editable
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT approved_by FROM billing_link_requests WHERE id=:id"),
                                 {"id": request}) == admin.id
        assert connection.scalar(text("SELECT count(*) FROM billing_subscriptions")) == 0

    accounts.create_account(bob.id, "other-payer")
    conflicting = accounts.request_link(bob.id, "other-payer", beta)
    with pytest.raises(IdentityError, match="conflict"):
        accounts.approve_link(bob.id, beta, conflicting)


def test_disabled_payer_cannot_complete_a_pending_link(database):
    identity, alice, _, alpha, _ = setup(database)
    payer = identity.resolve_user(verified("independent-payer"))
    accounts = PostgresBillingAccounts(database)
    accounts.create_account(payer.id, "payer")
    request = accounts.request_link(payer.id, "payer", alpha)
    identity.set_user_state(payer.id, "disabled")
    with pytest.raises(IdentityError, match="forbidden"):
        accounts.approve_link(alice.id, alpha, request)
    assert accounts.free_limits_for_organization(alpha) is None


def _initialize(database, payer, account, external_id):
    accounts = PostgresBillingAccounts(database)
    subscriptions = PostgresBillingSubscriptions(database)
    accounts.create_account(payer, account)
    subscriptions.initialize_free(payer, account, "sub", plan_key="free-plan", plan_version=1)
    with database.transaction() as connection:
        params = {"account": account, "external": external_id}
        connection.execute(text("""INSERT INTO billing_provider_customers
            VALUES (:account, 'synthetic', 'test', :external)"""), params)
        connection.execute(text("""INSERT INTO billing_provider_subscriptions
            VALUES (:account, 'sub', 'synthetic', 'test', :external)"""), params)
    return subscriptions


def test_verified_reconciliation_is_account_scoped_atomic_and_idempotent(database):
    _, alice, bob, alpha, beta = setup(database)
    with database.transaction() as connection:
        connection.exec_driver_sql("""INSERT INTO billing_plan_versions (plan_key, version, entitlements)
            VALUES ('free-plan', 1, '{}'), ('paid-plan', 1, '{}')""")
    repo = _initialize(database, alice.id, "account-a", "external-a")
    _initialize(database, bob.id, "account-b", "external-b")
    accounts = PostgresBillingAccounts(database)
    accounts.approve_link(alice.id, alpha, accounts.request_link(alice.id, "account-a", alpha))
    accounts.approve_link(bob.id, beta, accounts.request_link(alice.id, "account-a", beta))
    with pytest.raises(IdentityError, match="forbidden"):
        repo.for_payer(bob.id, "account-a")
    with pytest.raises(IdentityError, match="conflict"):
        repo.initialize_free(alice.id, "account-a", "second-sub", plan_key="free-plan", plan_version=1)

    first = VerifiedSubscriptionEvent("synthetic", "test", "first", "external-a")
    end = datetime(2026, 12, 1, tzinfo=UTC)
    assert repo.reconciliation_target(first) == ("account-a", 1)
    assert repo.reconciliation_target(replace(first, provider_scope="live")) is None
    assert reconcile_subscription_event(repo, replace(first, provider_scope="live"), lambda _: pytest.fail(
        "An unmapped event must not request provider state.",
    )) == "unmapped"
    kwargs = {"expected_version": 1, "plan_key": "paid-plan", "plan_version": 1,
              "terms": SubscriptionTerms("paid", end)}
    with pytest.raises(IdentityError, match="forbidden"):
        repo.apply_verified_event("account-b", first, **kwargs)
    assert repo.apply_verified_event("account-a", first, **kwargs)
    assert not repo.apply_verified_event("account-a", first, **kwargs)
    with pytest.raises(IdentityError, match="conflict"):
        repo.apply_verified_event("account-b", replace(first, provider_subscription_id="external-b"), **kwargs)

    failure = replace(first, provider_event_id="failed")
    grace = SubscriptionTerms("grace", end, end)
    kwargs["terms"] = grace
    with pytest.raises(IdentityError, match="stale"):
        repo.apply_verified_event("account-a", failure, **kwargs)
    with pytest.raises(IdentityError, match="reference"):
        repo.apply_verified_event("account-a", failure, **{**kwargs, "expected_version": 2, "plan_version": 9})
    assert repo.apply_verified_event("account-a", failure, **{**kwargs, "expected_version": 2})
    retry = replace(failure, provider_event_id="retry")
    retry_terms = SubscriptionTerms("grace", end, end + timedelta(days=1))
    assert repo.apply_verified_event("account-a", retry, **{**kwargs, "expected_version": 3, "terms": retry_terms})
    current = repo.for_payer(alice.id, "account-a")
    assert current.version == 4
    assert current.terms == grace
    assert current.terms.effective_state(as_of=end + timedelta(days=15)) == "free"
    recovery = replace(retry, provider_event_id="recovery")
    paid = SubscriptionTerms("paid", end + timedelta(days=30))
    assert reconcile_subscription_event(repo, recovery, lambda _: None) == "unavailable"
    assert repo.for_payer(alice.id, "account-a").version == 4

    def read_superseded_terms(_):
        assert reconcile_subscription_event(
            repo, recovery, lambda _: ReconciledSubscription("paid-plan", 1, paid),
        ) == "applied"
        return ReconciledSubscription("paid-plan", 1, grace)

    late = replace(retry, provider_event_id="late")
    assert reconcile_subscription_event(repo, late, read_superseded_terms) == "stale"
    assert repo.for_payer(alice.id, "account-a").terms == paid
    assert repo.for_organization(alpha) == repo.for_organization(beta) == repo.for_payer(alice.id, "account-a")
    assert repo.for_payer(bob.id, "account-b").version == 1
    with database.transaction(read_only=True) as connection:
        assert connection.scalar(text("SELECT count(*) FROM billing_event_receipts")) == 4
        assert connection.execute(text("""SELECT state FROM billing_subscription_history
            WHERE billing_account_id='account-a' ORDER BY version""")).scalars().all() == [
                "free", "paid", "grace", "grace", "paid",
            ]
