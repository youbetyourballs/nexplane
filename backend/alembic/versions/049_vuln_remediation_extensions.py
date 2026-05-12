"""vuln remediation extensions: PatchCampaign + finding fields

Revision ID: 049
Revises: 048
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '049'
down_revision = '048'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'patch_campaigns',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('cve_id', sa.String(50), nullable=True),
        sa.Column('target_asset_ids', sa.JSON, nullable=False),
        sa.Column('batch_size', sa.Integer, nullable=False, server_default='5'),
        sa.Column('health_gate_seconds', sa.Integer, nullable=False, server_default='120'),
        sa.Column('health_endpoint', sa.String(500), nullable=False, server_default='/health'),
        sa.Column('abort_threshold', sa.Float, nullable=False, server_default='0.2'),
        sa.Column('rollout_strategy', sa.String(50), nullable=False, server_default='rolling'),
        sa.Column('status', sa.Enum('draft','running','paused','complete','failed','aborted', name='campaign_status'), nullable=False, server_default='draft'),
        sa.Column('batches', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_patch_campaigns_org', 'patch_campaigns', ['organization_id'])
    op.add_column('vulnerability_findings', sa.Column('assigned_to_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('accepted_risk_reason', sa.Text, nullable=True))
    op.add_column('vulnerability_findings', sa.Column('accepted_risk_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('mitigations', sa.JSON, nullable=True))

def downgrade():
    op.drop_column('vulnerability_findings', 'mitigations')
    op.drop_column('vulnerability_findings', 'accepted_risk_expires_at')
    op.drop_column('vulnerability_findings', 'accepted_risk_reason')
    op.drop_column('vulnerability_findings', 'assigned_to_user_id')
    op.drop_index('ix_patch_campaigns_org', table_name='patch_campaigns')
    op.drop_table('patch_campaigns')
    op.execute("DROP TYPE IF EXISTS campaign_status")
