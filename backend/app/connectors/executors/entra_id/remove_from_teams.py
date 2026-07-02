# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "remove_from_teams",
            "user_id": user_id,
            "teams_removed": [],
            "mock": True,
        }

    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    graph = "https://graph.microsoft.com/v1.0"

    async with await get_graph_client(token, connector) as client:
        resp = await client.get(f"/users/{user_id}/joinedTeams")
        resp.raise_for_status()
        teams = resp.json().get("value", [])

        removed = []
        errors = []
        for team in teams:
            team_id = team["id"]
            team_name = team.get("displayName", team_id)
            snapshot = {"teamId": team_id, "teamName": team_name}
            if dry_run:
                removed.append(snapshot)
                continue
            del_resp = await client.delete(f"/groups/{team_id}/members/{user_id}/$ref")
            if del_resp.status_code in (204, 404):
                removed.append(snapshot)
            else:
                errors.append({"teamId": team_id, "status": del_resp.status_code})

    return {
        "action": "remove_from_teams",
        "user_id": user_id,
        "dry_run": dry_run,
        "teams_removed": removed,
        "errors": errors,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    teams = execution_result.get("teams_removed", [])

    if not creds:
        return {"action": "restore_team_memberships", "user_id": user_id, "mock": True}

    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    graph = "https://graph.microsoft.com/v1.0"

    restored = []
    errors = []
    async with await get_graph_client(token, connector) as client:
        for team in teams:
            team_id = team["teamId"]
            body = {"@odata.id": f"{graph}/directoryObjects/{user_id}"}
            resp = await client.post(f"/groups/{team_id}/members/$ref", json=body)
            if resp.status_code in (204, 201):
                restored.append(team_id)
            else:
                errors.append({"teamId": team_id, "status": resp.status_code})

    return {"action": "restore_team_memberships", "user_id": user_id, "teams_restored": restored, "errors": errors}
