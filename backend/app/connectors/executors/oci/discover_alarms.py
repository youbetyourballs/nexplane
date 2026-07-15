# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Discovers OCI Monitoring alarms in a compartment.
    Alarms are surfaced via compartment asset metadata updates, not as
    standalone asset records (same approach as AWS CloudWatch alarms).
    """
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {"alarms": [], "mock": True}

    from ._client import get_monitoring_client
    mon_client = get_monitoring_client(creds)
    loop = asyncio.get_running_loop()

    def _list():
        return mon_client.list_alarms(compartment_id=compartment_id).data

    alarms = await loop.run_in_executor(None, _list)
    alarm_list = [
        {
            "alarm_id": a.id,
            "display_name": a.display_name,
            "namespace": a.namespace,
            "query": a.query,
            "severity": a.severity,
            "lifecycle_state": a.lifecycle_state,
            "is_enabled": a.is_enabled,
        }
        for a in alarms
    ]

    return {
        "alarms": alarm_list,
        "count": len(alarm_list),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
