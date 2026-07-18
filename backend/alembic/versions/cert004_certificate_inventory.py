# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add certificate_inventory table for expiry-worker DB-backed cert tracking.

Revision ID: cert004_cert_inventory
Revises: cred002_cred_rotation_fanout
Create Date: 2026-07-18
"""

from typing import Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision: str = "cert004_cert_inventory"
down_revision: Union[str, None] = "cred002_cred_rotation_fanout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "certificate_inventory",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("san", JSONB(), nullable=False, server_default="[]"),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_request_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_certificate_inventory_organization_id", "certificate_inventory", ["organization_id"])
    op.create_index("ix_certificate_inventory_expires_at", "certificate_inventory", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_certificate_inventory_expires_at", table_name="certificate_inventory")
    op.drop_index("ix_certificate_inventory_organization_id", table_name="certificate_inventory")
    op.drop_table("certificate_inventory")
