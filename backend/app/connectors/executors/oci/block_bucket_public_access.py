import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters.get("bucket_name", "")
    namespace = parameters.get("namespace", "")

    if not creds:
        return {
            "action": "block_bucket_public_access",
            "bucket_name": bucket_name,
            "previous_public_access_type": "ObjectRead",
            "mock": True,
        }

    from ._client import get_objectstorage_client
    import oci
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        ns = namespace or client.get_namespace().data
        bucket = client.get_bucket(namespace_name=ns, bucket_name=bucket_name).data
        previous = bucket.public_access_type or "NoPublicAccess"
        update_details = oci.object_storage.models.UpdateBucketDetails(public_access_type="NoPublicAccess")
        client.update_bucket(namespace_name=ns, bucket_name=bucket_name, update_bucket_details=update_details)
        return ns, previous

    ns, previous = await loop.run_in_executor(None, _call)
    return {
        "action": "block_bucket_public_access",
        "bucket_name": bucket_name,
        "namespace": ns,
        "previous_public_access_type": previous,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_objectstorage_client
    import oci
    creds = getattr(connector, "credentials", {})
    bucket_name = execution_result.get("bucket_name", parameters.get("bucket_name", ""))
    namespace = execution_result.get("namespace", parameters.get("namespace", ""))
    previous = execution_result.get("previous_public_access_type", "ObjectRead")
    if not creds:
        return {"rolled_back": True, "mock": True}
    loop = asyncio.get_running_loop()
    client = get_objectstorage_client(creds)

    def _restore():
        ns = namespace or client.get_namespace().data
        details = oci.object_storage.models.UpdateBucketDetails(public_access_type=previous)
        client.update_bucket(namespace_name=ns, bucket_name=bucket_name, update_bucket_details=details)

    await loop.run_in_executor(None, _restore)
    return {"rolled_back": True, "bucket_name": bucket_name, "restored_public_access_type": previous}
