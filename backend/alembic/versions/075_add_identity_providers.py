"""Add identity_providers table

Revision ID: 075
Revises: 074
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TYPE idp_type AS ENUM ('oidc', 'ldap', 'saml')")
    op.execute("CREATE TYPE idp_status AS ENUM ('pending', 'active')")

    op.create_table(
        "identity_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("type", sa.Enum("oidc", "ldap", "saml", name="idp_type", create_type=False), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum("pending", "active", name="idp_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("connectors.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_identity_providers_org_id", "identity_providers", ["org_id"])


def downgrade():
    op.drop_index("ix_identity_providers_org_id", table_name="identity_providers")
    op.drop_table("identity_providers")
    op.execute("DROP TYPE IF EXISTS idp_status")
    op.execute("DROP TYPE IF EXISTS idp_type")
