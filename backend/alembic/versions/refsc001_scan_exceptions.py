# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""scan_exceptions

Revision ID: refsc001
Revises: mig001
Create Date: 2026-07-14

"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "refsc001"
down_revision: Union[str, None] = "mig001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new change_type enum values for reference scan/update.
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block in PostgreSQL < 12.
    # We use op.execute() here; alembic runs DDL in transaction by default but PostgreSQL
    # silently commits ADD VALUE before the transaction end, so this works for PG 10+.
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'scan_for_references'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'update_reference'")

    op.create_table(
        "scan_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_cr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matched_term", sa.String(length=512), nullable=False),
        sa.Column("location", sa.String(length=1024), nullable=False),
        sa.Column("surface", sa.String(length=64), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("suggested_action", sa.String(length=256), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_by_cr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["consumer_asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["resolved_by_cr_id"], ["change_requests.id"]),
        sa.ForeignKeyConstraint(["scan_cr_id"], ["change_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scan_exceptions_organization_id"), "scan_exceptions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_scan_exceptions_scan_cr_id"), "scan_exceptions", ["scan_cr_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scan_exceptions_scan_cr_id"), table_name="scan_exceptions")
    op.drop_index(op.f("ix_scan_exceptions_organization_id"), table_name="scan_exceptions")
    op.drop_table("scan_exceptions")
