# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Pre-built runbook seed templates.

Call `load_seed_templates(db, org_id, system_user_id)` once during initial setup
or from an Alembic data migration. Idempotent: skips runbooks where is_seed=True
and name already exists for the org.
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runbook import Runbook, RunbookStep

SEED_TEMPLATES = [
    {
        "name": "Engineer Onboarding",
        "description": (
            "Provision a new engineer: create AD account, assign Okta groups, "
            "add to GitHub org, send welcome email."
        ),
        "tags": ["onboarding", "identity"],
        "steps": [
            {"step_number": 1, "name": "Create AD Account", "type": "change",
             "change_type": "create_ad_account", "on_failure": "abort",
             "parameters": {"username": "", "first_name": "", "last_name": "", "temp_password": "Welcome1!"}},
            {"step_number": 2, "name": "Assign Okta Groups", "type": "change",
             "change_type": "assign_okta_groups", "on_failure": "abort",
             "parameters": {"user_id": "", "group_ids": []}},
            {"step_number": 3, "name": "Add to GitHub Org", "type": "change",
             "change_type": "add_github_org_member", "on_failure": "continue",
             "parameters": {"username": "", "org": "NexplaneAI"}},
            {
                "step_number": 4, "name": "Manager Approval", "type": "human_checkpoint",
                "prompt": (
                    "Confirm the above accounts were provisioned correctly "
                    "and approve sending the welcome email."
                ),
                "required_role": "manager", "timeout_hours": 48, "on_timeout": "abort",
                "on_failure": "abort",
            },
            {"step_number": 5, "name": "Send Welcome Email", "type": "change",
             "change_type": "send_welcome_email", "on_failure": "continue"},
        ],
    },
    {
        "name": "Patch Campaign",
        "description": "Fleet health check, rolling patch, compliance verification.",
        "tags": ["patch", "compliance"],
        "steps": [
            {
                "step_number": 1, "name": "Fleet Health Check", "type": "change",
                "change_type": "check_fleet_health",
                "asset_selector": {"tags": [], "asset_ids": [], "environment": "prod"},
                "on_failure": "abort",
            },
            {
                "step_number": 2, "name": "Health Check Passed?", "type": "condition",
                "condition_expr": "steps[1]['exit_code'] == 0",
                "on_true_step": 3, "on_false_step": 99, "on_failure": "abort",
            },
            {
                "step_number": 3, "name": "Operator Approval", "type": "human_checkpoint",
                "prompt": "Fleet health check passed. Approve rolling patch to prod?",
                "required_role": "operator", "timeout_hours": 24, "on_timeout": "abort",
                "on_failure": "abort",
            },
            {
                "step_number": 4, "name": "Rolling Patch", "type": "change",
                "change_type": "patch_packages",
                "asset_selector": {"tags": [], "asset_ids": [], "environment": "prod"},
                "on_failure": "abort",
            },
            {
                "step_number": 5, "name": "Verify Compliance", "type": "change",
                "change_type": "check_compliance",
                "asset_selector": {"tags": [], "asset_ids": [], "environment": "prod"},
                "on_failure": "continue",
            },
        ],
    },
]


async def load_seed_templates(
    db: AsyncSession, org_id: uuid.UUID, system_user_id: uuid.UUID
) -> None:
    """Insert seed templates for an org. Idempotent."""
    for template in SEED_TEMPLATES:
        existing = await db.execute(
            select(Runbook).where(
                Runbook.organization_id == org_id,
                Runbook.name == template["name"],
                Runbook.is_seed.is_(True),
            )
        )
        if existing.scalar_one_or_none():
            continue  # Already seeded

        rb = Runbook(
            organization_id=org_id,
            name=template["name"],
            description=template["description"],
            tags=template["tags"],
            version=1,
            is_seed=True,
            auto_execute=True,
            created_by=system_user_id,
        )
        db.add(rb)
        await db.flush()

        for step_def in template["steps"]:
            step = RunbookStep(
                runbook_id=rb.id,
                step_number=step_def["step_number"],
                name=step_def["name"],
                type=step_def["type"],
                change_type=step_def.get("change_type"),
                parameters=step_def.get("parameters"),
                asset_selector=step_def.get("asset_selector"),
                condition_expr=step_def.get("condition_expr"),
                on_true_step=step_def.get("on_true_step"),
                on_false_step=step_def.get("on_false_step"),
                prompt=step_def.get("prompt"),
                required_role=step_def.get("required_role"),
                timeout_hours=step_def.get("timeout_hours"),
                on_timeout=step_def.get("on_timeout"),
                on_failure=step_def.get("on_failure", "abort"),
            )
            db.add(step)

    await db.commit()
