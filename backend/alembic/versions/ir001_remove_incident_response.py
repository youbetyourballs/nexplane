# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Remove incident response tables and enum values.

IR workflow removed from product — isolate_host, lockdown_account,
phishing_response, preserve_evidence change types and associated tables dropped.

Revision ID: ir001
Revises: tunnel002
Create Date: 2026-07-05
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "ir001"
down_revision: Union[str, None] = "tunnel002"
branch_labels = None
depends_on = None

_IR_CHANGE_TYPES = {"isolate_host", "lockdown_account", "phishing_response", "preserve_evidence"}


def _recreate_enum_without(conn, enum_name: str, table: str, column: str, remove_values: set) -> None:
    """Recreate a Postgres enum type removing the specified values."""
    # Fetch current values
    rows = conn.execute(text(
        "SELECT enumlabel FROM pg_enum "
        "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "WHERE pg_type.typname = :name ORDER BY enumsortorder"
    ), {"name": enum_name}).fetchall()
    kept = [r[0] for r in rows if r[0] not in remove_values]

    # Delete any rows using the removed values so the USING cast succeeds.
    # Use CASCADE via DELETE on parent so FK-referencing child rows are cleaned up first.
    if table and column:
        placeholders = ", ".join(f"'{v}'" for v in remove_values)
        if table == "change_requests":
            # Null or delete FK references before deleting change_requests rows.
            cr_ids_sql = f"SELECT id FROM change_requests WHERE change_type::text IN ({placeholders})"
            # Hard-delete dependents that own their CR reference
            for dep_table, col in (
                ("change_plans", "change_request_id"),
                ("execution_runs", "change_request_id"),
                ("audit_events", "change_request_id"),
                ("approvals", "change_request_id"),
                ("agent_jobs", "change_request_id"),
                ("project_change_requests", "change_request_id"),
                ("project_rollback_steps", "change_request_id"),
                ("vulnerability_findings", "change_request_id"),
                ("finding_change_requests", "cr_id"),
                ("security_policy_baselines", "cr_id"),
                ("security_policy_soak_sessions", "cr_id"),
            ):
                conn.execute(text(
                    f"DELETE FROM {dep_table} WHERE {col} IN ({cr_ids_sql})"
                ))
            # Nullable FKs — set to NULL rather than deleting the parent row
            for dep_table, col in (
                ("backup_targets", "last_successful_backup_cr_id"),
                ("recurring_jobs", "last_cr_id"),
                ("project_rollbacks", "triggered_by_cr_id"),
                ("project_rollback_steps", "backup_cr_id"),
                ("recovery_tokens", "restore_cr_id"),
                ("change_requests", "parent_change_request_id"),
            ):
                conn.execute(text(
                    f"UPDATE {dep_table} SET {col} = NULL WHERE {col} IN ({cr_ids_sql})"
                ))
        conn.execute(text(f"DELETE FROM {table} WHERE {column}::text IN ({placeholders})"))

    old_name = f"{enum_name}_old_ir001"
    conn.execute(text(f"ALTER TYPE {enum_name} RENAME TO {old_name}"))

    values_sql = ", ".join(f"'{v}'" for v in kept)
    conn.execute(text(f"CREATE TYPE {enum_name} AS ENUM ({values_sql})"))

    conn.execute(text(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {enum_name} "
        f"USING {column}::text::{enum_name}"
    ))
    conn.execute(text(f"DROP TYPE {old_name}"))


def upgrade() -> None:
    conn = op.get_bind()

    # Drop IR-specific tables (may already be absent from prior migrations)
    conn.execute(text("DROP TABLE IF EXISTS forensic_bundles CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS ir_playbook_templates CASCADE"))
    # Remove forensic_bundles from the dep-delete list since it may not exist
    # (handled by DROP IF EXISTS above)

    # Remove IR change types from changetype enum
    _recreate_enum_without(conn, "changetype", "change_requests", "change_type", _IR_CHANGE_TYPES)

    # Remove ir_responder from userrole enum
    _recreate_enum_without(conn, "userrole", "users", "role", {"ir_responder"})


def downgrade() -> None:
    raise NotImplementedError("IR removal is a one-way migration; downgrade not supported.")
