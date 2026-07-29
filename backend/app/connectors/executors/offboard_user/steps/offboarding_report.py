# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Generate structured offboarding report including discovery manifest and verification results."""
    target_email = parameters["target_email"]
    reason = parameters.get("reason", "unspecified")
    notify_manager = parameters.get("notify_manager", True)
    manager_email = parameters.get("manager_email")
    discovery_manifest = parameters.get("discovery_manifest", [])
    completed_at = datetime.now(timezone.utc).isoformat()

    report = {
        "report_type": "offboarding",
        "target_email": target_email,
        "reason": reason,
        "completed_at": completed_at,
        "manager_notified": False,
        "discovery_manifest": discovery_manifest,
        "systems_offboarded": [
            r["connector_type"] for r in discovery_manifest if r.get("found")
        ],
    }

    if notify_manager and manager_email:
        report["manager_notified"] = True
        report["manager_email"] = manager_email

    return {
        "action": "generate_offboarding_report",
        "report": report,
        "executed_at": completed_at,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "offboarding_report_rollback", "skipped": True, "reason": "reports are not reversible"}
