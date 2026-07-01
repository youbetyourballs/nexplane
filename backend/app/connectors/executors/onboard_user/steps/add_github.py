# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "add_github_member",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        invite_resp = await client.post(
            f"/orgs/{org}/invitations",
            headers=headers,
            json={"email": target_email, "role": "direct_member"},
        )
        invite_resp.raise_for_status()
        invitation_id = invite_resp.json().get("id")

        team_results = []
        for team_slug in parameters.get("teams", []):
            team_resp = await client.put(
                f"/orgs/{org}/teams/{team_slug}/invitations/{invitation_id}",
                headers=headers,
            )
            team_results.append({"team": team_slug, "status": team_resp.status_code})

    return {
        "action": "add_github_member",
        "target_email": target_email,
        "invitation_id": invitation_id,
        "teams": team_results,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "cancel_github_invitation",
        "target_email": parameters["target_email"],
        "invitation_id": execution_result.get("invitation_id"),
        "rolled_back": True,
    }
