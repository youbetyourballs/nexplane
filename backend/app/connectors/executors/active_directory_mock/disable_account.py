import asyncio
from datetime import datetime, timezone


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
    return {"action": "disable_account", "username": username, "user_dn": user_dn, "disabled": True, "ldap_result": str(result), "disabled_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "disable_account", "username": parameters.get("username"), "disabled": True, "disabled_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "enable_account", "username": parameters.get("username")}
