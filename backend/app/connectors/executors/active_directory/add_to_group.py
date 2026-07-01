# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    username = parameters.get("username", "")
    group_name = parameters.get("group_name", "")
    if not username or not group_name:
        return {"status": "error", "message": "username and group_name are required"}

    if not creds:
        return {"action": "add_to_group", "username": username, "group_name": group_name, "mock": True}

    loop = asyncio.get_event_loop()

    if creds.get("winrm_hostname"):
        from ._client import run_winrm_ps
        script = (
            f"Add-ADGroupMember -Identity '{group_name}' -Members '{username}' -ErrorAction Stop\n"
            f"Write-Output 'ADDED_OK'"
        )
        out, err, rc = await loop.run_in_executor(None, run_winrm_ps, creds, script)
        if rc != 0 or "ADDED_OK" not in out:
            raise RuntimeError(f"Add-ADGroupMember failed (rc={rc}): {err or out}")
    else:
        from ldap3 import Server, Connection, ALL, MODIFY_ADD
        from ._client import get_connection

        def _ldap():
            conn = get_connection(creds)
            # Resolve user DN
            conn.search(creds["base_dn"], f"(sAMAccountName={username})", attributes=["distinguishedName"])
            if not conn.entries:
                raise ValueError(f"User '{username}' not found")
            user_dn = str(conn.entries[0]["distinguishedName"])
            # Resolve group DN
            conn.search(creds["base_dn"], f"(cn={group_name})", attributes=["distinguishedName"])
            if not conn.entries:
                raise ValueError(f"Group '{group_name}' not found")
            group_dn = str(conn.entries[0]["distinguishedName"])
            conn.modify(group_dn, {"member": [(MODIFY_ADD, [user_dn])]})
            if conn.result["result"] != 0:
                raise RuntimeError(f"LDAP modify failed: {conn.result['description']}")
            conn.unbind()

        await loop.run_in_executor(None, _ldap)

    return {
        "action": "add_to_group",
        "username": username,
        "group_name": group_name,
        "added": True,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Remove the user from the group to undo the add."""
    creds = getattr(connector, "credentials", {}) or {}
    username = parameters.get("username", "")
    group_name = parameters.get("group_name", "")

    if not execution_result.get("added"):
        return {"rolled_back": False, "reason": "original add did not complete"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    loop = asyncio.get_event_loop()

    if creds.get("winrm_hostname"):
        from ._client import run_winrm_ps
        script = (
            f"Remove-ADGroupMember -Identity '{group_name}' -Members '{username}' -Confirm:$false -ErrorAction Stop\n"
            f"Write-Output 'REMOVED_OK'"
        )
        out, err, rc = await loop.run_in_executor(None, run_winrm_ps, creds, script)
        if rc != 0 or "REMOVED_OK" not in out:
            return {"rolled_back": False, "reason": f"Remove-ADGroupMember failed: {err or out}"}
    else:
        from ldap3 import MODIFY_DELETE
        from ._client import get_connection

        def _ldap():
            conn = get_connection(creds)
            conn.search(creds["base_dn"], f"(sAMAccountName={username})", attributes=["distinguishedName"])
            if not conn.entries:
                raise ValueError(f"User '{username}' not found")
            user_dn = str(conn.entries[0]["distinguishedName"])
            conn.search(creds["base_dn"], f"(cn={group_name})", attributes=["distinguishedName"])
            if not conn.entries:
                raise ValueError(f"Group '{group_name}' not found")
            group_dn = str(conn.entries[0]["distinguishedName"])
            conn.modify(group_dn, {"member": [(MODIFY_DELETE, [user_dn])]})
            conn.unbind()

        await loop.run_in_executor(None, _ldap)

    return {"rolled_back": True, "username": username, "group_name": group_name}
