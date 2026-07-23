# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add tier-zero AD and upgrade CR types

Revision ID: r800s9t0u1v2
Revises: q700m5n8o9p0
Create Date: 2026-07-23

"""
from alembic import op

revision = 'r800s9t0u1v2'
down_revision = 'q700m5n8o9p0'
branch_labels = None
depends_on = None

NEW_TYPES = [
    "ad_dc_parallel_upgrade",
    "ad_domain_functional_level_upgrade",
    "ad_trust_create",
    "ad_gpo_deploy",
    "ad_pso_manage",
    "ad_stale_computer_cleanup",
    "db_major_version_upgrade",
    "k8s_cluster_upgrade",
    "windows_os_upgrade",
]


def upgrade():
    for value in NEW_TYPES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade():
    pass
