# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters["bucket_name"]
    location = parameters.get("location", "US")
    if not creds:
        return {"action": "create_bucket", "bucket_name": bucket_name, "location": location}
    from ._client import get_credentials, get_project_id
    from google.cloud import storage
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        client = storage.Client(credentials=credentials, project=project)
        bucket = client.bucket(bucket_name)
        bucket.storage_class = "STANDARD"
        new_bucket = client.create_bucket(bucket, location=location)
        return new_bucket.location

    actual_location = await loop.run_in_executor(None, _create)
    return {"action": "create_bucket", "bucket_name": bucket_name, "location": actual_location}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_bucket import execute as delete
    bucket_name = execution_result.get("bucket_name", parameters.get("bucket_name"))
    return await delete({"bucket_name": bucket_name}, [], connector)
