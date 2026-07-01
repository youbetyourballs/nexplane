# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: rotate a PostgreSQL user's password."""
from __future__ import annotations
import secrets
import string

from ._client import get_postgres_client


def _generate_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    username = parameters.get("username", "")
    if not username:
        raise ValueError("username is required")

    client = get_postgres_client(connector)
    if not client:
        return {
            "action": "rotate_postgres_password",
            "status": "skipped",
            "reason": "no_postgres_credentials",
            "username": username,
            "_asset_ids": [str(a) for a in asset_ids],
        }

    new_password = _generate_password()
    client.alter_user_password(username, new_password)

    return {
        "action": "rotate_postgres_password",
        "username": username,
        "new_password": new_password,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    username = parameters.get("username", "")
    # The old password was whatever the connector's admin knows; we can't
    # recover it unless it was stored before rotation. The safest rollback is
    # to rotate to a new password again (break-glass scenario).
    # In smoke tests the old password is passed via parameters["old_password"].
    old_password = parameters.get("old_password") or execution_result.get("old_password")
    if not old_password:
        return {
            "rolled_back": False,
            "reason": "old_password_not_available",
            "username": username,
        }

    client = get_postgres_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_postgres_credentials"}

    client.alter_user_password(username, old_password)
    return {"rolled_back": True, "username": username}
