# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_gitlab_client, GitLabClient


async def execute(parameters, asset_ids, connector):
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_gitlab_client(connector)
    if not client and parameters.get("gitlab_url"):
        client = GitLabClient(url=parameters["gitlab_url"],
                              token=parameters.get("gitlab_token", ""))
    if not client:
        return {"action": "gitlab_suspend_user", "status": "skipped",
                "reason": "no_gitlab_credentials", "username": username}
    user = await client.get_user_by_username(username)
    if not user:
        return {"action": "gitlab_suspend_user", "status": "error",
                "reason": f"user_not_found: {username}"}
    user_id = user["id"]
    result = await client.block_user(user_id)
    return {
        **result,
        "action": "gitlab_suspend_user",
        "username": username,
        "suspended_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters, execution_result, connector):
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_gitlab_client(connector)
    if not client and parameters.get("gitlab_url"):
        client = GitLabClient(url=parameters["gitlab_url"],
                              token=parameters.get("gitlab_token", ""))
    if not client:
        return {"rolled_back": False, "reason": "no_gitlab_credentials"}
    user = await client.get_user_by_username(username)
    if not user:
        return {"rolled_back": False, "reason": f"user_not_found: {username}"}
    result = await client.unblock_user(user["id"])
    return {"rolled_back": result.get("success", False), **result}
