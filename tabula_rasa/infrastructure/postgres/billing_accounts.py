"""Private payer accounts, link approvals, and free-plan projections."""

from uuid import uuid4
from sqlalchemy import text

from tabula_rasa.application.billing_policy import BillingMember, FreeBusinessLimits, LinkedBusiness, free_plan_limits
from tabula_rasa.application.identity import IdentityError
from .control_plane import ENTITY_ACCESS_SQL, identity_operation, lock_active_users


def lock_payer_account(connection, account_id, payer_id):
    if not connection.scalar(text("""SELECT id FROM billing_accounts
        WHERE id=:account AND payer_user_id=:payer FOR UPDATE"""), {"account": account_id, "payer": payer_id}):
        raise IdentityError("forbidden")


class PostgresBillingAccounts:
    """Read dormant billing data using an authorized internal connection.

    The caller establishes authority over the selected organization before
    invoking this repository. Other businesses supply link order only.
    """

    def __init__(self, database):
        self.database = database

    def create_account(self, payer_id: str, account_id: str) -> None:
        """Use an account ID assigned by the trusted caller before retry."""
        with identity_operation(self.database) as connection:
            lock_active_users(connection, payer_id)
            connection.execute(text("""INSERT INTO billing_accounts (id, payer_user_id)
                VALUES (:account, :payer) ON CONFLICT (id) DO NOTHING"""),
                {"account": account_id, "payer": payer_id})
            lock_payer_account(connection, account_id, payer_id)

    def request_link(self, payer_id: str, account_id: str, organization_id: str) -> str:
        """Request a link without changing business authority or charging."""
        with identity_operation(self.database) as connection:
            lock_active_users(connection, payer_id)
            lock_payer_account(connection, account_id, payer_id)
            params = {"account": account_id, "org": organization_id, "payer": payer_id, "id": uuid4().hex}
            request_id = connection.scalar(text("""INSERT INTO billing_link_requests
                (id, billing_account_id, organization_id, requested_by) VALUES (:id, :account, :org, :payer)
                ON CONFLICT (billing_account_id, organization_id) DO NOTHING RETURNING id"""), params)
            if request_id is None:
                request_id = connection.scalar(text("""SELECT id FROM billing_link_requests
                    WHERE billing_account_id=:account AND organization_id=:org AND requested_by=:payer"""), params)
                if request_id is None:
                    raise IdentityError("conflict")
            return request_id

    def approve_link(self, approver_id: str, organization_id: str, request_id: str) -> None:
        """Approve the payer's exact request using current business authority."""
        with identity_operation(self.database) as connection:
            params = {"id": request_id, "org": organization_id, "user": approver_id}
            request = connection.execute(text("""SELECT billing_account_id, requested_by
                FROM billing_link_requests WHERE id=:id AND organization_id=:org"""), params).mappings().first()
            if request is None:
                raise IdentityError("forbidden")
            payer = request["requested_by"]
            account = request["billing_account_id"]
            lock_active_users(connection, approver_id, payer)
            lock_payer_account(connection, account, payer)
            if not connection.scalar(text("SELECT id FROM organizations WHERE id=:org FOR UPDATE"), params):
                raise IdentityError("forbidden")
            access = connection.execute(text(ENTITY_ACCESS_SQL + " AND e.organization_id=:org"), params).mappings().first()
            if access is None or access["role"] not in {"owner", "admin"} or access["closure_expires_at"] is not None:
                raise IdentityError("forbidden")
            params["account"] = account
            linked = connection.scalar(text("""INSERT INTO billing_account_links (organization_id, billing_account_id)
                VALUES (:org, :account) ON CONFLICT (organization_id) DO NOTHING RETURNING billing_account_id"""), params)
            if linked is None:
                linked = connection.scalar(text("""SELECT billing_account_id FROM billing_account_links
                    WHERE organization_id=:org"""), params)
                if linked != account:
                    raise IdentityError("conflict")
            connection.execute(text("""UPDATE billing_link_requests SET approved_by=:user, approved_at=clock_timestamp()
                WHERE id=:id AND approved_at IS NULL"""), params)

    def free_limits_for_organization(self, organization_id: str) -> FreeBusinessLimits | None:
        with self.database.transaction(read_only=True) as connection:
            links = tuple(LinkedBusiness(**row) for row in connection.execute(text("""
                SELECT link.organization_id, link.linked_at
                FROM billing_account_links selected
                JOIN billing_account_links link ON link.billing_account_id = selected.billing_account_id
                WHERE selected.organization_id = :organization_id
            """), {"organization_id": organization_id}).mappings())
            if not links:
                return None

            members = connection.execute(text("""
                SELECT m.user_id, m.role, m.joined_at
                FROM memberships m
                JOIN identity_users u ON u.id = m.user_id AND u.state = 'active'
                WHERE m.organization_id = :organization_id AND m.state = 'active'
            """), {"organization_id": organization_id}).mappings().all()
            return free_plan_limits(
                links, organization_id=organization_id, active_members=tuple(BillingMember(**row) for row in members),
            )
