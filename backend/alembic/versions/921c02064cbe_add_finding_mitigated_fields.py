"""add_finding_mitigated_fields

Revision ID: 921c02064cbe
Revises: 1f44ac74ae59
Create Date: 2026-05-14 02:32:21.714365

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '921c02064cbe'
down_revision: Union[str, None] = '1f44ac74ae59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Task 4: finding mitigated state fields
    existing = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='vulnerability_findings'"
    ))}
    if 'mitigated_at' not in existing:
        op.add_column('vulnerability_findings', sa.Column('mitigated_at', sa.DateTime(timezone=True), nullable=True))
    if 'mitigated_by_cr_id' not in existing:
        op.add_column('vulnerability_findings', sa.Column('mitigated_by_cr_id', postgresql.UUID(as_uuid=True), nullable=True))
    if 'patch_available_at' not in existing:
        op.add_column('vulnerability_findings', sa.Column('patch_available_at', sa.DateTime(timezone=True), nullable=True))

    # Task 5: access review entry remediation CR link
    entry_cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='review_entries'"
    ))}
    if 'remediation_cr_id' not in entry_cols:
        op.add_column('review_entries', sa.Column('remediation_cr_id', postgresql.UUID(as_uuid=True), nullable=True))

    # Task 6: compliance attestations table (create only if not exists)
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
    ))}
    if 'compliance_attestations' not in tables:
        op.create_table(
            'compliance_attestations',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('control_id', sa.String(64), nullable=False),
            sa.Column('attested_by', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('evidence_description', sa.Text(), nullable=False),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id', name='compliance_attestations_pkey'),
        )
        op.create_index('ix_compliance_attestations_organization_id', 'compliance_attestations', ['organization_id'])
        op.create_index('ix_compliance_attestations_control_id', 'compliance_attestations', ['control_id'])


def downgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
    ))}
    if 'compliance_attestations' in tables:
        op.drop_index('ix_compliance_attestations_control_id', table_name='compliance_attestations')
        op.drop_index('ix_compliance_attestations_organization_id', table_name='compliance_attestations')
        op.drop_table('compliance_attestations')
    op.drop_column('review_entries', 'remediation_cr_id')
    op.drop_column('vulnerability_findings', 'patch_available_at')
    op.drop_column('vulnerability_findings', 'mitigated_by_cr_id')
    op.drop_column('vulnerability_findings', 'mitigated_at')
