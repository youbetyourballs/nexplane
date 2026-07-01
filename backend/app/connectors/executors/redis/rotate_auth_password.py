# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: rotate Redis requirepass (auth password)."""
from __future__ import annotations
import asyncio
import secrets
import string

from ._client import get_redis_client, RedisClient


def _generate_password(length: int = 32) -> str:
    # Redis passwords should avoid spaces and quotes
    alphabet = string.ascii_letters + string.digits + "!@#%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    client = await get_redis_client(connector)
    if not client:
        return {
            "action": "rotate_redis_password",
            "status": "skipped",
            "reason": "no_redis_credentials",
            "_asset_ids": [str(a) for a in asset_ids],
        }

    # Capture old password for rollback — run blocking call off the event loop
    # so the in-process forwarder can service the routed connection.
    try:
        old_password = await asyncio.to_thread(client.get_requirepass)
    except Exception:
        old_password = ""

    new_password = _generate_password()
    await asyncio.to_thread(client.set_requirepass, new_password)

    return {
        "action": "rotate_redis_password",
        "old_password": old_password,
        "new_password": new_password,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    old_password = execution_result.get("old_password", "")
    new_password = execution_result.get("new_password", "")

    if not new_password:
        return {"rolled_back": False, "reason": "new_password_not_in_result"}

    # Reconnect with new password to restore old one.
    # Route the rollback connection the same way as execute.
    creds = getattr(connector, "credentials", None) or {}

    # Build a temporary connector-like object that uses new_password so we can
    # route through tcp_endpoint again.  We call get_redis_client but override
    # the password afterward because we need the forwarder endpoint.
    rollback_client = await get_redis_client(connector)
    if rollback_client is None:
        # Fallback: construct directly (unrouted) — best-effort.
        host = creds.get("host") or creds.get("hostname", "")
        port = int(creds.get("port", 6379))
        rollback_client = RedisClient(host=host, port=port, password=new_password)
    else:
        # Override password so we auth with the new (post-rotate) credential.
        rollback_client.password = new_password

    try:
        await asyncio.to_thread(rollback_client.set_requirepass, old_password)
        return {"rolled_back": True}
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}
