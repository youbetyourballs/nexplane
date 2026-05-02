import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "start_instance", "instance_id": instance_id, "previous_state": "stopped", "current_state": "pending"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
    change = resp['StartingInstances'][0]
    return {
        "action": "start_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": change['CurrentState']['Name'],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.stop_instance import execute as stop
    return await stop(parameters, [], connector)
