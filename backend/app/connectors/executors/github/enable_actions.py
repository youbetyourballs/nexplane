# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "enable_actions", "repo": repo, "enabled": True, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        httpx.put(
            f"https://api.github.com/repos/{org}/{repo}/actions/permissions",
            headers=headers,
            json={"enabled": True},
        ).raise_for_status()

    await loop.run_in_executor(None, _call)
    return {
        "action": "enable_actions",
        "repo": repo,
        "enabled": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.github.disable_actions import execute as disable
    return await disable(parameters, [], connector)
