import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ts = int(datetime.now(timezone.utc).timestamp())
    compartment_id = parameters.get("compartment_id", creds.get("compartment_id", "") if creds else "ocid1.compartment.oc1..mock")
    name = parameters.get("name", f"nexplane-bucket-{ts}")
    storage_tier = parameters.get("storage_tier", "Standard")
    public_access_type = parameters.get("public_access_type", "NoPublicAccess")
    versioning = parameters.get("versioning", "Disabled")
    region = (creds.get("region", "us-ashburn-1") if creds else "us-ashburn-1")

    auto_asset = {
        "name": name,
        "asset_type": "storage_bucket",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "bucket_name": name,
            "namespace": "",
            "compartment_id": compartment_id,
            "region": region,
            "provider": "oci",
            "public_access_type": public_access_type,
            "storage_tier": storage_tier,
            "versioning": versioning,
        },
        "tags": ["oci", "object-storage", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_bucket",
            "bucket_name": name,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_objectstorage_client
    import oci
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        namespace = client.get_namespace().data
        details = oci.object_storage.models.CreateBucketDetails(
            name=name,
            compartment_id=compartment_id,
            storage_tier=storage_tier,
            public_access_type=public_access_type,
            versioning=versioning,
        )
        client.create_bucket(namespace_name=namespace, create_bucket_details=details)
        return namespace

    namespace = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["namespace"] = namespace

    return {
        "action": "create_bucket",
        "bucket_name": name,
        "namespace": namespace,
        "region": region,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_bucket import execute as delete
    return await delete(
        {
            "bucket_name": execution_result.get("bucket_name", parameters.get("name")),
            "namespace": execution_result.get("namespace", ""),
        },
        [],
        connector,
    )
