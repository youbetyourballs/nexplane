# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP Cloud Run deploy executor.

Deploys a new container image to a Cloud Run service. Cloud Run creates
a new revision automatically; traffic routes to the new revision once ready.

Rollback: update the service back to the previous image (creates another
revision pointing to the old image).
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_POLL_INTERVAL = 5
_POLL_TIMEOUT = 300


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def _wait_ready(creds: dict, project_id: str, region: str, service_name: str) -> str:
    full_name = f"projects/{project_id}/locations/{region}/services/{service_name}"
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            from google.cloud import run_v2
            from app.connectors.executors.gcp._client import get_credentials
            credentials = get_credentials(creds)
            client = run_v2.ServicesClient(credentials=credentials)
            return client.get_service(name=full_name)

        svc = await _run(_poll)
        state = getattr(svc, "terminal_condition", None)
        if state and getattr(state, "state", None) and state.state.name == "CONDITION_SUCCEEDED":
            return svc.uri
        logger.info("cloud_run_deploy: polling service=%s state=%s", service_name,
                    state.state.name if state and state.state else "unknown")
    raise TimeoutError(f"Cloud Run service {service_name} did not become ready within {_POLL_TIMEOUT}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    service_name = parameters["service_name"]
    region = parameters["region"]
    image = parameters["image"]

    def _get_project_id():
        from app.connectors.executors.gcp._client import get_project_id
        return get_project_id(creds)

    project_id = await _run(_get_project_id)
    full_name = f"projects/{project_id}/locations/{region}/services/{service_name}"

    def _get_service():
        from google.cloud import run_v2
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = run_v2.ServicesClient(credentials=credentials)
        return client.get_service(name=full_name)

    service = await _run(_get_service)
    previous_image = service.template.containers[0].image if service.template.containers else ""

    def _update_service():
        from google.cloud import run_v2
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = run_v2.ServicesClient(credentials=credentials)
        updated = run_v2.Service(
            name=full_name,
            template=run_v2.RevisionTemplate(
                containers=[run_v2.Container(image=image)]
            ),
        )
        op = client.update_service(service=updated)
        return op.result(timeout=60)

    await _run(_update_service)
    logger.info("cloud_run_deploy: update initiated for %s → %s", service_name, image)

    url = await _wait_ready(creds, project_id, region, service_name)

    return {
        "status": "deployed",
        "service_name": service_name,
        "region": region,
        "previous_image": previous_image,
        "current_image": image,
        "url": url,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials
    service_name = execution_result["service_name"]
    region = execution_result["region"]
    previous_image = execution_result["previous_image"]

    def _get_project_id():
        from app.connectors.executors.gcp._client import get_project_id
        return get_project_id(creds)

    project_id = await _run(_get_project_id)
    full_name = f"projects/{project_id}/locations/{region}/services/{service_name}"

    def _restore():
        from google.cloud import run_v2
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = run_v2.ServicesClient(credentials=credentials)
        updated = run_v2.Service(
            name=full_name,
            template=run_v2.RevisionTemplate(
                containers=[run_v2.Container(image=previous_image)]
            ),
        )
        op = client.update_service(service=updated)
        return op.result(timeout=60)

    await _run(_restore)
    url = await _wait_ready(creds, project_id, region, service_name)
    logger.info("cloud_run_deploy rollback: restored %s to %s", service_name, previous_image)

    return {
        "rolled_back": True,
        "service_name": service_name,
        "restored_image": previous_image,
        "url": url,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
