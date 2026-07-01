# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
IaC Executor Service
====================
Handles two-phase execution for IaC change types:

  Phase 1 (PLAN):   Dispatch plan/check/diff agent command.
                    Store combined output in change_plan.blast_radius.impact_description.
                    Set change_request.status = "planned".
                    Return — the change request is now awaiting human review.

  Phase 2 (APPLY):  Called only after the change_request.status becomes "approved"
                    (the existing approval gate in execute_change_workflow handles this).
                    Dispatch apply/run/upgrade agent command.
                    Store output in the step result.

Rollback:           Dispatch rollback agent command with resources/revision from
                    the step metadata written during Phase 2.

This service is called from execute_change_workflow.py when change_type is one of
the IaC types. No new workflow infrastructure is needed.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.models.change_plan import ChangePlan

logger = logging.getLogger(__name__)

# The agent commands dispatched per change type, per phase.
_PLAN_COMMAND: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "terraform_plan",
    ChangeType.ansible_playbook: "ansible_check",
    ChangeType.helm_upgrade:     "helm_diff",
}

_APPLY_COMMAND: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "terraform_apply",
    ChangeType.ansible_playbook: "ansible_run",
    ChangeType.helm_upgrade:     "helm_upgrade",
}

_ROLLBACK_COMMAND: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "terraform_apply",  # executor rolls back via rollbacks map
    ChangeType.ansible_playbook: "ansible_run",
    ChangeType.helm_upgrade:     "helm_upgrade",
}

_PLAN_OUTPUT_KEY: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "plan_output",
    ChangeType.ansible_playbook: "check_output",
    ChangeType.helm_upgrade:     "diff_output",
}

_PANEL_TITLE: dict[ChangeType, str] = {
    ChangeType.terraform_apply:  "Terraform Plan",
    ChangeType.ansible_playbook: "Ansible Check Output",
    ChangeType.helm_upgrade:     "Helm Diff",
}

IAC_CHANGE_TYPES = frozenset(_PLAN_COMMAND.keys())


def is_iac_change_type(change_type: ChangeType) -> bool:
    return change_type in IAC_CHANGE_TYPES


async def run_plan_phase(
    db: AsyncSession,
    cr: ChangeRequest,
    agent_dispatch_fn,          # callable(agent_id, command, params) -> dict
    agent_id: str,
) -> dict[str, Any]:
    """
    Phase 1: run the plan/check/diff command, store output in blast_radius,
    and transition the change request to 'planned'.

    Returns the raw step result dict from the agent.
    Raises RuntimeError if the agent command fails.
    """
    ct = cr.change_type
    command = _PLAN_COMMAND[ct]
    params = _build_agent_params(cr)
    params["change_id"] = str(cr.id)

    logger.info("IaC plan phase: cr=%s command=%s agent=%s", cr.id, command, agent_id)
    result = await agent_dispatch_fn(agent_id, command, params)

    if result.get("status") != "completed":
        error = result.get("error", "unknown error")
        raise RuntimeError(f"IaC plan phase failed: {error}")

    # Extract plan output and write it to blast_radius.impact_description.
    plan_output = result.get("data", {}).get(_PLAN_OUTPUT_KEY[ct], "")
    plan = cr.change_plan
    if plan is None:
        plan = ChangePlan(change_request_id=cr.id)
        db.add(plan)

    existing_blast_radius = plan.blast_radius or {}
    existing_blast_radius["impact_description"] = plan_output
    existing_blast_radius["panel_title"] = _PANEL_TITLE[ct]
    plan.blast_radius = existing_blast_radius

    cr.status = ChangeRequestStatus.planned
    await db.flush()
    logger.info("IaC plan phase complete: cr=%s status=planned", cr.id)
    return result


async def run_apply_phase(
    db: AsyncSession,
    cr: ChangeRequest,
    agent_dispatch_fn,
    agent_id: str,
) -> dict[str, Any]:
    """
    Phase 2: run the apply/run/upgrade command after approval.
    Called by execute_change_workflow after the approval gate is passed.

    Returns the raw step result dict from the agent.
    Raises RuntimeError if the agent command fails.
    """
    ct = cr.change_type
    command = _APPLY_COMMAND[ct]
    params = _build_agent_params(cr)
    params["change_id"] = str(cr.id)

    # For dry_run mode, skip apply entirely.
    if params.get("dry_run"):
        logger.info("IaC apply skipped (dry_run=true): cr=%s", cr.id)
        return {"status": "completed", "data": {"skipped": "dry_run=true"}}

    logger.info("IaC apply phase: cr=%s command=%s agent=%s", cr.id, command, agent_id)
    result = await agent_dispatch_fn(agent_id, command, params)

    if result.get("status") != "completed":
        error = result.get("error", "unknown error")
        raise RuntimeError(f"IaC apply phase failed: {error}")

    logger.info("IaC apply phase complete: cr=%s", cr.id)
    return result


def _build_agent_params(cr: ChangeRequest) -> dict[str, Any]:
    """Extract typed parameters from desired_outcome for the agent command."""
    return dict(cr.desired_outcome or {})
