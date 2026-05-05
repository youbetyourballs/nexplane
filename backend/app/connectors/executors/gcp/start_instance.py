import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]
    if not creds:
        return {"action": "start_instance", "instance_name": instance_name, "zone": zone, "status": "STAGING"}
    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_running_loop()
    client = compute_v1.InstancesClient(credentials=credentials)
    op = await loop.run_in_executor(None, lambda: client.start(project=project, zone=zone, instance=instance_name))
    return {"action": "start_instance", "instance_name": instance_name, "zone": zone, "operation": op.name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.stop_instance import execute as stop
    return await stop(parameters, [], connector)
