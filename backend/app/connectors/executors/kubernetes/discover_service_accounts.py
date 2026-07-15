# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_service_accounts", "service_accounts": [{"name": "default", "namespace": "default"}], "count": 1}
    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    sa_list = await loop.run_in_executor(None, lambda: clients["core"].list_service_account_for_all_namespaces())
    service_accounts = [{"name": sa.metadata.name, "namespace": sa.metadata.namespace} for sa in sa_list.items]
    return {"action": "discover_service_accounts", "service_accounts": service_accounts, "count": len(service_accounts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
