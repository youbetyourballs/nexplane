# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    username = parameters['username']
    if not creds:
        return {"action": "unlock_account", "username": username, "mock": True}
    import asyncio
    from ldap3 import Server, Connection, ALL, MODIFY_REPLACE
    from ._client import prepare_ad_target
    creds = await prepare_ad_target(connector, creds)
    loop = asyncio.get_event_loop()

    def _call():
        server = Server(creds.get('_forward_host', creds['server']),
                        port=int(creds.get('_forward_port', creds.get('port', 389))),
                        get_info=ALL)
        conn = Connection(server, user=creds['bind_dn'], password=creds['bind_password'], auto_bind=True)
        conn.search(creds['base_dn'], f'(sAMAccountName={username})', attributes=['distinguishedName'])
        if not conn.entries:
            raise ValueError(f"User '{username}' not found")
        dn = str(conn.entries[0]['distinguishedName'])
        conn.modify(dn, {'lockoutTime': [(MODIFY_REPLACE, ['0'])]})
        conn.unbind()

    await loop.run_in_executor(None, _call)
    return {"action": "unlock_account", "username": username, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "account unlock has no rollback"}
