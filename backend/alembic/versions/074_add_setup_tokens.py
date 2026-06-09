"""Add setup_tokens table

Revision ID: 074
Revises: 073
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "setup_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("instance_url", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_setup_tokens_token_hash", "setup_tokens", ["token_hash"], unique=True)


def downgrade():
    op.drop_index("ix_setup_tokens_token_hash", table_name="setup_tokens")
    op.drop_table("setup_tokens")
