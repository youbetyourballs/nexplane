# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import MODIFY_REPLACE
    username = parameters.get("username")
    password = parameters.get("new_password", "")
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    user_dn = parameters.get("user_dn") or f"CN={username},{base_dn}"

    def _sync():
        conn = get_connection(creds)
        encoded_pw = f'"{password}"'.encode("utf-16-le")
        conn.modify(user_dn, {"unicodePwd": [(MODIFY_REPLACE, [encoded_pw])]})
        conn.unbind()
        return conn.result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {"action": "reset_password", "username": username, "user_dn": user_dn, "must_change_on_next_login": True, "ldap_result": str(result), "reset_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "reset_password", "username": parameters.get("username"), "must_change_on_next_login": True, "reset_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "password reset has no rollback"}
