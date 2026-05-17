"""add runbook auto_execute

Revision ID: c9d8e7f6a5b4
Revises: b3c4d5e6f7a8
Create Date: 2026-05-17

"""
from alembic import op
import sqlalchemy as sa

revision = "c9d8e7f6a5b4"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runbooks",
        sa.Column("auto_execute", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("runbooks", "auto_execute")
