"""add compliance_baselines table

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'compliance_baselines',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scope_type', sa.Text(), nullable=False),
        sa.Column('scope_value', sa.Text(), nullable=False),
        sa.Column('cis_level', sa.Integer(), nullable=False),
        sa.Column('os_family', sa.Text(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('history', postgresql.ARRAY(postgresql.JSONB()), nullable=False,
                  server_default='{}'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('auto_execute', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("scope_type IN ('asset', 'tag')", name='ck_baseline_scope_type'),
        sa.CheckConstraint('cis_level IN (1, 2)', name='ck_baseline_cis_level'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_compliance_baselines_scope',
        'compliance_baselines',
        ['scope_type', 'scope_value'],
    )
    op.create_index(
        'idx_compliance_baselines_org',
        'compliance_baselines',
        ['organization_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_compliance_baselines_scope', table_name='compliance_baselines')
    op.drop_index('idx_compliance_baselines_org', table_name='compliance_baselines')
    op.drop_table('compliance_baselines')
