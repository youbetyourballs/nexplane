# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add ecs rolling deploy change types

Revision ID: ecs001
Revises: appupgrade001
Create Date: 2026-08-01
"""

from alembic import op

revision = "ecs001"
down_revision = "appupgrade001"
branch_labels = None
depends_on = None


def upgrade():
    for value in [
        "ecs_rolling_deploy",
        "ecs_task_def_deregister",
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade():
    pass
