# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "export_scan_results",
        "assets": [
            {"id": "host-001", "name": "192.168.1.10", "asset_type": "server", "metadata": {"critical_count": 2, "high_count": 5, "medium_count": 12}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    import httpx
    headers = {"X-ApiKeys": f"accessKey={creds['access_key']};secretKey={creds['secret_key']}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://cloud.tenable.com/scans", headers=headers)
        resp.raise_for_status()
        scans = resp.json().get('scans', []) or []
    assets = []
    for scan in scans[:5]:  # Limit to most recent 5 scans
        assets.append({
            "id": str(scan.get('id', '')),
            "name": scan.get('name', 'unknown'),
            "asset_type": "server",
            "metadata": {
                "status": scan.get('status'),
                "last_modification_date": scan.get('last_modification_date'),
                "host_count": scan.get('total', 0),
            },
        })
    return {"action": "export_scan_results", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "export actions have no rollback"}
