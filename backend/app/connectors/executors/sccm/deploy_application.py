from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Deploy an application to an SCCM collection.

    Parameters
    ----------
    app_name : str
        Exact name of the application as registered in SCCM.
    collection_id : str
        Target collection ID (e.g. "SMS00001").
    deployment_purpose : str
        "Required" (default) or "Available".
    """
    app_name = parameters.get("app_name", "")
    collection_id = parameters.get("collection_id", "")
    deployment_purpose = parameters.get("deployment_purpose", "Required")

    if not app_name:
        raise ValueError("app_name is required")
    if not collection_id:
        raise ValueError("collection_id is required")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "sccm_deploy_application",
            "app_name": app_name,
            "collection_id": collection_id,
            "deployment_purpose": deployment_purpose,
            "deployment_id": "mock-deployment-id",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_sccm_client

    loop = asyncio.get_event_loop()

    def _run():
        client = get_sccm_client(connector)
        return client.deploy_application(app_name, collection_id, deployment_purpose)

    result = await loop.run_in_executor(None, _run)

    deployment_id = (
        result.get("DeploymentID")
        or result.get("deploymentId")
        or result.get("value", {}).get("DeploymentID", "")
    )

    return {
        "action": "sccm_deploy_application",
        "app_name": app_name,
        "collection_id": collection_id,
        "deployment_purpose": deployment_purpose,
        "deployment_id": deployment_id,
        "raw_response": result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Remove the deployment created during execute()."""
    deployment_id = execution_result.get("deployment_id", "")
    if not deployment_id:
        return {"rolled_back": False, "reason": "no deployment_id in execution result"}

    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "rolled_back": True,
            "deployment_id": deployment_id,
            "note": "mock rollback",
        }

    from ._client import get_sccm_client

    loop = asyncio.get_event_loop()

    def _run():
        client = get_sccm_client(connector)
        return client.remove_deployment(deployment_id)

    result = await loop.run_in_executor(None, _run)

    return {
        "rolled_back": True,
        "deployment_id": deployment_id,
        "raw_response": result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
