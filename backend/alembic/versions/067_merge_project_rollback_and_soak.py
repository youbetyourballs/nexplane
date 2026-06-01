"""merge project_rollback and security_policy_soak heads

Revision ID: 067_merge_rollback
Revises: 066_project_rollback, f1dd971a6d60
Create Date: 2026-05-31
"""
from alembic import op
import sqlalchemy as sa

revision = "067_merge_rollback"
down_revision = ("066_project_rollback", "f1dd971a6d60")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
