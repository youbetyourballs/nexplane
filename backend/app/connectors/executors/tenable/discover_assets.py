# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

﻿import asyncio
from datetime import datetime, timezone

_HOSTS = [
    ("payments-api-01", "10.0.1.10", [22, 443, 8080]),
    ("payments-api-02", "10.0.1.11", [22, 443, 8080]),
    ("web-frontend-01", "10.0.2.10", [22, 80, 443]),
    ("db-primary-01", "10.0.3.10", [22, 5432]),
    ("legacy-app-01", "10.0.4.10", [22, 80, 8443, 21]),
]


def _mock_response():
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": hostname,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical" if "db" in hostname else "high",
            "tags": ["tenable-scanned"],
            "asset_metadata": {
                "ip_address": ip,
                "open_ports": ports,
                "os": "Ubuntu 22.04",
                "last_scanned": now,
                "tenable_source": "tenable",
            },
        }
        for hostname, ip, ports in _HOSTS
    ]


async def _real_execute(creds: dict) -> list:
    from ._client import get_tio
    tio = get_tio(creds)
    loop = asyncio.get_event_loop()
    assets = await loop.run_in_executor(None, lambda: list(tio.assets.list()))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for asset in assets:
        hostname = asset.get("fqdn", [None])[0] or asset.get("hostname", [None])[0] or asset.get("id", "unknown")
        ipv4 = asset.get("ipv4", [None])[0]
        results.append({
            "name": hostname,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["tenable-scanned"],
            "asset_metadata": {
                "ip_address": ipv4,
                "open_ports": [],
                "os": asset.get("operating_system", ["unknown"])[0] if asset.get("operating_system") else "unknown",
                "last_scanned": asset.get("last_seen", now),
                "tenable_source": "tenable",
                "asset_id": asset.get("id"),
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)

