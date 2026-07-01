# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Offboard User change type definition and plan builder.

Phase ordering:
  Phase 1 — Session revocation (Okta, Entra ID, Google Workspace) — parallel
  Phase 2 — Account disable (AD, Okta, Entra ID, Google Workspace) — parallel
  Phase 3 — Workspace/org removal (GitHub, Slack) — parallel
  Phase 4 — Endpoint isolation (CrowdStrike) — sequential, opt-in only
  Phase 5 — Offboarding report — always last
"""

DEFINITION = {
    "name": "offboard_user",
    "display_name": "Offboard User",
    "description": (
        "Disable a user across all connected identity systems in a single "
        "coordinated change request. One step is generated per connector "
        "that has an account for the target email address."
    ),
    "payload_schema": "OffboardUserPayload",
    "rollback_supported": True,
    "parallel_steps": False,
}

_SESSION_REVOKE_TYPES = {"okta", "entra_id", "google_workspace"}
_ACCOUNT_DISABLE_TYPES = {"active_directory", "okta", "entra_id", "google_workspace"}
_REMOVAL_TYPES = {"github", "slack"}


async def build_plan(payload: dict, resolved_connectors: list[dict]) -> list[dict]:
    """
    Returns a list of step dicts (not Pydantic models, for SQLite test compatibility).
    Each dict: {name, action, connector_id, parameters, phase, rollback_action}
    """
    steps = []

    # Phase 1: session revocation
    for c in resolved_connectors:
        if c["connector_type"] in _SESSION_REVOKE_TYPES:
            steps.append({
                "name": f"Revoke {c['connector_type']} sessions",
                "action": f"revoke_{c['connector_type']}_sessions",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]),
                },
                "phase": 1,
                "rollback_action": None,
                "status": "pending",
            })

    # Phase 2: account disable
    for c in resolved_connectors:
        if c["connector_type"] in _ACCOUNT_DISABLE_TYPES:
            steps.append({
                "name": f"Disable {c['connector_type']} account",
                "action": f"disable_{c['connector_type']}_account",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]),
                },
                "phase": 2,
                "rollback_action": f"enable_{c['connector_type']}_account",
                "status": "pending",
            })

    # Phase 3: removal from collaborative tools
    for c in resolved_connectors:
        if c["connector_type"] in _REMOVAL_TYPES:
            steps.append({
                "name": f"Remove from {c['connector_type']}",
                "action": f"remove_{c['connector_type']}_member",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]),
                },
                "phase": 3,
                "rollback_action": f"reinstate_{c['connector_type']}_member",
                "status": "pending",
            })

    # Phase 4: CrowdStrike isolation (opt-in)
    if payload.get("isolate_endpoints"):
        for c in resolved_connectors:
            if c["connector_type"] == "crowdstrike":
                steps.append({
                    "name": "Isolate CrowdStrike-managed endpoints",
                    "action": "isolate_crowdstrike_endpoints",
                    "connector_id": str(c["connector_id"]),
                    "parameters": {
                        "target_email": payload["target_email"],
                    },
                    "phase": 4,
                    "rollback_action": "lift_crowdstrike_isolation",
                    "status": "pending",
                })

    # Phase 5: report (always last)
    steps.append({
        "name": "Generate offboarding report",
        "action": "generate_offboarding_report",
        "connector_id": None,
        "parameters": {
            "target_email": payload["target_email"],
            "reason": payload.get("reason"),
            "notify_manager": payload.get("notify_manager", True),
            "manager_email": payload.get("manager_email"),
        },
        "phase": 5,
        "rollback_action": None,
        "status": "pending",
    })

    return steps
