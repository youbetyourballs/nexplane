"""
Onboard User change type definition and plan builder.

Phase ordering:
  Phase 1 — Create Active Directory account (must precede SSO)
  Phase 2 — Create Okta / Entra ID / Google Workspace accounts (parallel)
  Phase 3 — Add to GitHub org + invite to Slack (parallel, after identity accounts)
  Phase 4 — Onboarding report
"""

DEFINITION = {
    "name": "onboard_user",
    "display_name": "Onboard User",
    "description": (
        "Provision a new user across all connected identity systems. "
        "Steps are generated for each connector type present in the tenant."
    ),
    "payload_schema": "OnboardUserPayload",
    "rollback_supported": True,
    "parallel_steps": False,
}

_IDP_TYPES = ["okta", "entra_id", "google_workspace"]


async def build_plan(payload: dict, available_connectors: list[dict]) -> list[dict]:
    steps = []
    connector_by_type = {c["connector_type"]: c for c in available_connectors}

    # Phase 1: Active Directory (must be first — downstream SSO may depend on AD)
    if "active_directory" in connector_by_type:
        c = connector_by_type["active_directory"]
        steps.append({
            "name": "Create Active Directory account",
            "action": "create_active_directory_account",
            "connector_id": str(c["connector_id"]),
            "parameters": {
                "target_email": payload["target_email"],
                "display_name": payload["display_name"],
                "ou": payload.get("ad_ou"),
                "groups": payload.get("ad_groups", []),
            },
            "phase": 1,
            "rollback_action": "delete_active_directory_account",
            "status": "pending",
        })

    # Phase 2: Cloud IdPs (parallel)
    for ct in _IDP_TYPES:
        if ct in connector_by_type:
            c = connector_by_type[ct]
            steps.append({
                "name": f"Create {ct} account",
                "action": f"create_{ct}_account",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "display_name": payload["display_name"],
                    "department": payload["department"],
                    "manager_email": payload["manager_email"],
                    "groups": payload.get(f"{ct}_groups", []),
                    "org_unit": payload.get("google_org_unit"),
                },
                "phase": 2,
                "rollback_action": f"delete_{ct}_account",
                "status": "pending",
            })

    # Phase 3: Collaborative tools (parallel)
    if "github" in connector_by_type:
        c = connector_by_type["github"]
        steps.append({
            "name": "Add to GitHub org",
            "action": "add_github_member",
            "connector_id": str(c["connector_id"]),
            "parameters": {
                "target_email": payload["target_email"],
                "teams": payload.get("github_teams", []),
            },
            "phase": 3,
            "rollback_action": "remove_github_member",
            "status": "pending",
        })

    if "slack" in connector_by_type:
        c = connector_by_type["slack"]
        steps.append({
            "name": "Invite to Slack workspace",
            "action": "invite_slack_member",
            "connector_id": str(c["connector_id"]),
            "parameters": {
                "target_email": payload["target_email"],
                "channels": payload.get("slack_channels", []),
            },
            "phase": 3,
            "rollback_action": "deactivate_slack_member",
            "status": "pending",
        })

    # Phase 4: Report
    steps.append({
        "name": "Generate onboarding report",
        "action": "generate_onboarding_report",
        "connector_id": None,
        "parameters": {
            "target_email": payload["target_email"],
            "display_name": payload["display_name"],
            "manager_email": payload["manager_email"],
        },
        "phase": 4,
        "rollback_action": None,
        "status": "pending",
    })

    return steps
