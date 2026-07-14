# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Bucket deletion is destructive; bucket contents and configuration cannot be recovered"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters.get("bucket_name", "")
    namespace = parameters.get("namespace", "")

    if not creds:
        return {"action": "delete_bucket", "bucket_name": bucket_name, "mock": True}

    from ._client import get_objectstorage_client
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        ns = namespace or client.get_namespace().data
        # Preflight: ensure bucket is empty
        objects = client.list_objects(namespace_name=ns, bucket_name=bucket_name).data.objects
        if objects:
            raise ValueError(
                f"Bucket '{bucket_name}' is not empty ({len(objects)} object(s)). "
                "Empty the bucket before deleting."
            )
        client.delete_bucket(namespace_name=ns, bucket_name=bucket_name)
        return ns

    ns = await loop.run_in_executor(None, _call)
    return {
        "action": "delete_bucket",
        "bucket_name": bucket_name,
        "namespace": ns,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_bucket is destructive — no rollback"}
