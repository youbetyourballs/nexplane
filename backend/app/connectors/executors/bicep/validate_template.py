# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    template = parameters["template"]
    if not creds:
        return {"action": "validate_template", "resource_group": resource_group, "valid": True}
    from ._client import get_client
    from azure.mgmt.resource.resources.models import Deployment, DeploymentProperties, DeploymentMode
    loop = asyncio.get_event_loop()
    client = get_client(creds)
    deployment = Deployment(properties=DeploymentProperties(mode=DeploymentMode.incremental, template=template))
    result = await loop.run_in_executor(None, lambda: client.deployments.begin_validate(resource_group, "nexplane-validate", deployment).result())
    return {"action": "validate_template", "resource_group": resource_group, "valid": True, "result": str(result)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validate has no rollback"}
