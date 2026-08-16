# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP Artifact Registry repository create executor.

Creates a new Artifact Registry repository (Docker, Maven, npm, etc.).
Rollback: delete the repository (only safe if no images have been pushed).
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    location = parameters["location"]
    repository_id = parameters["repository_id"]
    fmt = parameters.get("format", "DOCKER").upper()
    description = parameters.get("description", f"Nexplane-managed {fmt} repository")

    def _create():
        from google.cloud import artifactregistry_v1
        from app.connectors.executors.gcp._client import get_credentials, get_project_id
        credentials = get_credentials(creds)
        project_id = get_project_id(creds)
        client = artifactregistry_v1.ArtifactRegistryClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/{location}"
        repo = artifactregistry_v1.Repository(
            format_=getattr(artifactregistry_v1.Repository.Format, fmt),
            description=description,
        )
        op = client.create_repository(parent=parent, repository_id=repository_id, repository=repo)
        return op.result(timeout=120)

    from google.api_core.exceptions import AlreadyExists

    class _RepoStub:
        """Minimal stand-in when the repo already exists and we skip a second API call."""
        def __init__(self, name):
            self.name = name

    already_exists = False
    try:
        repo = await _run(_create)
    except AlreadyExists:
        already_exists = True
        # Derive the repo name from known parameters — avoids a second SDK round-trip
        # and keeps the test boundary clean (only one _run call).
        from app.connectors.executors.gcp._client import get_project_id
        project_id = get_project_id(creds)
        repo_name = f"projects/{project_id}/locations/{location}/repositories/{repository_id}"
        repo = _RepoStub(repo_name)

    logger.info("gcp_artifact_registry_create: repo %s (already_existed=%s)", repo.name, already_exists)

    return {
        "status": "created",
        "already_exists": already_exists,
        "repository_name": repo.name,
        "location": location,
        "format": fmt,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    if execution_result.get("already_exists"):
        return {"rolled_back": False, "reason": "Repository already existed before execution — not deleting"}

    creds = connector.credentials
    repository_name = execution_result["repository_name"]

    def _delete():
        from google.cloud import artifactregistry_v1
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = artifactregistry_v1.ArtifactRegistryClient(credentials=credentials)
        op = client.delete_repository(name=repository_name)
        op.result(timeout=120)

    await _run(_delete)
    logger.info("gcp_artifact_registry_create rollback: deleted %s", repository_name)

    return {
        "rolled_back": True,
        "repository_name": repository_name,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
