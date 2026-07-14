# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

from ._client import get_artifacts_client
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repository_id = parameters.get("repository_id", "")

    if not creds:
        return {"action": "delete_ocir_repository", "repository_id": repository_id, "mock": True}

    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    repo = await loop.run_in_executor(
        None, lambda: client.get_container_repository(repository_id=repository_id).data
    )

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {
                "display_name": repo.display_name,
                "compartment_id": repo.compartment_id,
                "is_public": bool(repo.is_public) if repo.is_public is not None else False,
                "defined_tags": repo.defined_tags or {},
                "freeform_tags": repo.freeform_tags or {},
            },
        )
        await db.commit()

    await loop.run_in_executor(
        None, lambda: client.delete_container_repository(repository_id=repository_id)
    )
    return {
        "action": "delete_ocir_repository",
        "repository_id": repository_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    import oci
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "rollback_delete_ocir_repository", "mock": True}

    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )

    if not state:
        return {"action": "rollback_delete_ocir_repository", "error": "pre-state not found"}

    def _recreate():
        details = oci.artifacts.models.CreateContainerRepositoryDetails(
            compartment_id=state["compartment_id"],
            display_name=state["display_name"],
            is_public=state.get("is_public", False),
        )
        return client.create_container_repository(
            create_container_repository_details=details
        ).data

    repo = await loop.run_in_executor(None, _recreate)
    return {
        "action": "rollback_delete_ocir_repository",
        "new_repository_id": repo.id,
        "display_name": repo.display_name,
        "rolled_back": True,
    }
