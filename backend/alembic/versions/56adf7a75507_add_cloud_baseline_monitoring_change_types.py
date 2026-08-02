# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""add cloud baseline monitoring change types

Revision ID: 56adf7a75507
Revises: wpm001
Create Date: 2026-08-02
"""
from alembic import op

revision = '56adf7a75507'
down_revision = 'wpm001'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'aws_account_baseline_monitoring',
        'gcp_account_baseline_monitoring',
        'azure_account_baseline_monitoring',
        'oci_account_baseline_monitoring',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
