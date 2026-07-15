# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters["bucket_name"]
    if not creds:
        return {"action": "delete_bucket", "bucket_name": bucket_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import storage
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        client = storage.Client(credentials=credentials, project=project)
        bucket = client.bucket(bucket_name)
        bucket.delete(force=True)

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_bucket", "bucket_name": bucket_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "bucket deletion is irreversible"}
