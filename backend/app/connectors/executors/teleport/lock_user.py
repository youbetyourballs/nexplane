# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_teleport_client, TeleportClient


async def execute(parameters, asset_ids, connector):
    username = parameters.get("username") or parameters.get("user_identifier", "")
    ttl = parameters.get("ttl", "1h")
    message = parameters.get("message", "Locked by Nexplane")
    client = get_teleport_client(connector)
    if not client and parameters.get("teleport_proxy_addr"):
        client = TeleportClient(
            proxy_addr=parameters["teleport_proxy_addr"],
            auth_token=parameters.get("teleport_auth_token"),
            tctl_bin=parameters.get("tctl_bin", "tctl"),
        )
    if not client:
        return {"action": "teleport_lock_user", "status": "skipped",
                "reason": "no_teleport_credentials", "username": username}
    try:
        result = client.lock_user(username, ttl=ttl, message=message)
    except Exception as e:
        return {"action": "teleport_lock_user", "status": "error",
                "error": str(e), "username": username}
    return {
        **result,
        "action": "teleport_lock_user",
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters, execution_result, connector):
    lock_id = execution_result.get("lock_id", "")
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_teleport_client(connector)
    if not client and parameters.get("teleport_proxy_addr"):
        client = TeleportClient(
            proxy_addr=parameters["teleport_proxy_addr"],
            auth_token=parameters.get("teleport_auth_token"),
            tctl_bin=parameters.get("tctl_bin", "tctl"),
        )
    if not client:
        return {"rolled_back": False, "reason": "no_teleport_credentials"}
    if not lock_id:
        # Try to find the lock by username
        try:
            lock_id = client._get_lock_id_for_user(username)
        except Exception:
            pass
    result = client.delete_lock(lock_id)
    return {"rolled_back": result.get("success", False), **result}
