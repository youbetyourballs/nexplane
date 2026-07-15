# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {"action": "remove_org_member", "username": username, "removed": True, "mock": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        org = g.get_organization(creds["org"])
        user = g.get_user(username)
        teams = [t.name for t in org.get_teams() if t.has_in_members(user)]
        try:
            membership = org.get_membership(user)
            org_role = membership.role
        except Exception:
            org_role = "member"
        rollback_data = {"username": username, "org_role": org_role, "teams": teams}
        if not dry_run:
            org.remove_from_members(user)
        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "remove_org_member",
        "username": username,
        "dry_run": dry_run,
        "removed": not dry_run,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Re-invitation must be accepted by the user. Send invitation via GitHub org settings.",
        "rollback_data": execution_result.get("rollback_data"),
    }
