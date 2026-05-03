"""add_secret_versions_table

Revision ID: 016
Revises: 015
Create Date: 2026-05-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid


# revision identifiers, used by Alembic.
revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secret_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False),
        sa.Column(
            "secret_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secrets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("ciphertext", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum("current", "superseded", name="secret_version_status"),
            nullable=False,
            default="current",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Index to quickly find the current version for a secret
    op.create_index(
        "ix_secret_versions_secret_id_status",
        "secret_versions",
        ["secret_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_secret_versions_secret_id_status", table_name="secret_versions")
    op.drop_table("secret_versions")
    op.execute("DROP TYPE IF EXISTS secret_version_status")
