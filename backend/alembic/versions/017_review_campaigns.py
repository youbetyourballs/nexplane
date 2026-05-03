"""Replace access_reviews with review_campaigns + review_entries.

Revision ID: b3f7d2e9a1c5
Revises: ad8f4c9e1b2d
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'b3f7d2e9a1c5'
down_revision = 'ad8f4c9e1b2d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS access_review_change_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS access_reviews CASCADE")

    op.create_table(
        "review_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("campaign_type", sa.String(50), nullable=False),
        sa.Column("scope", JSONB, nullable=False, server_default="{}"),
        sa.Column("reviewer_assignment_rule", JSONB, nullable=False, server_default="{}"),
        sa.Column("evidence_options", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_campaigns_org_id", "review_campaigns", ["organization_id"])
    op.create_index("ix_review_campaigns_status", "review_campaigns", ["status"])

    op.create_table(
        "review_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("review_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("user_display_name", sa.String(255), nullable=True),
        sa.Column("user_status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("resource_name", sa.String(500), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("connector_id", UUID(as_uuid=True), nullable=True),
        sa.Column("permission_level", sa.String(100), nullable=False),
        sa.Column("is_privileged", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("reviewer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_unresolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("decision", sa.String(10), nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("change_request_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_review_entries_campaign_id", "review_entries", ["campaign_id"])
    op.create_index("ix_review_entries_reviewer_id", "review_entries", ["reviewer_id"])


def downgrade() -> None:
    op.drop_table("review_entries")
    op.drop_table("review_campaigns")
