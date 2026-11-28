"""Store billing account links and record membership join times."""

from pathlib import Path

from alembic import op

revision = "0028_billing_account_links"
down_revision = "0027_billing_storage"
branch_labels = None
depends_on = None
rollout = {
    "mode": "maintenance",
    "data": "Adds dormant billing accounts and links. Existing memberships receive the migration timestamp.",
    "recovery": "forward_only",
    "grants": (),
}


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise ValueError("Billing account links are forward-only; use compatible recovery.")
