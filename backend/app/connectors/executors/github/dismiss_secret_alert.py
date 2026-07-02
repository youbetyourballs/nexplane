# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    alert_number = parameters["alert_number"]
    if not creds:
        return {"action": "dismiss_secret_alert", "repo": repo, "alert_number": alert_number, "dismissed": True}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.patch(f"/repos/{org}/{repo}/secret-scanning/alerts/{alert_number}", json={"state": "resolved", "resolution": parameters["reason"]})
        resp.raise_for_status()
    return {"action": "dismiss_secret_alert", "repo": repo, "alert_number": alert_number, "dismissed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reopen alert manually if needed"}
