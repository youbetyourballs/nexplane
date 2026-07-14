# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_artifacts_client

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-repo")
    is_public = bool(parameters.get("is_public", False))

    if not creds:
        return {
            "action": "create_ocir_repository",
            "repository_id": "mock-repo-id",
            "display_name": display_name,
            "mock": True,
        }

    import oci
    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    def _create():
        details = oci.artifacts.models.CreateContainerRepositoryDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            is_public=is_public,
        )
        return client.create_container_repository(
            create_container_repository_details=details
        ).data

    repo = await loop.run_in_executor(None, _create)
    return {
        "action": "create_ocir_repository",
        "repository_id": repo.id,
        "display_name": repo.display_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repository_id = execution_result.get("repository_id", "")
    if not creds or not repository_id:
        return {"action": "rollback_create_ocir_repository", "mock": True}

    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: client.delete_container_repository(repository_id=repository_id),
    )
    return {
        "action": "rollback_create_ocir_repository",
        "repository_id": repository_id,
        "status": "DELETED",
    }
