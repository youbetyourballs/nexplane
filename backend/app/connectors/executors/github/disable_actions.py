# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "disable_actions", "repo": repo, "enabled": False, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        current = httpx.get(f"https://api.github.com/repos/{org}/{repo}/actions/permissions", headers=headers)
        was_enabled = current.json().get("enabled", True) if current.status_code == 200 else True
        httpx.put(
            f"https://api.github.com/repos/{org}/{repo}/actions/permissions",
            headers=headers,
            json={"enabled": False},
        ).raise_for_status()
        return was_enabled

    was_enabled = await loop.run_in_executor(None, _call)
    return {
        "action": "disable_actions",
        "repo": repo,
        "enabled": False,
        "rollback_data": {"was_enabled": was_enabled},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.github.enable_actions import execute as enable
    return await enable(parameters, [], connector)
