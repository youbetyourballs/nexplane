import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-mysql")
    admin_username = parameters.get("admin_username", "nexplane")
    admin_password = parameters.get("admin_password", "Nexplane1234!")
    shape_name = parameters.get("shape_name", "MySQL.VM.Standard.E4.1.8GB")
    mysql_version = parameters.get("mysql_version", "8.0.36")
    subnet_id = parameters.get("subnet_id", "")
    data_storage_size_in_gbs = parameters.get("data_storage_size_in_gbs", 50)
    availability_domain = parameters.get("availability_domain", "")

    auto_asset = {
        "name": display_name,
        "asset_type": "database",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {
            "shape_name": shape_name,
            "mysql_version": mysql_version,
            "compartment_id": compartment_id,
            "provider": "oci",
        },
        "tags": ["oci", "mysql"],
    }

    if not creds:
        return {
            "action": "create_mysql",
            "display_name": display_name,
            "db_system_id": "mock-mysql-id",
            "status": "ACTIVE",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    import oci
    from ._client import get_mysql_client
    mysql_client = get_mysql_client(creds)
    loop = asyncio.get_running_loop()

    def _create():
        details = oci.mysql.models.CreateDbSystemDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            admin_username=admin_username,
            admin_password=admin_password,
            shape_name=shape_name,
            mysql_version=mysql_version,
            subnet_id=subnet_id,
            data_storage_size_in_gbs=data_storage_size_in_gbs,
            availability_domain=availability_domain,
        )
        return mysql_client.create_db_system(create_db_system_details=details).data

    sys = await loop.run_in_executor(None, _create)
    db_system_id = sys.id

    # Poll until ACTIVE — up to 50 x 30s = 25 min
    def _poll():
        for _ in range(50):
            info = mysql_client.get_db_system(db_system_id=db_system_id).data
            if info.lifecycle_state == "ACTIVE":
                return info
            if info.lifecycle_state in ("FAILED", "DELETED"):
                raise RuntimeError(f"MySQL DB System entered state: {info.lifecycle_state}")
            time.sleep(30)
        raise TimeoutError("MySQL DB System did not become ACTIVE within 25 minutes")

    final = await loop.run_in_executor(None, _poll)
    endpoint_hostname = ""
    port = 3306
    if final.endpoints:
        endpoint_hostname = final.endpoints[0].hostname or ""
        port = final.endpoints[0].port or 3306

    auto_asset["asset_metadata"]["db_system_id"] = db_system_id
    auto_asset["asset_metadata"]["lifecycle_state"] = final.lifecycle_state
    auto_asset["asset_metadata"]["endpoint_hostname"] = endpoint_hostname
    auto_asset["asset_metadata"]["port"] = port
    auto_asset["asset_metadata"]["data_storage_size_in_gbs"] = data_storage_size_in_gbs

    return {
        "action": "create_mysql",
        "display_name": display_name,
        "db_system_id": db_system_id,
        "endpoint_hostname": endpoint_hostname,
        "port": port,
        "status": final.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_mysql import execute as delete
    return await delete(
        {"db_system_id": execution_result.get("db_system_id", parameters.get("db_system_id", ""))},
        [], connector,
    )
