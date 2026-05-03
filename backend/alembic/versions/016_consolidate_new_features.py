"""Consolidate all 016+ feature migrations into a single linear migration.

Merged from:
  - 016_add_runbooks.py
  - 016_add_compliance_baselines.py
  - 016_add_ir_tables.py
  - 016_add_access_reviews.py
  - 016_add_secret_versions.py
  - 016_add_access_review_schedules.py
  - 016_add_fleet_operations.py
  - 016_vulnerability_remediation.py
  - 017_add_change_freeze_windows.py

Revision ID: ad8f4c9e1b2d
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
import uuid

revision = 'ad8f4c9e1b2d'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # RUNBOOKS
    # -------------------------------------------------------------------------
    op.create_table(
        "runbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("is_seed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_runbooks_organization_id", "runbooks", ["organization_id"])

    op.create_table(
        "runbook_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_step_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbook_steps.id"), nullable=True),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("change_type", sa.String(128), nullable=True),
        sa.Column("parameters", postgresql.JSONB, nullable=True),
        sa.Column("asset_selector", postgresql.JSONB, nullable=True),
        sa.Column("condition_expr", sa.Text, nullable=True),
        sa.Column("on_true_step", sa.Integer, nullable=True),
        sa.Column("on_false_step", sa.Integer, nullable=True),
        sa.Column("prompt", sa.Text, nullable=True),
        sa.Column("required_role", sa.String(64), nullable=True),
        sa.Column("timeout_hours", sa.Integer, nullable=True),
        sa.Column("on_timeout", sa.String(32), nullable=True),
        sa.Column("on_failure", sa.String(32), nullable=False, server_default="abort"),
    )
    op.create_index("ix_runbook_steps_runbook_id", "runbook_steps", ["runbook_id"])

    op.create_table(
        "runbook_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id"), nullable=False),
        sa.Column("runbook_version", sa.Integer, nullable=False),
        sa.Column("runbook_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("current_step", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_runbook_executions_runbook_id", "runbook_executions", ["runbook_id"])
    op.create_index("ix_runbook_executions_status", "runbook_executions", ["status"])

    op.create_table(
        "runbook_step_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_request_ids", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_runbook_step_results_execution_id", "runbook_step_results", ["execution_id"])

    # -------------------------------------------------------------------------
    # COMPLIANCE BASELINES
    # -------------------------------------------------------------------------
    op.create_table(
        'compliance_baselines',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scope_type', sa.Text(), nullable=False),
        sa.Column('scope_value', sa.Text(), nullable=False),
        sa.Column('cis_level', sa.Integer(), nullable=False),
        sa.Column('os_family', sa.Text(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('history', postgresql.ARRAY(postgresql.JSONB()), nullable=False,
                  server_default='{}'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('auto_execute', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("scope_type IN ('asset', 'tag')", name='ck_baseline_scope_type'),
        sa.CheckConstraint('cis_level IN (1, 2)', name='ck_baseline_cis_level'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_compliance_baselines_scope', 'compliance_baselines', ['scope_type', 'scope_value'])
    op.create_index('idx_compliance_baselines_org', 'compliance_baselines', ['organization_id'])

    # -------------------------------------------------------------------------
    # IR PLAYBOOK TEMPLATES
    # -------------------------------------------------------------------------
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
    op.execute("""
        INSERT INTO ir_playbook_templates (playbook_type, display_name, description, default_parameters) VALUES
        ('isolate_host',       'Host Isolation',        'Isolate a compromised host via agent-side firewall rules',                   '{"management_cidr": "10.0.0.0/8"}'),
        ('lockdown_account',   'Account Lockdown',      'Disable a user across all identity connectors simultaneously',              '{}'),
        ('phishing_response',  'Phishing Response',     'Block sender domain, reset passwords, revoke sessions, re-enroll MFA',     '{}'),
        ('preserve_evidence',  'Evidence Preservation', 'Collect forensic artifacts from host before remediation',                  '{"include_memory_dump": false}')
    """)

    # -------------------------------------------------------------------------
    # FORENSIC BUNDLES
    # -------------------------------------------------------------------------
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

    # -------------------------------------------------------------------------
    # ACCESS REVIEWS
    # -------------------------------------------------------------------------
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

    # -------------------------------------------------------------------------
    # ACCESS REVIEW SCHEDULES
    # -------------------------------------------------------------------------
    op.create_table(
        'access_review_schedules',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('frequency_days', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(255), nullable=False, server_default='all_users'),
        sa.Column('reviewer_assignment_rule', sa.String(100), nullable=False, server_default='direct_manager'),
        sa.Column('last_review_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # -------------------------------------------------------------------------
    # SECRET VERSIONS
    # -------------------------------------------------------------------------
    op.create_table(
        "secret_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False),
        sa.Column(
            "secret_id",
            UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("ciphertext", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum("current", "superseded", name="secret_version_status"),
            nullable=False,
            default="current",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_secret_versions_secret_id_status", "secret_versions", ["secret_id", "status"])

    # -------------------------------------------------------------------------
    # VULNERABILITY FINDINGS
    # -------------------------------------------------------------------------
    op.create_table(
        "vulnerability_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("scanner", sa.String, nullable=False),
        sa.Column("scanner_finding_id", sa.String, nullable=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("finding_type", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False),
        sa.Column("cve_id", sa.String, nullable=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("remediation_hint", sa.Text, nullable=True),
        sa.Column("affected_package", sa.String, nullable=True),
        sa.Column("affected_version", sa.String, nullable=True),
        sa.Column("fixed_version", sa.String, nullable=True),
        sa.Column("resource_type", sa.String, nullable=True),
        sa.Column("resource_id", sa.String, nullable=True),
        sa.Column("target_ip", sa.String, nullable=True),
        sa.Column("target_hostname", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.UniqueConstraint("organization_id", "scanner", "scanner_finding_id", name="uq_finding_scanner_id"),
    )
    op.create_index("ix_vf_org_id",   "vulnerability_findings", ["organization_id"])
    op.create_index("ix_vf_asset_id", "vulnerability_findings", ["asset_id"])
    op.create_index("ix_vf_severity", "vulnerability_findings", ["severity"])
    op.create_index("ix_vf_status",   "vulnerability_findings", ["status"])

    op.create_table(
        "remediation_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("match_scanner", sa.String, nullable=True),
        sa.Column("match_finding_type", sa.String, nullable=True),
        sa.Column("match_severity", ARRAY(sa.String), nullable=True),
        sa.Column("match_resource_type", sa.String, nullable=True),
        sa.Column("action_type", sa.String, nullable=False),
        sa.Column("action_params", JSONB, nullable=True),
        sa.Column("approval_level", sa.String, nullable=False, server_default="require_approval"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rp_org_id", "remediation_policies", ["organization_id"])

    op.create_table(
        "remediation_slas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("finding_id", UUID(as_uuid=True), sa.ForeignKey("vulnerability_findings.id"), nullable=False, unique=True),
        sa.Column("severity", sa.String, nullable=False),
        sa.Column("sla_hours", sa.Integer, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breached", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("breach_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_rs_org_due", "remediation_slas", ["organization_id", "due_at"])

    # -------------------------------------------------------------------------
    # MAINTENANCE WINDOW (fleet operations)
    # -------------------------------------------------------------------------
    op.create_table(
        "maintenance_window",
        sa.Column("id",               sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name",             sa.String(255), nullable=False),
        sa.Column("cron_schedule",    sa.String(100), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("applies_to_tags",  postgresql.JSONB, nullable=True),
        sa.Column("enabled",          sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_maintenance_window_org", "maintenance_window", ["organization_id"])

    # -------------------------------------------------------------------------
    # CHANGE FREEZE WINDOWS
    # -------------------------------------------------------------------------
    op.create_table(
        'change_freeze_windows',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('emergency_bypass_role', sa.Text(), nullable=False,
                  server_default='emergency_bypass'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint('end_at > start_at', name='ck_freeze_window_dates'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_freeze_windows_active', 'change_freeze_windows', ['start_at', 'end_at'])

    # -------------------------------------------------------------------------
    # change_requests — new columns
    # (deduplicated: 'source' appears in both runbooks and vuln remediation;
    #  keep one. 'step_results' from IR, 'step_metadata' from fleet ops,
    #  'runbook_execution_id' from runbooks are all distinct.)
    # -------------------------------------------------------------------------
    op.add_column("change_requests", sa.Column("source", sa.String(32), nullable=True))
    op.add_column("change_requests", sa.Column(
        "runbook_execution_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.add_column("change_requests", sa.Column("incident_response", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("change_requests", sa.Column("ir_playbook_type", sa.String(64), nullable=True))
    op.add_column("change_requests", sa.Column("ir_template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ir_playbook_templates.id"), nullable=True))
    op.add_column("change_requests", sa.Column("step_results", JSONB, nullable=False, server_default="{}"))
    op.add_column("change_requests", sa.Column("output", JSONB, nullable=True))
    op.add_column("change_requests", sa.Column("step_metadata", postgresql.JSONB, nullable=True))
    op.add_column("change_requests", sa.Column("finding_id", UUID(as_uuid=True),
                  sa.ForeignKey("vulnerability_findings.id"), nullable=True))

    # -------------------------------------------------------------------------
    # ENUM EXTENSIONS
    # -------------------------------------------------------------------------
    # change_type new values (IR)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'isolate_host'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'lockdown_account'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'phishing_response'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'preserve_evidence'")
    # change_type new values (fleet ops)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rolling_restart'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'canary_config_push'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'distribute_file'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'fleet_health_check'")
    # change_request_status new values (fleet ops)
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'queued_for_maintenance'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'preflight_running'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'preflight_failed'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'batch_running'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'batch_aborted'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'completed_with_errors'")
    # userrole new value (IR)
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'ir_responder'")
    # change_type new values (compliance)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'enforce_cis_benchmark'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'collect_evidence'")
    # change_type new values (IaC)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'terraform_apply'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ansible_playbook'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'helm_upgrade'")
    # change_type new values (patch management)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'patch_packages'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'patch_campaign'")
    # change_type new values (identity lifecycle)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'offboard_user'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'onboard_user'")
    # change_type new values (secret rotation)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rotate_db_credentials'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rotate_ssh_keys'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rotate_api_key'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rotate_service_account'")
    # change_type new values (database admin)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'provision_db_user'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'deprovision_db_user'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'db_permission_change'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_db_audit'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'promote_db_replica'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'db_connection_config'")
    # change_type new values (backup & recovery)
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'create_backup'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'verify_backup'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'restore_files'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'dr_failover'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'scheduled_reboot'")


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # change_requests columns — reverse order of upgrade
    # -------------------------------------------------------------------------
    op.drop_column("change_requests", "finding_id")
    op.drop_column("change_requests", "step_metadata")
    op.drop_column("change_requests", "output")
    op.drop_column("change_requests", "step_results")
    op.drop_column("change_requests", "ir_template_id")
    op.drop_column("change_requests", "ir_playbook_type")
    op.drop_column("change_requests", "incident_response")
    op.drop_column("change_requests", "runbook_execution_id")
    op.drop_column("change_requests", "source")

    # -------------------------------------------------------------------------
    # CHANGE FREEZE WINDOWS
    # -------------------------------------------------------------------------
    op.drop_index('idx_freeze_windows_active', table_name='change_freeze_windows')
    op.drop_table('change_freeze_windows')

    # -------------------------------------------------------------------------
    # MAINTENANCE WINDOW
    # -------------------------------------------------------------------------
    op.drop_index("ix_maintenance_window_org", table_name="maintenance_window")
    op.drop_table("maintenance_window")
    # Note: PostgreSQL does not support removing enum values; fleet/IR enum values left in place.

    # -------------------------------------------------------------------------
    # REMEDIATION SLAS / POLICIES / VULNERABILITY FINDINGS
    # -------------------------------------------------------------------------
    op.drop_table("remediation_slas")
    op.drop_table("remediation_policies")
    op.drop_index("ix_vf_status",   table_name="vulnerability_findings")
    op.drop_index("ix_vf_severity", table_name="vulnerability_findings")
    op.drop_index("ix_vf_asset_id", table_name="vulnerability_findings")
    op.drop_index("ix_vf_org_id",   table_name="vulnerability_findings")
    op.drop_table("vulnerability_findings")

    # -------------------------------------------------------------------------
    # SECRET VERSIONS
    # -------------------------------------------------------------------------
    op.drop_index("ix_secret_versions_secret_id_status", table_name="secret_versions")
    op.drop_table("secret_versions")
    op.execute("DROP TYPE IF EXISTS secret_version_status")

    # -------------------------------------------------------------------------
    # ACCESS REVIEW SCHEDULES
    # -------------------------------------------------------------------------
    op.drop_table('access_review_schedules')

    # -------------------------------------------------------------------------
    # ACCESS REVIEWS
    # -------------------------------------------------------------------------
    op.drop_index("ix_access_reviews_created_by", table_name="access_reviews")
    op.drop_index("ix_access_reviews_status", table_name="access_reviews")
    op.drop_table("access_review_change_requests")
    op.drop_table("access_reviews")

    # -------------------------------------------------------------------------
    # FORENSIC BUNDLES
    # -------------------------------------------------------------------------
    op.drop_index("idx_forensic_bundles_collected_at", table_name="forensic_bundles")
    op.drop_index("idx_forensic_bundles_asset_id", table_name="forensic_bundles")
    op.drop_table("forensic_bundles")

    # -------------------------------------------------------------------------
    # IR PLAYBOOK TEMPLATES
    # -------------------------------------------------------------------------
    op.drop_table("ir_playbook_templates")

    # -------------------------------------------------------------------------
    # COMPLIANCE BASELINES
    # -------------------------------------------------------------------------
    op.drop_index('idx_compliance_baselines_org', table_name='compliance_baselines')
    op.drop_index('idx_compliance_baselines_scope', table_name='compliance_baselines')
    op.drop_table('compliance_baselines')

    # -------------------------------------------------------------------------
    # RUNBOOKS
    # -------------------------------------------------------------------------
    op.drop_index("ix_runbook_step_results_execution_id", table_name="runbook_step_results")
    op.drop_table("runbook_step_results")
    op.drop_index("ix_runbook_executions_status", table_name="runbook_executions")
    op.drop_index("ix_runbook_executions_runbook_id", table_name="runbook_executions")
    op.drop_table("runbook_executions")
    op.drop_index("ix_runbook_steps_runbook_id", table_name="runbook_steps")
    op.drop_table("runbook_steps")
    op.drop_index("ix_runbooks_organization_id", table_name="runbooks")
    op.drop_table("runbooks")
