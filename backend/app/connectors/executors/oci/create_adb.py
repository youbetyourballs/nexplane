# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-adb")
    db_name = parameters.get("db_name", "nexplaneadb")
    admin_password = parameters.get("admin_password", "Nexplane1234!")
    db_workload = parameters.get("db_workload", "OLTP")
    cpu_core_count = parameters.get("cpu_core_count", 1)
    data_storage_size_in_tbs = parameters.get("data_storage_size_in_tbs", 1)
    is_auto_scaling_enabled = parameters.get("is_auto_scaling_enabled", False)
    is_free_tier = parameters.get("is_free_tier", True)
    license_model = parameters.get("license_model", "LICENSE_INCLUDED")

    auto_asset = {
        "name": display_name,
        "asset_type": "database",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {
            "db_name": db_name,
            "db_workload": db_workload,
            "compartment_id": compartment_id,
            "provider": "oci",
            "is_free_tier": is_free_tier,
        },
        "tags": ["oci", "autonomous-database"],
    }

    if not creds:
        return {
            "action": "create_adb",
            "display_name": display_name,
            "db_name": db_name,
            "status": "AVAILABLE",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    import oci
    from ._client import get_database_client
    db_client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _create():
        details = oci.database.models.CreateAutonomousDatabaseDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            db_name=db_name,
            admin_password=admin_password,
            db_workload=db_workload,
            cpu_core_count=cpu_core_count,
            data_storage_size_in_tbs=data_storage_size_in_tbs,
            is_auto_scaling_enabled=is_auto_scaling_enabled,
            is_free_tier=is_free_tier,
            license_model=license_model,
        )
        return db_client.create_autonomous_database(
            create_autonomous_database_details=details
        ).data

    adb = await loop.run_in_executor(None, _create)
    db_id = adb.id

    # Poll until AVAILABLE — up to 40 x 30s = 20 min
    def _poll():
        for _ in range(40):
            info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
            if info.lifecycle_state == "AVAILABLE":
                return info
            if info.lifecycle_state in ("TERMINATED", "TERMINATING", "FAILED"):
                raise RuntimeError(f"ADB entered terminal state: {info.lifecycle_state}")
            time.sleep(30)
        raise TimeoutError("ADB did not become AVAILABLE within 20 minutes")

    final = await loop.run_in_executor(None, _poll)
    connection_strings = {}
    if final.connection_strings:
        connection_strings = {
            "high": final.connection_strings.high,
            "medium": final.connection_strings.medium,
            "low": final.connection_strings.low,
        }

    auto_asset["asset_metadata"]["db_id"] = db_id
    auto_asset["asset_metadata"]["lifecycle_state"] = final.lifecycle_state
    auto_asset["asset_metadata"]["connection_strings"] = connection_strings
    auto_asset["asset_metadata"]["cpu_core_count"] = cpu_core_count
    auto_asset["asset_metadata"]["data_storage_size_in_tbs"] = data_storage_size_in_tbs

    return {
        "action": "create_adb",
        "display_name": display_name,
        "db_id": db_id,
        "db_name": db_name,
        "status": final.lifecycle_state,
        "connection_strings": connection_strings,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_adb import execute as delete
    return await delete(
        {"db_id": execution_result.get("db_id", parameters.get("db_id", ""))},
        [], connector,
    )
