"""Add asset_relationships table

Revision ID: 077
Revises: o500i3j6k7l8
Create Date: 2026-06-25
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "077"
down_revision = "o500i3j6k7l8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_relationships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(), nullable=False),
        sa.Column(
            "rel_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_asset_relationships_org_src_tgt_type",
        "asset_relationships",
        ["organization_id", "source_asset_id", "target_asset_id", "relationship_type"],
    )
    op.create_index(
        "ix_asset_relationships_org_source",
        "asset_relationships",
        ["organization_id", "source_asset_id"],
    )
    op.create_index(
        "ix_asset_relationships_org_target",
        "asset_relationships",
        ["organization_id", "target_asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_relationships_org_target", table_name="asset_relationships")
    op.drop_index("ix_asset_relationships_org_source", table_name="asset_relationships")
    op.drop_constraint(
        "uq_asset_relationships_org_src_tgt_type",
        "asset_relationships",
        type_="unique",
    )
    op.drop_table("asset_relationships")
