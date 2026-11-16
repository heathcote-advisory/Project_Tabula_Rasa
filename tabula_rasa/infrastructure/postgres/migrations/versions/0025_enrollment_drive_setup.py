"""Add explicit enrollment policy and first-time Drive setup state."""
from alembic import op

revision = '0025_enrollment_drive_setup'
down_revision = '0024_mileage_vehicles'
branch_labels = None
depends_on = None
rollout = {
    'mode': 'maintenance',
    'data': 'Defaults enrollment to approval-required and preserves existing Drive connection bindings.',
    'recovery': 'forward_only',
    'grants': (
        ('column', 'beta_program.enrollment_mode', ('SELECT', 'UPDATE')),
        ('column', 'drive_oauth_requests.setup_selection', ('SELECT', 'UPDATE')),
    ),
}


def upgrade():
    op.execute("""ALTER TABLE beta_program ADD COLUMN enrollment_mode text NOT NULL
        DEFAULT 'approval_required' CHECK (enrollment_mode = 'approval_required'
        OR (enrollment_mode = 'self_service' AND stage = 'expanded'))""")

    op.execute("""ALTER TABLE drive_oauth_requests
        DROP CONSTRAINT drive_oauth_requests_outcome_check,
        ADD CHECK (outcome IN ('connecting','failed','setup','complete','cancelled')),
        ADD COLUMN setup_selection jsonb""")


def downgrade():
    raise ValueError('Enrollment and Drive setup migrations are forward-only.')
