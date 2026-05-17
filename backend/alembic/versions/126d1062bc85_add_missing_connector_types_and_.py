"""add_missing_connector_types_and_vulnerability_findings

Revision ID: 126d1062bc85
Revises: 99745eb6873e
Create Date: 2026-05-17 04:28:00.854458

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '126d1062bc85'
down_revision: Union[str, None] = '99745eb6873e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_CONNECTOR_TYPES = [
    'freeipa', 'gitlab', 'teleport', 'opnsense', 'step_ca',
    'postgres', 'redis', 'mongodb',
    'nessus', 'openvas', 'elastic',
    'winrm', 'sccm', 'intune', 'wufb', 'laps',
    'bind_dns', 'slack', 'jfrog',
]


def upgrade() -> None:
    # Add new connector_type enum values
    for val in NEW_CONNECTOR_TYPES:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")

    # Create vulnerability_findings table (was built but migration not applied)
    op.create_table(
        'vulnerability_findings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('asset_id', postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column('scanner', sa.String(64), nullable=False),
        sa.Column('scanner_finding_id', sa.String(255), nullable=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('finding_type', sa.String(64), nullable=False),
        sa.Column('severity', sa.String(32), nullable=False),
        sa.Column('cve_id', sa.String(32), nullable=True),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('hostname', sa.String(255), nullable=True),
        sa.Column('affected_package', sa.String(255), nullable=True),
        sa.Column('affected_version', sa.String(128), nullable=True),
        sa.Column('fixed_version', sa.String(128), nullable=True),
        sa.Column('cvss_score', sa.Float(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='open'),
        sa.Column('sla_breached', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('escalated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('raw_data', postgresql.JSONB(), nullable=True),
        sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
    )
    op.create_index('ix_vuln_findings_org', 'vulnerability_findings', ['organization_id'])
    op.create_index('ix_vuln_findings_asset', 'vulnerability_findings', ['asset_id'])
    op.create_index('ix_vuln_findings_severity', 'vulnerability_findings', ['severity'])
    op.create_index('ix_vuln_findings_scanner_id', 'vulnerability_findings',
                    ['scanner', 'scanner_finding_id'], unique=True)


def downgrade() -> None:
    op.drop_table('vulnerability_findings')
    # Note: Postgres does not support removing enum values — downgrade cannot undo enum additions
