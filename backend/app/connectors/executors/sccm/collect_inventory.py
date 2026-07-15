# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only inventory collection — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Pull hardware/software inventory for a device from SCCM.

    Read-only operation — no rollback needed.

    Parameters
    ----------
    device_name : str
        NetBIOS name of the device as registered in SCCM.
    """
    device_name = parameters.get("device_name", "")
    if not device_name:
        raise ValueError("device_name is required")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "sccm_collect_inventory",
            "device_name": device_name,
            "device": {"Name": device_name, "ResourceID": "mock-resource"},
            "software": [],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_sccm_client

    loop = asyncio.get_event_loop()

    def _run():
        client = get_sccm_client(connector)
        device = client.get_device(device_name)
        software = client.get_software_inventory(device_name)
        return device, software

    device, software = await loop.run_in_executor(None, _run)

    return {
        "action": "sccm_collect_inventory",
        "device_name": device_name,
        "device": device,
        "software_count": len(software),
        "software": software,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Inventory collection is read-only — no rollback required."""
    return {"rolled_back": False, "reason": "collect_inventory is read-only, no rollback needed"}
