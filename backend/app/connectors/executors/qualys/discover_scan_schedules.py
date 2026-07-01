# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import xml.etree.ElementTree as ET

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_scan_schedules", "schedules": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/api/2.0/fo/schedule/scan/", params={"action": "list"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        schedules = [{"id": s.findtext("ID"), "title": s.findtext("TITLE"), "active": s.findtext("ACTIVE")} for s in root.findall(".//SCAN")]
    return {"action": "discover_scan_schedules", "schedules": schedules, "count": len(schedules)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
