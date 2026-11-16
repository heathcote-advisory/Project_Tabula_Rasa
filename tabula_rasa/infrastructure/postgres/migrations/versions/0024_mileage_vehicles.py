"""Managed mileage vehicles with lossless exact-label historical mapping."""
from pathlib import Path
from alembic import op

revision = '0024_mileage_vehicles'
down_revision = '0023_business_currency'
branch_labels = None
depends_on = None
rollout = {
    'mode': 'maintenance',
    'data': 'Maps exact historical vehicle labels per business; retains original text and mileage values.',
    'recovery': 'forward_only',
    'grants': (
        ('table', 'vehicles', ('SELECT', 'INSERT', 'UPDATE', 'DELETE')),
        ('column', 'mileage_records.vehicle_id', ('SELECT', 'INSERT', 'UPDATE')),
    ),
}


def upgrade():
    op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise ValueError('Vehicle migrations are forward-only; use verified recovery.')
