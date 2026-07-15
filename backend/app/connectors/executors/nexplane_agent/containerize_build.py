# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor for agent_containerize_build change type."""
from __future__ import annotations
import uuid
from app.database import AsyncSessionLocal
from app.models.asset import Asset

ROLLBACK_CAPABILITY = "full"


async def _load_app_profile(asset_id: str, app_name: str) -> dict | None:
    """Load the app profile from asset_metadata.applications[]."""
    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, uuid.UUID(asset_id))
        if not asset:
            return None
        for app in (asset.asset_metadata or {}).get("applications", []):
            if app.get("name") == app_name:
                return app
    return None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch containerize_build to the registered Nexplane agent.

    Parameters:
      - app_name (str): which application from applications[] to containerize
      - registry (str): image registry prefix
      - namespace (str, optional): k8s namespace (default: "default")
      - cpu_request (str, optional): k8s CPU request (default: "100m")
      - mem_request (str, optional): k8s memory request (default: "128Mi")
      - dry_run (bool, optional): generate artifacts without docker build
    """
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    app_name = parameters.get("app_name", "")
    if not app_name:
        raise ValueError("Missing required parameter: app_name")

    asset_id = asset_ids[0] if asset_ids else None
    if not asset_id:
        raise ValueError("No asset_id provided")

    app_profile = await _load_app_profile(str(asset_id), app_name)
    if app_profile is None:
        raise ValueError(
            f"Application '{app_name}' not found in asset_metadata.applications[]. "
            "Run agent_appdiscovery first."
        )

    agent_params = {
        "app_profile": app_profile,
        "registry": parameters.get("registry", "nexplane-local"),
        "namespace": parameters.get("namespace", "default"),
        "cpu_request": parameters.get("cpu_request", "100m"),
        "mem_request": parameters.get("mem_request", "128Mi"),
        "dry_run": bool(parameters.get("dry_run", False)),
    }

    return await dispatch_agent_job(
        command="containerize_build",
        parameters=agent_params,
        asset_ids=list(asset_ids),
        timeout_seconds=300,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: note the image that should be deleted."""
    image_name = execution_result.get("image_name", "")
    image_digest = execution_result.get("image_digest", "")
    if not image_name or not image_digest:
        return {"rolled_back": False, "note": "No image digest to delete"}
    return {
        "rolled_back": True,
        "note": f"Image {image_name}@{image_digest} should be deleted from registry manually",
        "image_name": image_name,
        "image_digest": image_digest,
    }
