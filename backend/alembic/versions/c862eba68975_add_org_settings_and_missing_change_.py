"""add_org_settings_and_missing_change_types

Revision ID: c862eba68975
Revises: 94b872c1854a
Create Date: 2026-05-17 07:40:18.839168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c862eba68975'
down_revision: Union[str, None] = '94b872c1854a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_CHANGE_TYPES = [
    'dc_integrity_check', 'ad_forest_snapshot', 'ad_forest_restore',
    'ad_tiered_backup', 'discover_ad_snapshots',
    'list_dns_records', 'create_dns_record', 'delete_dns_record', 'update_dns_record',
    'bind_list_zone', 'bind_create_record', 'bind_delete_record', 'bind_check_record',
]


def upgrade() -> None:
    op.create_table(
        'organization_settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id'), nullable=False, unique=True),
        sa.Column('anthropic_api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('agent_secret_encrypted', sa.Text(), nullable=True),
        sa.Column('ai_providers_encrypted', sa.Text(), nullable=True),
        sa.Column('sla_config', sa.JSON(), nullable=True),
        sa.Column('escalation_chain', postgresql.JSONB(), nullable=True),
        sa.Column('escalation_timeout_minutes', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    for val in NEW_CHANGE_TYPES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade() -> None:
    op.drop_table('organization_settings')
