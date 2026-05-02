import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "stop_instance", "instance_id": instance_id, "previous_state": "running", "current_state": "stopping"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
    change = resp['StoppingInstances'][0]
    return {
        "action": "stop_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": change['CurrentState']['Name'],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.start_instance import execute as start
    return await start(parameters, [], connector)
