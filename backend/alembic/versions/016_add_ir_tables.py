"""add IR tables and change_request columns

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- ir_playbook_templates ---
    op.create_table(
        "ir_playbook_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("playbook_type", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("default_parameters", JSONB, nullable=False, server_default="{}"),
        sa.Column("ir_auto_approve", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # Seed data
    op.execute("""
        INSERT INTO ir_playbook_templates (playbook_type, display_name, description, default_parameters) VALUES
        ('isolate_host',       'Host Isolation',        'Isolate a compromised host via agent-side firewall rules',                   '{"management_cidr": "10.0.0.0/8"}'),
        ('lockdown_account',   'Account Lockdown',      'Disable a user across all identity connectors simultaneously',              '{}'),
        ('phishing_response',  'Phishing Response',     'Block sender domain, reset passwords, revoke sessions, re-enroll MFA',     '{}'),
        ('preserve_evidence',  'Evidence Preservation', 'Collect forensic artifacts from host before remediation',                  '{"include_memory_dump": false}')
    """)

    # --- forensic_bundles ---
    op.create_table(
        "forensic_bundles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("upload_url", sa.Text, nullable=False),
        sa.Column("manifest", JSONB, nullable=False, server_default="{}"),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_forensic_bundles_asset_id", "forensic_bundles", ["asset_id"])
    op.create_index("idx_forensic_bundles_collected_at", "forensic_bundles", ["collected_at"])

    # --- change_requests new columns ---
    op.add_column("change_requests", sa.Column("incident_response", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("change_requests", sa.Column("ir_playbook_type", sa.String(64), nullable=True))
    op.add_column("change_requests", sa.Column("ir_template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ir_playbook_templates.id"), nullable=True))
    op.add_column("change_requests", sa.Column("step_results", JSONB, nullable=False, server_default="{}"))
    op.add_column("change_requests", sa.Column("output", JSONB, nullable=True))

    # --- new ChangeType values ---
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'isolate_host'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'lockdown_account'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'phishing_response'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'preserve_evidence'")

    # --- new UserRole value ---
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'ir_responder'")


def downgrade() -> None:
    op.drop_column("change_requests", "output")
    op.drop_column("change_requests", "step_results")
    op.drop_column("change_requests", "ir_template_id")
    op.drop_column("change_requests", "ir_playbook_type")
    op.drop_column("change_requests", "incident_response")
    op.drop_index("idx_forensic_bundles_collected_at", "forensic_bundles")
    op.drop_index("idx_forensic_bundles_asset_id", "forensic_bundles")
    op.drop_table("forensic_bundles")
    op.drop_table("ir_playbook_templates")
    # Note: Postgres enum values cannot be removed without dropping/recreating the type.
    # downgrade leaves the new enum values in place.
