"""ensure_notifications_table

Creates the notifications table if it does not already exist.
This handles the case where the 165954e097c8 migration (which depends on
revision 049 in a separate branch) was not applied.

Revision ID: 053_ensure_notifications
Revises: 052_all_missing_change_types
Create Date: 2026-05-15 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '053_ensure_notifications'
down_revision: Union[str, None] = '052_all_missing_change_types'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'notifications' not in inspector.get_table_names():
        op.create_table(
            'notifications',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('organization_id', sa.UUID(), nullable=False),
            sa.Column('recipient_user_id', sa.UUID(), nullable=False),
            sa.Column('event_type', sa.String(64), nullable=False),
            sa.Column('resource_type', sa.String(64), nullable=True),
            sa.Column('resource_id', sa.String(64), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id', name='notifications_pkey'),
        )
        op.create_index('ix_notifications_organization_id', 'notifications', ['organization_id'], unique=False)
        op.create_index('ix_notifications_recipient_user_id', 'notifications', ['recipient_user_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'notifications' in inspector.get_table_names():
        op.drop_index('ix_notifications_recipient_user_id', table_name='notifications')
        op.drop_index('ix_notifications_organization_id', table_name='notifications')
        op.drop_table('notifications')
