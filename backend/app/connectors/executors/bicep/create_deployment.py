import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    deployment_name = parameters["deployment_name"]
    template = parameters["template"]
    deploy_params = parameters.get("parameters", {})

    if not creds:
        return {"action": "create_deployment", "deployment_name": deployment_name, "status": "Succeeded"}

    from ._client import arm_put, arm_get
    sub = creds["subscription_id"]
    path = f"/subscriptions/{sub}/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}"
    body = {
        "properties": {
            "mode": "Incremental",
            "template": template,
            "parameters": deploy_params,
        }
    }
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: arm_put(creds, path, body))

    # Poll until terminal state (max 10 min)
    deadline = time.time() + 600
    while time.time() < deadline:
        state = await loop.run_in_executor(None, lambda: arm_get(creds, path))
        status = state.get("properties", {}).get("provisioningState", "")
        if status in ("Succeeded", "Failed", "Canceled"):
            return {"action": "create_deployment", "deployment_name": deployment_name, "status": status}
        await asyncio.sleep(10)

    return {"action": "create_deployment", "deployment_name": deployment_name, "status": "Unknown"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "deployment rollback requires creating a new deployment with previous template"}
