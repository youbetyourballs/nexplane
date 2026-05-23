"""identity_graph

Revision ID: b994b0b54f7b
Revises: 6c5d4e3f2a1b
Create Date: 2026-05-23 00:00:00.000000

"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = 'b994b0b54f7b'
down_revision: Union[str, None] = '6c5d4e3f2a1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create identity_profiles table
    op.create_table(
        'identity_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False, server_default=''),
        sa.Column('primary_email', sa.String(), nullable=False),
        sa.Column('source_idp_connector_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('connectors.id'), nullable=True),
        sa.Column('correlation_method', sa.String(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_identity_profiles_primary_email', 'identity_profiles', ['primary_email'])

    # Create identity_accounts table
    op.create_table(
        'identity_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('identity_profile_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('identity_profiles.id'), nullable=False),
        sa.Column('connector_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('connectors.id'), nullable=False),
        sa.Column('connector_type', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False, server_default=''),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('raw_attributes', postgresql.JSONB(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_stale', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index("ix_identity_accounts_identity_profile_id", "identity_accounts", ["identity_profile_id"])
    op.create_unique_constraint(
        "uq_identity_accounts_connector_external",
        "identity_accounts",
        ["connector_id", "external_id"],
    )

    # Add parent_change_request_id to change_requests (self-referential FK)
    op.add_column('change_requests',
        sa.Column('parent_change_request_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_change_requests_parent_cr_id',
        'change_requests', 'change_requests',
        ['parent_change_request_id'], ['id']
    )

    # Add new ChangeType enum values
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'identity_snapshot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'identity_reconstitute'")


def downgrade() -> None:
    op.drop_constraint('fk_change_requests_parent_cr_id', 'change_requests', type_='foreignkey')
    op.drop_column('change_requests', 'parent_change_request_id')
    op.drop_constraint("uq_identity_accounts_connector_external", "identity_accounts", type_='unique')
    op.drop_index("ix_identity_accounts_identity_profile_id", table_name="identity_accounts")
    op.drop_index('ix_identity_profiles_primary_email', table_name='identity_profiles')
    op.drop_table('identity_accounts')
    op.drop_table('identity_profiles')
    # Note: PostgreSQL does not support removing enum values; downgrade omits enum rollback
