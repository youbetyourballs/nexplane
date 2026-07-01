# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_identities",
        "assets": [
            {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "name": "Alice Example",
                "asset_type": "identity",
                "asset_metadata": {
                    "object_guid": "550e8400-e29b-41d4-a716-446655440001",
                    "sam_account_name": "alice.example",
                    "email": "alice@corp.local",
                    "dn": "CN=Alice Example,OU=Users,DC=corp,DC=local",
                    "enabled": True,
                    "provider": "active_directory",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import SUBTREE, ALL_ATTRIBUTES
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _search():
        conn = get_connection(creds)
        conn.search(
            base_dn,
            "(objectClass=user)",
            SUBTREE,
            attributes=["cn", "sAMAccountName", "mail", "objectGUID", "userAccountControl"],
        )
        entries = list(conn.entries)
        conn.unbind()
        return entries

    entries = await asyncio.get_event_loop().run_in_executor(None, _search)
    assets = []
    for entry in entries:
        try:
            guid = str(entry.objectGUID.value) if entry.objectGUID else None
            uac = int(entry.userAccountControl.value) if entry.userAccountControl else 512
            enabled = not bool(uac & 0x2)
            cn = str(entry.cn.value) if entry.cn else str(entry.entry_dn)
            assets.append({
                "id": guid or str(entry.entry_dn),
                "name": cn,
                "asset_type": "identity",
                "asset_metadata": {
                    "object_guid": guid,
                    "sam_account_name": str(entry.sAMAccountName.value) if entry.sAMAccountName else None,
                    "email": str(entry.mail.value) if entry.mail else None,
                    "dn": str(entry.entry_dn),
                    "enabled": enabled,
                    "provider": "active_directory",
                },
            })
        except Exception:
            continue
    return {"action": "discover_identities", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
