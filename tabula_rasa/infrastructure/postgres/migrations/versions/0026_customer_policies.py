"""Immutable evidence of explicit reviewed-policy acknowledgement."""
from pathlib import Path
from alembic import op

revision = '0026_customer_policies'
down_revision = '0025_enrollment_drive_setup'
branch_labels = None
depends_on = None
rollout = {
    'mode': 'maintenance',
    'data': 'Adds acknowledgement evidence; existing users are not marked as accepting.',
    'recovery': 'forward_only',
    'grants': (('table', 'customer_policy_acknowledgements', ('SELECT', 'INSERT')),),
}


def upgrade():
    op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise ValueError('Policy evidence is forward-only; use compatible recovery.')
