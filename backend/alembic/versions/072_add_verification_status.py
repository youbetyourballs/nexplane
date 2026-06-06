"""Add verification_status enum and column to change_requests

Revision ID: 072
Revises: 071
Create Date: 2026-06-05
"""
import sqlalchemy as sa
from alembic import op

revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TYPE verification_status AS ENUM (
            'passed', 'failed', 'unsupported', 'manual_required', 'skipped_development_only'
        )
    """)
    op.add_column(
        "change_requests",
        sa.Column(
            "verification_status",
            sa.Enum(
                "passed", "failed", "unsupported", "manual_required", "skipped_development_only",
                name="verification_status",
            ),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("change_requests", "verification_status")
    op.execute("DROP TYPE IF EXISTS verification_status")
