# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID

revision: str = "presst001"
down_revision: Union[tuple, None] = ("gcp001", "refsc001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pre_state_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cr_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state_json", JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pre_state_snapshots_cr_id", "pre_state_snapshots", ["cr_id"])
    op.create_index("ix_pre_state_snapshots_org_id", "pre_state_snapshots", ["organization_id"])
    op.create_index("ix_pre_state_snapshots_expires_at", "pre_state_snapshots", ["expires_at"])

    op.add_column(
        "organization_settings",
        sa.Column("pre_state_retention_days", sa.Integer(), nullable=False, server_default="30"),
    )


def downgrade() -> None:
    op.drop_column("organization_settings", "pre_state_retention_days")
    op.drop_index("ix_pre_state_snapshots_expires_at", table_name="pre_state_snapshots")
    op.drop_index("ix_pre_state_snapshots_org_id", table_name="pre_state_snapshots")
    op.drop_index("ix_pre_state_snapshots_cr_id", table_name="pre_state_snapshots")
    op.drop_table("pre_state_snapshots")
