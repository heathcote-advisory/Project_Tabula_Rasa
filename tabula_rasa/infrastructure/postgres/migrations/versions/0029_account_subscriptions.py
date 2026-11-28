"""Move dormant subscription records to the shared payer account."""

from pathlib import Path

from alembic import op

revision = "0029_account_subscriptions"
down_revision = "0028_billing_account_links"
branch_labels = None
depends_on = None
rollout = {
    "mode": "maintenance",
    "data": "Rekeys empty billing customer tables to payer accounts and adds private link approvals.",
    "recovery": "forward_only",
    "grants": (),
}


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise ValueError("Account subscription storage is forward-only; use compatible recovery.")
