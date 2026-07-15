# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters["bucket_name"]
    if not creds:
        return {"action": "block_public_bucket_access", "bucket_name": bucket_name, "public_access_blocked": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import storage
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = storage.Client(credentials=credentials, project=project)

    def _block():
        bucket = client.bucket(bucket_name)
        policy = bucket.get_iam_policy(requested_policy_version=3)
        policy.bindings = [
            b for b in policy.bindings
            if "allUsers" not in b.get("members", []) and "allAuthenticatedUsers" not in b.get("members", [])
        ]
        bucket.set_iam_policy(policy)
        return True

    await loop.run_in_executor(None, _block)
    return {"action": "block_public_bucket_access", "bucket_name": bucket_name, "public_access_blocked": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-granting public access requires explicit action"}
