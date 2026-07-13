# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add migration workflow asset types

Revision ID: mig001
Revises: tunnel002
Create Date: 2026-07-13
"""
from typing import Union
from alembic import op

revision: str = "mig001"
down_revision: Union[str, None] = "tunnel002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'application_profile'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'service_endpoint'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'database_instance'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'network_port'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
