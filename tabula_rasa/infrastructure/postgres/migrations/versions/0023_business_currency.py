"""Permit explicit, authorized currency-label changes with private audit history."""
from pathlib import Path
from alembic import op

revision = '0023_business_currency'
down_revision = '0022_drive_recovery_binding'
branch_labels = None
depends_on = None
rollout = {
    'mode': 'maintenance',
    'data': 'Replaces the currency update guard and removes redundant admission currency; financial records are unchanged.',
    'recovery': 'forward_only',
    'grants': (('column', 'business_entities.currency_code', ('SELECT', 'UPDATE')),),
}


def upgrade():
    op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise ValueError('Currency migrations are forward-only; use verified recovery.')
