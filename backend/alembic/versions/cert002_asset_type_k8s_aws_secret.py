# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add k8s_secret and aws_secret to asset_type enum."""

from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "cert002_assettype_secrets"
down_revision: Union[str, None] = "cert001_certificate_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'k8s_secret'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'aws_secret'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
