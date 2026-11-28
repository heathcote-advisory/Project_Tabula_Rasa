"""Private billing-account projection for a selected organization."""

from sqlalchemy import text

from tabula_rasa.application.billing_policy import BillingMember, FreeBusinessLimits, LinkedBusiness, free_plan_limits


class PostgresBillingAccounts:
    """Read dormant billing data using an authorized internal connection.

    The caller establishes authority over the selected organization before
    invoking this repository. Other businesses supply link order only.
    """

    def __init__(self, database):
        self.database = database

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
