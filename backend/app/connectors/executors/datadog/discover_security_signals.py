# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_security_signals", "signals": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/security_monitoring/signals", params={"filter[status]": "open", "page[limit]": 100})
        resp.raise_for_status()
        signals = [{"id": s["id"], "attributes": s.get("attributes", {})} for s in resp.json().get("data", [])]
    return {"action": "discover_security_signals", "signals": signals, "count": len(signals)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
