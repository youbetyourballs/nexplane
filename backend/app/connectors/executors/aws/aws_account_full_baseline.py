# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS Account Full Baseline executor.

Thin orchestration over three sub-executors, run in sequence:
  1. aws_account_baseline_hardening  — IAM password policy, S3 block public, VPC flow logs
  2. aws_account_baseline_monitoring — CloudTrail, GuardDuty, SecurityHub, Config (all regions)
  3. iam_role_baseline               — NexplaneReadOnly, NexplaneSecurityAudit, NexplaneBreakGlass

Each step's execution_result is stored in the parent result under steps_completed for FILO rollback.

Rollback unwinds in reverse order (FILO):
  3. iam_role_baseline rollback
  2. aws_account_baseline_monitoring rollback
  1. aws_account_baseline_hardening rollback
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


# ---------------------------------------------------------------------------
# Sub-executor imports
# ---------------------------------------------------------------------------

from app.connectors.executors.aws import aws_account_baseline_hardening as _hardening
from app.connectors.executors.aws import aws_account_baseline_monitoring as _monitoring
from app.connectors.executors.aws import iam_role_baseline as _iam_roles


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Run hardening → monitoring → iam_role_baseline in sequence.

    Stops on the first failure; steps_completed records which sub-executors
    finished successfully (used by rollback to determine what to unwind).
    """
    steps_completed: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()

    # ---- Step 1: hardening ------------------------------------------------
    logger.info("aws_account_full_baseline: running hardening")
    try:
        hardening_result = await _hardening.execute(parameters, asset_ids, connector)
        steps_completed.append({
            "step": "aws_account_baseline_hardening",
            "result": hardening_result,
        })
        logger.info("aws_account_full_baseline: hardening complete")
    except Exception as exc:
        logger.error("aws_account_full_baseline: hardening failed: %s", exc)
        return {
            "status": "failed",
            "failed_step": "aws_account_baseline_hardening",
            "error": str(exc),
            "steps_completed": steps_completed,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    # ---- Step 2: monitoring -----------------------------------------------
    logger.info("aws_account_full_baseline: running monitoring")
    try:
        monitoring_result = await _monitoring.execute(parameters, asset_ids, connector)
        steps_completed.append({
            "step": "aws_account_baseline_monitoring",
            "result": monitoring_result,
        })
        logger.info("aws_account_full_baseline: monitoring complete")
    except Exception as exc:
        logger.error("aws_account_full_baseline: monitoring failed: %s", exc)
        return {
            "status": "failed",
            "failed_step": "aws_account_baseline_monitoring",
            "error": str(exc),
            "steps_completed": steps_completed,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    # ---- Step 3: IAM role baseline ----------------------------------------
    logger.info("aws_account_full_baseline: running iam_role_baseline")
    try:
        iam_result = await _iam_roles.execute(parameters, asset_ids, connector)
        steps_completed.append({
            "step": "iam_role_baseline",
            "result": iam_result,
        })
        logger.info("aws_account_full_baseline: iam_role_baseline complete")
    except Exception as exc:
        logger.error("aws_account_full_baseline: iam_role_baseline failed: %s", exc)
        return {
            "status": "failed",
            "failed_step": "iam_role_baseline",
            "error": str(exc),
            "steps_completed": steps_completed,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "status": "completed",
        "steps_completed": steps_completed,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Unwind sub-executors in FILO order using steps_completed from execution_result."""
    steps_completed = execution_result.get("steps_completed", [])
    rollback_results = []
    errors = []

    # Reverse order for FILO unwind
    for step in reversed(steps_completed):
        step_name = step["step"]
        step_result = step.get("result", {})

        logger.info("aws_account_full_baseline rollback: rolling back %s", step_name)
        try:
            if step_name == "aws_account_baseline_hardening":
                rb = await _hardening.rollback(parameters, step_result, connector)
            elif step_name == "aws_account_baseline_monitoring":
                rb = await _monitoring.rollback(parameters, step_result, connector)
            elif step_name == "iam_role_baseline":
                rb = await _iam_roles.rollback(parameters, step_result, connector)
            else:
                logger.warning("aws_account_full_baseline rollback: unknown step %s — skipping", step_name)
                rb = {"rolled_back": False, "skipped": True, "reason": "unknown step"}

            rollback_results.append({"step": step_name, "result": rb})
            logger.info("aws_account_full_baseline rollback: %s complete", step_name)
        except Exception as exc:
            logger.error("aws_account_full_baseline rollback: %s failed: %s", step_name, exc)
            errors.append({"step": step_name, "error": str(exc)})
            rollback_results.append({"step": step_name, "error": str(exc)})

    return {
        "rolled_back": len(errors) == 0,
        "rollback_results": rollback_results,
        "errors": errors,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
