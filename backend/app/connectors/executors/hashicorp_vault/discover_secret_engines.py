# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_secret_engines", "engines": [{"path": "secret/", "type": "kv", "version": 2}], "count": 1}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    mounts = await loop.run_in_executor(None, lambda: client.sys.list_mounted_secrets_engines())
    engines = [{"path": path, "type": info.get("type"), "description": info.get("description")} for path, info in mounts.items()]
    return {"action": "discover_secret_engines", "engines": engines, "count": len(engines)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
