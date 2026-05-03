"""add change_freeze_windows table

Revision ID: 017
Revises: 016
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'change_freeze_windows',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('emergency_bypass_role', sa.Text(), nullable=False,
                  server_default='emergency_bypass'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint('end_at > start_at', name='ck_freeze_window_dates'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_freeze_windows_active',
        'change_freeze_windows',
        ['start_at', 'end_at'],
    )


def downgrade() -> None:
    op.drop_index('idx_freeze_windows_active', table_name='change_freeze_windows')
    op.drop_table('change_freeze_windows')
