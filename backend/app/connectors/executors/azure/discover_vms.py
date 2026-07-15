# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

﻿import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def _real_execute(creds: dict) -> list:
    from ._client import get_compute_client
    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    vms = await loop.run_in_executor(None, lambda: list(compute.virtual_machines.list_all()))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for vm in vms:
        location = getattr(vm, "location", "unknown")
        hw = getattr(vm, "hardware_profile", None)
        vm_size = getattr(hw, "vm_size", "unknown") if hw else "unknown"
        rg = "unknown"
        if vm.id:
            parts = vm.id.split("/")
            try:
                rg = parts[parts.index("resourceGroups") + 1]
            except (ValueError, IndexError):
                pass
        results.append({
            "name": vm.name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["azure", "nexplane-managed"],
            "asset_metadata": {
                "vm_name": vm.name,
                "resource_group": rg,
                "location": location,
                "vm_size": vm_size,
                "provider": "azure",
                "subscription_id": creds.get("subscription_id"),
                "discovered_at": now,
            },
        })
    return results


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    assets = await _real_execute(creds) if creds else []
    subscription_id = (creds or {}).get("subscription_id", "unknown")
    short_id = subscription_id[:8] if subscription_id != "unknown" else "unknown"
    return {
        "action": "discover_vms",
        "assets": assets,
        "_auto_asset": {
            "name": f"Azure · {short_id} (Microsoft Azure)",
            "asset_type": "cloud_account",
            "asset_metadata": {"subscription_id": subscription_id, "provider": "azure"},
            "tags": ["azure", "cloud-account", "live"],
        },
    }

