"""vuln poc lifecycle: FindingChangeRequest + poc/verification fields

Revision ID: 055
Revises: 054_merge_branches, b994b0b54f7b
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '055'
down_revision = ('054_merge_branches', 'b994b0b54f7b')
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'finding_change_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('finding_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vulnerability_findings.id'), nullable=False),
        sa.Column('cr_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('change_requests.id'), nullable=False),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_finding_change_requests_finding_id', 'finding_change_requests', ['finding_id'])
    op.create_index('ix_finding_change_requests_cr_id', 'finding_change_requests', ['cr_id'])

    op.add_column('vulnerability_findings', sa.Column('exploitability_result', sa.String(50), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('poc_source', sa.String(50), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('poc_ref', sa.String(500), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('exploitability_challenged_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('exploitability_challenge_reason', sa.Text, nullable=True))
    op.add_column('vulnerability_findings', sa.Column('verification_result', sa.String(50), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('verification_failed', sa.Boolean, nullable=False, server_default='false'))


def downgrade():
    for col in ['verification_failed', 'verified_at', 'verification_result',
                'exploitability_challenge_reason', 'exploitability_challenged_at',
                'poc_ref', 'poc_source', 'exploitability_result']:
        op.drop_column('vulnerability_findings', col)
    op.drop_index('ix_finding_change_requests_cr_id', table_name='finding_change_requests')
    op.drop_index('ix_finding_change_requests_finding_id', table_name='finding_change_requests')
    op.drop_table('finding_change_requests')
