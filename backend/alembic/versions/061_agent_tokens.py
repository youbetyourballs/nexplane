"""add agent_tokens table

Revision ID: 061_agent_tokens
Revises: 060_add_mitigation_cr_types
Create Date: 2026-05-26
"""
from typing import Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = '061_agent_tokens'
down_revision: Union[str, None] = '060_add_mitigation_cr_types'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean, default=False, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("allowed_connector_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_asset_tags", JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_cr_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_roles", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("ix_agent_tokens_token_hash", "agent_tokens", ["token_hash"])
    op.create_index("ix_agent_tokens_organization_id", "agent_tokens", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_organization_id", table_name="agent_tokens")
    op.drop_index("ix_agent_tokens_token_hash", table_name="agent_tokens")
    op.drop_table("agent_tokens")
