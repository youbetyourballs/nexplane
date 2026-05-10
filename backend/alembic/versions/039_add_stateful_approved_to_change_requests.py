"""add stateful_approved_at to change_requests and agent_containerize_auto enum value

Revision ID: 039
Revises: 038
Create Date: 2026-05-09
"""
from alembic import op
import sqlalchemy as sa

revision = '039'
down_revision = '038'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'change_requests',
        sa.Column('stateful_approved_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'agent_containerize_auto'")


def downgrade() -> None:
    op.drop_column('change_requests', 'stateful_approved_at')
