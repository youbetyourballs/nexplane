# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: rotate a MongoDB user's password."""
from __future__ import annotations
import asyncio
import secrets
import string

from ._client import get_mongo_client


def _generate_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    username = parameters.get("username", "")
    if not username:
        raise ValueError("username is required")
    db_name = parameters.get("db_name", "admin")

    client = await get_mongo_client(connector)
    if not client:
        return {
            "action": "rotate_mongodb_password",
            "status": "skipped",
            "reason": "no_mongodb_credentials",
            "username": username,
            "_asset_ids": [str(a) for a in asset_ids],
        }

    new_password = _generate_password()
    # Run blocking pymongo call off the event loop so the in-process forwarder
    # can accept the routed connection (avoids loop deadlock).
    await asyncio.to_thread(client.rotate_user_password, username, new_password, db_name)

    return {
        "action": "rotate_mongodb_password",
        "username": username,
        "db_name": db_name,
        "new_password": new_password,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    username = parameters.get("username", "")
    db_name = parameters.get("db_name", "admin")
    old_password = parameters.get("old_password") or execution_result.get("old_password")

    if not old_password:
        return {
            "rolled_back": False,
            "reason": "old_password_not_available",
            "username": username,
        }

    # After rotation the connector creds still hold the admin credentials
    client = await get_mongo_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_mongodb_credentials"}

    await asyncio.to_thread(client.rotate_user_password, username, old_password, db_name)
    return {"rolled_back": True, "username": username}
