# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure Container Apps deploy executor.

Deploys a new container image to an Azure Container App.
Container Apps are revision-based; rollback updates the app back to the prior image.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_POLL_INTERVAL = 10
_POLL_TIMEOUT = 300


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.appcontainers import ContainerAppsAPIClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return ContainerAppsAPIClient(credential, creds["subscription_id"])


async def _wait_ready(creds: dict, resource_group: str, app_name: str) -> str:
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            client = _get_client(creds)
            return client.container_apps.get(resource_group, app_name)

        app = await _run(_poll)
        state = getattr(app, "provisioning_state", "")
        logger.info("azure_container_apps_deploy: polling app=%s state=%s", app_name, state)
        if state == "Succeeded":
            return app.configuration.ingress.fqdn if app.configuration and app.configuration.ingress else ""
        if state == "Failed":
            raise RuntimeError(f"Container App {app_name} provisioning failed")
    raise TimeoutError(f"Container App {app_name} did not become ready within {_POLL_TIMEOUT}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    resource_group = parameters["resource_group"]
    app_name = parameters["app_name"]
    image = parameters["image"]

    def _get():
        client = _get_client(creds)
        return client.container_apps.get(resource_group, app_name)

    app = await _run(_get)
    previous_image = app.template.containers[0].image if app.template and app.template.containers else ""

    def _update():
        from azure.mgmt.appcontainers.models import ContainerApp, Template, Container
        client = _get_client(creds)
        patch_body = ContainerApp(template=Template(containers=[Container(name=app_name, image=image)]))
        poller = client.container_apps.begin_update(resource_group, app_name, patch_body)
        poller.wait(timeout=60)

    await _run(_update)
    logger.info("azure_container_apps_deploy: update initiated for %s → %s", app_name, image)

    fqdn = await _wait_ready(creds, resource_group, app_name)

    return {
        "status": "deployed",
        "app_name": app_name,
        "resource_group": resource_group,
        "previous_image": previous_image,
        "current_image": image,
        "fqdn": fqdn,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials
    resource_group = execution_result["resource_group"]
    app_name = execution_result["app_name"]
    previous_image = execution_result["previous_image"]

    def _restore():
        from azure.mgmt.appcontainers.models import ContainerApp, Template, Container
        client = _get_client(creds)
        patch_body = ContainerApp(template=Template(containers=[Container(name=app_name, image=previous_image)]))
        poller = client.container_apps.begin_update(resource_group, app_name, patch_body)
        poller.wait(timeout=60)

    await _run(_restore)
    fqdn = await _wait_ready(creds, resource_group, app_name)
    logger.info("azure_container_apps_deploy rollback: restored %s to %s", app_name, previous_image)

    return {
        "rolled_back": True,
        "app_name": app_name,
        "restored_image": previous_image,
        "fqdn": fqdn,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
