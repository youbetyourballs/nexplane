# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

try:
    from ._client import get_connection, prepare_ad_target
except ImportError:
    get_connection = None
    prepare_ad_target = None

ROLLBACK_CAPABILITY = "full"


async def _verify_disabled(username: str, user_dn: str, creds: dict, retries: int = 3, delay: float = 2.0) -> bool:
    from ._client import get_connection
    from ldap3 import SUBTREE
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _check():
        conn = get_connection(creds)
        conn.search(base_dn, f"(distinguishedName={user_dn})", SUBTREE, attributes=["userAccountControl"])
        entries = conn.entries
        conn.unbind()
        if not entries:
            return False
        uac = int(entries[0].userAccountControl.value)
        return bool(uac & 0x2)

    for _ in range(retries):
        confirmed = await asyncio.get_event_loop().run_in_executor(None, _check)
        if confirmed:
            return True
        await asyncio.sleep(delay)
    return False


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import MODIFY_REPLACE
    username = parameters.get("username")
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    user_dn = parameters.get("user_dn") or f"CN={username},{base_dn}"

    def _sync():
        conn = get_connection(creds)
        conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        conn.unbind()
        return conn.result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    confirmed = await _verify_disabled(username, user_dn, creds)
    return {
        "action": "disable_account",
        "username": username,
        "user_dn": user_dn,
        "disabled": confirmed,
        "ldap_result": str(result),
        "disabled_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "disable_account",
            "username": parameters.get("username"),
            "disabled": True,
            "disabled_at": datetime.now(timezone.utc).isoformat(),
        }
    from ._client import prepare_ad_target
    creds = await prepare_ad_target(connector, creds)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ldap3 import MODIFY_REPLACE
    creds = getattr(connector, "credentials", {})
    username = execution_result.get("username") or parameters.get("username")
    user_dn = execution_result.get("user_dn") or parameters.get("user_dn")
    if not creds:
        return {"rolled_back": True, "username": username, "mock": True}
    try:
        resolved = await prepare_ad_target(connector, creds)

        def _enable():
            conn = get_connection(resolved)
            conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [512])]})
            result = conn.result
            conn.unbind()
            return result

        result = await asyncio.get_event_loop().run_in_executor(None, _enable)
        return {"rolled_back": True, "username": username, "user_dn": user_dn, "ldap_result": str(result)}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
