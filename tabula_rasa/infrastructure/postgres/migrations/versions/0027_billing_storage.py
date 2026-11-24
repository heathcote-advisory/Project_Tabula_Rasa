"""Dormant, provider-neutral billing persistence."""

from pathlib import Path

from alembic import op

revision = "0027_billing_storage"
down_revision = "0026_customer_policies"
branch_labels = None
depends_on = None
rollout = {
    "mode": "maintenance",
    "data": "Adds empty billing tables without enrolling any existing organization.",
    "recovery": "forward_only",
    "grants": (),
}


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise ValueError("Billing storage is forward-only; use compatible recovery.")
