import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {"assets": [], "mock": True}

    from ._client import get_database_client
    db_client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _list():
        return db_client.list_autonomous_databases(compartment_id=compartment_id).data

    adbs = await loop.run_in_executor(None, _list)
    assets = []
    for adb in adbs:
        assets.append({
            "name": adb.display_name,
            "asset_type": "database",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "db_id": adb.id,
                "db_name": adb.db_name,
                "db_workload": adb.db_workload,
                "lifecycle_state": adb.lifecycle_state,
                "cpu_core_count": adb.cpu_core_count,
                "data_storage_size_in_tbs": adb.data_storage_size_in_tbs,
                "is_free_tier": adb.is_free_tier,
                "connection_strings": (
                    {
                        "high": adb.connection_strings.high if adb.connection_strings else None,
                        "medium": adb.connection_strings.medium if adb.connection_strings else None,
                        "low": adb.connection_strings.low if adb.connection_strings else None,
                    }
                    if adb.connection_strings else {}
                ),
                "compartment_id": adb.compartment_id,
                "provider": "oci",
            },
            "tags": ["oci", "autonomous-database"],
            "_dedup_key": adb.id,
        })

    return {
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
