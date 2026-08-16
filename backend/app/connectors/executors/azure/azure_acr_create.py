# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure Container Registry create executor.

Creates a new Azure Container Registry. SKU options: Basic, Standard, Premium.
Rollback: delete the registry. Only safe to roll back if no images have been pushed.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_acr_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.containerregistry import ContainerRegistryManagementClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return ContainerRegistryManagementClient(credential, creds["subscription_id"])


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    resource_group = parameters["resource_group"]
    registry_name = parameters["registry_name"]
    location = parameters["location"]
    sku = parameters.get("sku", "Basic")
    admin_enabled = parameters.get("admin_enabled", False)

    try:
        def _create():
            from azure.mgmt.containerregistry.models import Registry, Sku
            client = _get_acr_client(creds)
            poller = client.registries.begin_create(
                resource_group,
                registry_name,
                Registry(location=location, sku=Sku(name=sku), admin_user_enabled=admin_enabled),
            )
            return poller.result(timeout=120)

        registry = await _run(_create)
        already_exists = False
    except Exception as exc:
        # ResourceExistsError is checked by name so azure SDK need not be installed in test env
        if type(exc).__name__ != "ResourceExistsError":
            raise

        def _get_existing():
            client = _get_acr_client(creds)
            return client.registries.get(resource_group, registry_name)

        registry = await _run(_get_existing)
        already_exists = True

    logger.info("azure_acr_create: registry %s (already_existed=%s)", registry.name, already_exists)

    return {
        "status": "created",
        "already_exists": already_exists,
        "registry_name": registry.name,
        "resource_group": resource_group,
        "login_server": registry.login_server,
        "location": registry.location,
        "sku": sku,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    if execution_result.get("already_exists"):
        return {"rolled_back": False, "reason": "Registry already existed before execution — not deleting"}

    creds = connector.credentials
    resource_group = execution_result["resource_group"]
    registry_name = execution_result["registry_name"]

    def _delete():
        client = _get_acr_client(creds)
        poller = client.registries.begin_delete(resource_group, registry_name)
        poller.result(timeout=120)

    await _run(_delete)
    logger.info("azure_acr_create rollback: deleted registry %s", registry_name)

    return {
        "rolled_back": True,
        "registry_name": registry_name,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
