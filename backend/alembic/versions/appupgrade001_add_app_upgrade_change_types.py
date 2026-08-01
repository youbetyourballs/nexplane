# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add app upgrade change types

Revision ID: appupgrade001
Revises: wpm001
Create Date: 2026-07-31
"""

from alembic import op

revision = "appupgrade001"
down_revision = "wpm001"
branch_labels = None
depends_on = None


def upgrade():
    for value in [
        "elasticsearch_upgrade",
        "opensearch_upgrade",
        "kafka_zk_to_kraft_bridge",
        "kafka_kraft_cutover",
        "rabbitmq_upgrade",
        "java_runtime_upgrade",
        "python_runtime_upgrade",
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade():
    pass
