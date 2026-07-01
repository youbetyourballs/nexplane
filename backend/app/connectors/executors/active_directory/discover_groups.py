# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_groups",
        "assets": [
            {"id": "CN=Domain Admins,CN=Users,DC=corp,DC=local", "name": "Domain Admins", "asset_type": "identity", "metadata": {"type": "security", "member_count": 3}},
            {"id": "CN=IT-Staff,OU=Groups,DC=corp,DC=local", "name": "IT-Staff", "asset_type": "identity", "metadata": {"type": "security", "member_count": 12}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    import asyncio
    from ldap3 import Server, Connection, ALL, NTLM
    loop = asyncio.get_event_loop()

    def _call():
        server = Server(creds.get('_forward_host', creds['server']),
                        port=int(creds.get('_forward_port', creds.get('port', 389))),
                        get_info=ALL)
        conn = Connection(server, user=creds['bind_dn'], password=creds['bind_password'], auto_bind=True)
        conn.search(creds['base_dn'], '(objectClass=group)', attributes=['cn', 'distinguishedName', 'groupType', 'member'])
        assets = []
        for entry in conn.entries:
            assets.append({
                "id": str(entry['distinguishedName']),
                "name": str(entry['cn']),
                "asset_type": "identity",
                "metadata": {"member_count": len(entry['member']) if entry['member'] else 0},
            })
        conn.unbind()
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_groups", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    from ._client import prepare_ad_target
    creds = await prepare_ad_target(connector, creds)
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
