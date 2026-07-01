# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

﻿from datetime import datetime, timezone

_THREATS = [
    ("legacy-app-01", "exploit", "CVE-2023-44487"),
    ("payments-api-01", "spyware", "trojan-generic"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": host,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical",
            "tags": ["palo-threat-detected"],
            "asset_metadata": {
                "threat_category": threat_type,
                "threat_id": threat_id,
                "last_threat_seen": now,
                "paloalto_source": "paloalto",
            },
        }
        for host, threat_type, threat_id in _THREATS
    ]

