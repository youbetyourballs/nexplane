"""add access_reviews and access_review_change_requests tables

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "access_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="collecting"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", JSONB(), nullable=True),
        sa.Column("decisions", JSONB(), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_table(
        "access_review_change_requests",
        sa.Column("review_id", UUID(as_uuid=True), sa.ForeignKey("access_reviews.id"), primary_key=True),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), primary_key=True),
    )
    op.create_index("ix_access_reviews_status", "access_reviews", ["status"])
    op.create_index("ix_access_reviews_created_by", "access_reviews", ["created_by"])


def downgrade():
    op.drop_index("ix_access_reviews_created_by", table_name="access_reviews")
    op.drop_index("ix_access_reviews_status", table_name="access_reviews")
    op.drop_table("access_review_change_requests")
    op.drop_table("access_reviews")
