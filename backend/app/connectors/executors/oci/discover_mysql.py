# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {"assets": [], "mock": True}

    from ._client import get_mysql_client
    mysql_client = get_mysql_client(creds)
    loop = asyncio.get_running_loop()

    def _list():
        return mysql_client.list_db_systems(compartment_id=compartment_id).data

    systems = await loop.run_in_executor(None, _list)
    assets = []
    for sys in systems:
        endpoint_hostname = ""
        port = 3306
        if sys.endpoints:
            endpoint_hostname = sys.endpoints[0].hostname or ""
            port = sys.endpoints[0].port or 3306
        assets.append({
            "name": sys.display_name,
            "asset_type": "database",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "db_system_id": sys.id,
                "mysql_version": sys.mysql_version,
                "shape_name": sys.shape_name,
                "lifecycle_state": sys.lifecycle_state,
                "endpoint_hostname": endpoint_hostname,
                "port": port,
                "data_storage_size_in_gbs": sys.data_storage_size_in_gbs,
                "compartment_id": sys.compartment_id,
                "provider": "oci",
            },
            "tags": ["oci", "mysql"],
            "_dedup_key": sys.id,
        })

    return {
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
