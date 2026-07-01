# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

# Approved PowerShell template IDs -> scripts
APPROVED_PS_TEMPLATES = {
    "winrm_get_services": "Get-Service | Select-Object Name, Status, StartType | ConvertTo-Json",
    "winrm_get_running_services": "Get-Service | Where-Object Status -eq Running | Select-Object Name, Status | ConvertTo-Json",
    "winrm_get_eventlog_system": "Get-EventLog -LogName System -Newest 20 | Select-Object TimeGenerated, EntryType, Source, Message | ConvertTo-Json",
    "winrm_system_info": "systeminfo",
    "winrm_disk_space": "Get-PSDrive C | Select-Object Used, Free | ConvertTo-Json",
}


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    template_id = parameters.get("template_id", "")
    if not template_id or template_id not in APPROVED_PS_TEMPLATES:
        raise ValueError(
            "Template '{}' is not approved. Approved: {}".format(
                template_id, list(APPROVED_PS_TEMPLATES.keys())
            )
        )

    script = APPROVED_PS_TEMPLATES[template_id]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "winrm_execute_template",
            "template_id": template_id,
            "host_results": [
                {"asset_id": str(a), "exit_code": 0,
                 "stdout": "[mock] template executed", "stderr": ""}
                for a in asset_ids
            ],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import prepare_winrm_client

    client = await prepare_winrm_client(connector)
    loop = asyncio.get_event_loop()

    def _run():
        stdout, stderr, rc = client.run_ps(script)
        return stdout, stderr, rc

    host_results = []
    for asset_id in asset_ids:
        try:
            stdout, stderr, rc = await loop.run_in_executor(None, _run)
            host_results.append({
                "asset_id": str(asset_id),
                "exit_code": rc,
                "stdout": stdout,
                "stderr": stderr,
            })
        except Exception as e:
            host_results.append({
                "asset_id": str(asset_id),
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            })

    return {
        "action": "winrm_execute_template",
        "template_id": template_id,
        "host_results": host_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    return {"rolled_back": False, "reason": "template execution rollback is manual"}
