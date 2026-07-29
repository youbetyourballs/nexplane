# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Offboard User change type definition and plan builder.

Phase ordering:
  Phase 1 — Session revocation (Okta, Entra ID, Google Workspace) — parallel
  Phase 2 — Account disable (AD, Okta, Entra ID, Google Workspace) — parallel
  Phase 3 — Workspace/org removal (GitHub, Slack) — parallel
  Phase 4 — Endpoint isolation (CrowdStrike) — sequential, opt-in only
  Phase 5 — Verification (one step per connector that ran in phases 1–4)
  Phase 6 — Offboarding report — always last
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
# Connector types that have verification support in verify_disabled.py
_VERIFY_TYPES = {"active_directory", "okta", "entra_id", "google_workspace", "github", "slack", "crowdstrike"}


async def build_plan(payload: dict, resolved_connectors: list[dict]) -> list[dict]:
    """
    Returns a list of step dicts (not Pydantic models, for SQLite test compatibility).
    Each dict: {name, action, connector_id, parameters, phase, rollback_action, status}

    resolved_connectors items must include:
      connector_id, connector_type, asset_id, account_identifier (from discovery)
    """
    steps = []
    # Track which connectors ran action steps (for phase 5 verify generation)
    _action_connectors: list[dict] = []

    # Phase 1: session revocation
    for c in resolved_connectors:
        if c["connector_type"] in _SESSION_REVOKE_TYPES:
            steps.append({
                "name": f"Revoke {c['connector_type']} sessions",
                "action_id": f"revoke_{c['connector_type']}_sessions",
                "connector_type": c["connector_type"],
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]) if c.get("asset_id") else None,
                },
                "phase": 1,
                "rollback_action_id": None,
                "status": "pending",
            })

    # Phase 2: account disable
    for c in resolved_connectors:
        if c["connector_type"] in _ACCOUNT_DISABLE_TYPES:
            steps.append({
                "name": f"Disable {c['connector_type']} account",
                "action_id": f"disable_{c['connector_type']}_account",
                "connector_type": c["connector_type"],
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]) if c.get("asset_id") else None,
                },
                "phase": 2,
                "rollback_action_id": f"enable_{c['connector_type']}_account",
                "status": "pending",
            })
            _action_connectors.append(c)

    # Phase 3: removal from collaborative tools
    for c in resolved_connectors:
        if c["connector_type"] in _REMOVAL_TYPES:
            steps.append({
                "name": f"Remove from {c['connector_type']}",
                "action_id": f"remove_{c['connector_type']}_member",
                "connector_type": c["connector_type"],
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]) if c.get("asset_id") else None,
                },
                "phase": 3,
                "rollback_action_id": f"reinstate_{c['connector_type']}_member",
                "status": "pending",
            })
            _action_connectors.append(c)

    # Phase 4: CrowdStrike isolation (opt-in)
    if payload.get("isolate_endpoints"):
        for c in resolved_connectors:
            if c["connector_type"] == "crowdstrike":
                steps.append({
                    "name": "Isolate CrowdStrike-managed endpoints",
                    "action_id": "isolate_crowdstrike_endpoints",
                    "connector_type": "crowdstrike",
                    "connector_id": str(c["connector_id"]),
                    "parameters": {
                        "target_email": payload["target_email"],
                    },
                    "phase": 4,
                    "rollback_action_id": "lift_crowdstrike_isolation",
                    "status": "pending",
                })
                _action_connectors.append(c)

    # Phase 5: verification (one step per connector that had an action step)
    seen_verify = set()
    for c in _action_connectors:
        ct = c["connector_type"]
        conn_id = str(c["connector_id"])
        if conn_id in seen_verify or ct not in _VERIFY_TYPES:
            continue
        seen_verify.add(conn_id)
        steps.append({
            "name": f"Verify {ct} account disabled",
            "action_id": f"verify_{ct}_disabled",
            "connector_type": ct,
            "connector_id": conn_id,
            "parameters": {
                "target_email": payload["target_email"],
                "connector_type": ct,
                "account_identifier": c.get("account_identifier"),
            },
            "phase": 5,
            "rollback_action_id": None,
            "status": "pending",
        })

    # Phase 6: report (always last)
    steps.append({
        "name": "Generate offboarding report",
        "action_id": "generate_offboarding_report",
        "connector_type": "offboard_user",
        "connector_id": None,
        "parameters": {
            "target_email": payload["target_email"],
            "reason": payload.get("reason"),
            "notify_manager": payload.get("notify_manager", True),
            "manager_email": payload.get("manager_email"),
            "discovery_manifest": payload.get("_discovery_manifest", []),
        },
        "phase": 6,
        "rollback_action_id": None,
        "status": "pending",
    })

    # Assign step_number (1-indexed) so activities.py can reference them by number
    for i, step in enumerate(steps, start=1):
        step["step_number"] = i

    return steps
