# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    path = parameters["path"]
    audit_type = parameters["type"]
    if not creds:
        return {"action": "enable_audit_device", "path": path, "type": audit_type, "enabled": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.enable_audit_device(device_type=audit_type, path=path, options=parameters.get("options", {})))
    return {"action": "enable_audit_device", "path": path, "type": audit_type, "enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable audit device via Vault CLI to avoid losing audit trail"}
