"""add_identity_security_cr_fields

Revision ID: e4a56443300c
Revises: a1b2c3d4e5f6
Create Date: 2026-05-14 02:49:38.550688

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e4a56443300c'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new ChangeType enum values for identity security
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'emergency_user_lockout'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'user_suspension'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'user_scope_reduction'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'enforce_mfa'")

    # Add scheduling / time-bound access fields to change_requests
    op.add_column('change_requests', sa.Column('execute_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('change_requests', sa.Column('access_expiry_hours', sa.Float(), nullable=True))
    op.add_column('change_requests', sa.Column('scheduled_rollback_cr_id', sa.UUID(), nullable=True))
    op.create_index('ix_change_requests_execute_at', 'change_requests', ['execute_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_change_requests_execute_at', table_name='change_requests')
    op.drop_column('change_requests', 'scheduled_rollback_cr_id')
    op.drop_column('change_requests', 'access_expiry_hours')
    op.drop_column('change_requests', 'execute_at')
    # Note: PostgreSQL does not support removing enum values; downgrade leaves enum values in place
