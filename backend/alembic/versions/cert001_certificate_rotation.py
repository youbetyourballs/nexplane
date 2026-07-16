# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add certificate_rotation change type."""

from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "cert001_certificate_rotation"
down_revision: Union[str, None] = "cred001_credential_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'certificate_rotation'")


def downgrade() -> None:
    pass
