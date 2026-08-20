"""drift detection tables

Revision ID: drift001
Revises: 20260806_smc001, iau004, sdu008
Create Date: 2026-08-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'drift001'
down_revision = ('20260806_smc001', 'iau004', 'sdu008')
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'resource_states',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('asset_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('surface_type', sa.Text(), nullable=False),
        sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('source_cr_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('accepted_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acceptance_note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_cr_id'], ['change_requests.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['accepted_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'asset_id', 'surface_type', name='uq_resource_states_org_asset_surface'),
    )
    op.create_index('ix_resource_states_organization_id', 'resource_states', ['organization_id'])
    op.create_index('ix_resource_states_asset_id', 'resource_states', ['asset_id'])

    op.create_table(
        'drift_policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('scope_type', sa.Text(), nullable=False),
        sa.Column('scope_value', sa.Text(), nullable=False),
        sa.Column('surface_types', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('poll_interval_seconds', sa.Integer(), nullable=False, server_default='3600'),
        sa.Column('auto_created', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('source_cr_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_cr_id'], ['change_requests.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_drift_policies_organization_id', 'drift_policies', ['organization_id'])

    op.create_table(
        'drift_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('asset_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('surface_type', sa.Text(), nullable=False),
        sa.Column('drift_policy_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('baseline_state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('observed_state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('diff', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('severity', sa.Text(), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='open'),
        sa.Column('shadow_cr_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('attested_suppress_until', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['drift_policy_id'], ['drift_policies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['shadow_cr_id'], ['change_requests.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_drift_events_organization_id', 'drift_events', ['organization_id'])
    op.create_index('ix_drift_events_asset_id', 'drift_events', ['asset_id'])

    # Add restore_resource_state to change_type enum
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'restore_resource_state'")


def downgrade() -> None:
    op.drop_table('drift_events')
    op.drop_table('drift_policies')
    op.drop_table('resource_states')
    # Note: cannot remove enum values in Postgres without recreating the type
