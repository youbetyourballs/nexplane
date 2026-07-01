# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "winrm_collect_diagnostics",
            "diagnostics": {"mock": True},
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import prepare_winrm_client

    client = await prepare_winrm_client(connector)
    loop = asyncio.get_event_loop()

    def _collect():
        results = {}

        stdout, _, _ = client.run_ps(
            "Get-EventLog -LogName System -Newest 50 | "
            "Select-Object TimeGenerated, EntryType, Source, Message | ConvertTo-Json"
        )
        results["event_log_system"] = stdout.strip()

        stdout2, _, _ = client.run_ps(
            "Get-Service | Where-Object Status -eq Running | "
            "Select-Object Name, Status | ConvertTo-Json"
        )
        results["running_services"] = stdout2.strip()

        stdout3, _, _ = client.run_cmd("systeminfo")
        results["systeminfo"] = stdout3.strip()

        return results

    try:
        diagnostics = await loop.run_in_executor(None, _collect)
    except Exception as e:
        return {
            "action": "winrm_collect_diagnostics",
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_collect_diagnostics",
        "diagnostics": diagnostics,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    return {"rolled_back": True, "reason": "collect_diagnostics is read-only"}
