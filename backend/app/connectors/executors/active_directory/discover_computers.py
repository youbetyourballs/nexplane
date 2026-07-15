# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

_OUS = ["OU=Servers,DC=acme,DC=example", "OU=Workstations,DC=acme,DC=example", "OU=DMZ,DC=acme,DC=example"]
_HOSTS = ["dc-01", "dc-02", "web-01", "web-02", "app-01", "app-02", "db-01", "payments-api-01", "bastion-01"]


def _mock_response():
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": host,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["ad-joined"],
            "asset_metadata": {
                "os": "Windows Server 2022",
                "ou": random.choice(_OUS),
                "last_logon": now,
                "ad_source": "active_directory",
            },
        }
        for host in _HOSTS
    ]


async def _real_execute(creds: dict) -> list:
    from ._client import get_connection
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(
            base_dn,
            "(objectClass=computer)",
            attributes=["cn", "distinguishedName", "operatingSystem", "lastLogonTimestamp"],
        )
        entries = list(conn.entries)
        conn.unbind()
        return entries

    entries = await asyncio.get_event_loop().run_in_executor(None, _sync)
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for entry in entries:
        cn = str(entry.cn) if hasattr(entry, "cn") else "unknown"
        os_name = str(entry.operatingSystem) if hasattr(entry, "operatingSystem") else "Windows"
        dn = str(entry.distinguishedName) if hasattr(entry, "distinguishedName") else ""
        results.append({
            "name": cn,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["ad-joined"],
            "asset_metadata": {
                "os": os_name,
                "ou": dn,
                "last_logon": now,
                "ad_source": "active_directory",
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    from ._client import prepare_ad_target
    creds = await prepare_ad_target(connector, creds)
    return await _real_execute(creds)

