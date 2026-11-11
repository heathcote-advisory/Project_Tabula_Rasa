"""Retain the verified Drive binding independently of credential custody."""
from alembic import op

revision = '0022_drive_recovery_binding'
down_revision = '0021_customer_lifecycle'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE external_connections ADD COLUMN drive_recovery_binding jsonb')


def downgrade():
    raise ValueError('Connection migrations are forward-only; use verified recovery.')
