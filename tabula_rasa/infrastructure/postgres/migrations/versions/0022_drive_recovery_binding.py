"""Retain the verified Drive binding independently of credential custody."""
from alembic import op

revision = '0022_drive_recovery_binding'
down_revision = '0021_customer_lifecycle'
branch_labels = None
depends_on = None
rollout = {
    # The serving 0021 image gates readiness on its exact Alembic head.
    "mode": "maintenance",
    "data": "Takes an exclusive table lock. Existing connection rows and custody grants are not rewritten.",
    "recovery": "forward_only",
    "grants": (
        ("column", "external_connections.drive_recovery_binding", ("SELECT", "INSERT", "UPDATE")),
    ),
}


def upgrade():
    op.execute('ALTER TABLE external_connections ADD COLUMN drive_recovery_binding jsonb')


def downgrade():
    raise ValueError('Connection migrations are forward-only; use verified recovery.')
