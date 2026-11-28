"""Account-scoped subscription persistence and verified-event application."""

from dataclasses import asdict, dataclass
from uuid import uuid4

from sqlalchemy import text

from tabula_rasa.application.billing_policy import SubscriptionTerms
from tabula_rasa.application.billing_reconciliation import VerifiedSubscriptionEvent
from tabula_rasa.application.identity import IdentityError
from .billing_accounts import lock_payer_account
from .control_plane import identity_operation, lock_active_users


@dataclass(frozen=True)
class AccountSubscription:
    id: str
    version: int
    plan_key: str
    plan_version: int
    terms: SubscriptionTerms


def _current(connection, account_id, *, for_update=False):
    query = """SELECT id, version, plan_key, plan_version, state, term_ends_at, first_failed_renewal_at
        FROM billing_subscriptions WHERE billing_account_id=:account"""
    return connection.execute(text(query + (" FOR UPDATE" if for_update else "")),
                              {"account": account_id}).mappings().first()


def _subscription(row):
    return AccountSubscription(
        row["id"], row["version"], row["plan_key"], row["plan_version"],
        SubscriptionTerms(row["state"], row["term_ends_at"], row["first_failed_renewal_at"]),
    )


def _append_history(connection, account_id, receipt_id=None):
    connection.execute(text("""INSERT INTO billing_subscription_history
        (billing_account_id, subscription_id, version, plan_key, plan_version, state,
         term_ends_at, first_failed_renewal_at, event_receipt_id)
        SELECT billing_account_id, id, version, plan_key, plan_version, state,
               term_ends_at, first_failed_renewal_at, :receipt
        FROM billing_subscriptions WHERE billing_account_id=:account"""),
        {"account": account_id, "receipt": receipt_id})


class PostgresBillingSubscriptions:
    """Trusted internal boundary. Provider adapters must verify and reconcile first."""

    def __init__(self, database):
        self.database = database

    def initialize_free(self, payer_id, account_id, subscription_id, *, plan_key, plan_version):
        """Register the server-selected free plan using an ID stable across retries."""
        with identity_operation(self.database) as connection:
            lock_active_users(connection, payer_id)
            lock_payer_account(connection, account_id, payer_id)
            current = _current(connection, account_id)
            if current is not None:
                if current["id"] != subscription_id:
                    raise IdentityError("conflict")
                original = connection.execute(text("""SELECT plan_key, plan_version FROM billing_subscription_history
                    WHERE billing_account_id=:account AND subscription_id=:id AND version=1"""),
                    {"account": account_id, "id": subscription_id}).one()
                if original != (plan_key, plan_version):
                    raise IdentityError("conflict")
                return _subscription(current)
            params = {"account": account_id, "id": subscription_id, "plan": plan_key, "plan_version": plan_version}
            connection.execute(text("""INSERT INTO billing_customers (billing_account_id)
                VALUES (:account) ON CONFLICT (billing_account_id) DO NOTHING"""), params)
            row = connection.execute(text("""INSERT INTO billing_subscriptions
                (billing_account_id, id, version, plan_key, plan_version, state)
                VALUES (:account, :id, 1, :plan, :plan_version, 'free')
                RETURNING id, version, plan_key, plan_version, state, term_ends_at, first_failed_renewal_at"""),
                params).mappings().one()
            _append_history(connection, account_id)
            return _subscription(row)

    def for_payer(self, payer_id, account_id):
        with identity_operation(self.database, read_only=True) as connection:
            if not connection.scalar(text("""SELECT a.id FROM billing_accounts a
                JOIN identity_users u ON u.id=a.payer_user_id AND u.state='active'
                WHERE a.id=:account AND a.payer_user_id=:payer"""), {"account": account_id, "payer": payer_id}):
                raise IdentityError("forbidden")
            row = _current(connection, account_id)
            return _subscription(row) if row is not None else None

    def for_organization(self, organization_id) -> AccountSubscription | None:
        """Resolve terms for an already authorized business operation."""
        with identity_operation(self.database, read_only=True) as connection:
            account_id = connection.scalar(text("""SELECT billing_account_id FROM billing_account_links
                WHERE organization_id=:org"""), {"org": organization_id})
            row = _current(connection, account_id) if account_id is not None else None
            return _subscription(row) if row is not None else None

    def reconciliation_target(self, event: VerifiedSubscriptionEvent) -> tuple[str, int] | None:
        """Resolve the internal account and version before the provider read."""
        with identity_operation(self.database, read_only=True) as connection:
            row = connection.execute(text("""SELECT s.billing_account_id, s.version
                FROM billing_provider_subscriptions p
                JOIN billing_subscriptions s ON (s.billing_account_id, s.id) = (p.billing_account_id, p.subscription_id)
                WHERE p.provider=:provider AND p.provider_scope=:provider_scope
                      AND p.provider_subscription_id=:provider_subscription_id"""), asdict(event)).one_or_none()
            return (row.billing_account_id, row.version) if row is not None else None

    def apply_verified_event(self, account_id, event: VerifiedSubscriptionEvent, *, expected_version,
                             plan_key, plan_version, terms: SubscriptionTerms) -> bool:
        """Commit one authoritative observation. Return False for a confirmed replay.

        Capture expected_version before fetching current provider state. A stale
        version requires fresh reconciliation, even for a late delivery.
        """
        params = {**asdict(event), **asdict(terms), "account": account_id, "receipt": uuid4().hex,
                  "plan": plan_key, "plan_version": plan_version}
        with identity_operation(self.database) as connection:
            current = _current(connection, account_id, for_update=True)
            mapped = connection.scalar(text("""SELECT subscription_id FROM billing_provider_subscriptions
                WHERE billing_account_id=:account AND provider=:provider AND provider_scope=:provider_scope
                      AND provider_subscription_id=:provider_subscription_id FOR SHARE"""), params)
            if current is None or mapped != current["id"]:
                raise IdentityError("forbidden")

            receipt_id = connection.scalar(text("""INSERT INTO billing_event_receipts
                (billing_account_id, id, provider, provider_scope, provider_event_id)
                VALUES (:account, :receipt, :provider, :provider_scope, :provider_event_id)
                ON CONFLICT (provider, provider_scope, provider_event_id) DO NOTHING RETURNING id"""), params)
            if receipt_id is None:
                previous_account = connection.scalar(text("""SELECT billing_account_id FROM billing_event_receipts
                    WHERE provider=:provider AND provider_scope=:provider_scope AND provider_event_id=:provider_event_id"""),
                    params)
                if previous_account != account_id:
                    raise IdentityError("conflict")
                return False
            if current["version"] != expected_version:
                raise IdentityError("stale")
            if current["state"] == terms.state == "grace" and current["term_ends_at"] == terms.term_ends_at:
                params["first_failed_renewal_at"] = min(current["first_failed_renewal_at"], terms.first_failed_renewal_at)
            connection.execute(text("""UPDATE billing_subscriptions SET version=version+1,
                plan_key=:plan, plan_version=:plan_version, state=:state, term_ends_at=:term_ends_at,
                first_failed_renewal_at=:first_failed_renewal_at WHERE billing_account_id=:account"""), params)
            _append_history(connection, account_id, receipt_id)
            return True
