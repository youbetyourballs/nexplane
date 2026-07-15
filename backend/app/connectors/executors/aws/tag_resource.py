# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def _real_execute(creds: dict, parameters: dict) -> dict:
    from ._client import get_boto3_client
    tagging = get_boto3_client(creds, 'resourcegroupstaggingapi')
    loop = asyncio.get_event_loop()
    resource_arn = parameters['resource_arn']
    tags = parameters['tags']

    def _call():
        tagging.tag_resources(ResourceARNList=[resource_arn], Tags=tags)

    await loop.run_in_executor(None, _call)
    return {"action": "tag_resource", "resource_arn": resource_arn, "tags": tags, "executed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "tag_resource", "resource_arn": parameters.get('resource_arn'), "tags": parameters.get('tags'), "mock": True}
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "tag changes have no automatic rollback"}
