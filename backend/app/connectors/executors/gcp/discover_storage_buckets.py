# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_storage_buckets", "buckets": [
            {"name": "mock-bucket", "location": "US", "public": False, "versioning": True}
        ], "count": 1}
    from ._client import get_credentials, get_project_id
    from google.cloud import storage
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = storage.Client(credentials=credentials, project=project)
    bucket_list = await loop.run_in_executor(None, lambda: list(client.list_buckets()))
    buckets = []
    for b in bucket_list:
        iam_config = b.iam_configuration
        buckets.append({
            "name": b.name,
            "location": b.location,
            "storage_class": b.storage_class,
            "versioning": b.versioning_enabled,
            "public_access_prevention": getattr(iam_config, "public_access_prevention", None),
        })
    return {"action": "discover_storage_buckets", "buckets": buckets, "count": len(buckets)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
