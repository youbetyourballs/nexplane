"""Add auth_mode to organizations

Revision ID: 076
Revises: 075
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None

org_auth_mode = postgresql.ENUM("local", "idp", name="org_auth_mode", create_type=False)


def upgrade():
    op.execute("CREATE TYPE org_auth_mode AS ENUM ('local', 'idp')")
    op.add_column(
        "organizations",
        sa.Column("auth_mode", org_auth_mode, nullable=False, server_default="local"),
    )
    op.add_column(
        "organizations",
        sa.Column("auth_mode_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("organizations", "auth_mode_changed_at")
    op.drop_column("organizations", "auth_mode")
    op.execute("DROP TYPE IF EXISTS org_auth_mode")
