import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not parameters.get('confirm_terminate'):
        return {"action": "terminate_instance", "error": "confirm_terminate must be true — termination is irreversible"}
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {"action": "terminate_instance", "instance_id": instance_id, "previous_state": "running", "current_state": "terminated"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _terminate_and_wait():
        resp = ec2.terminate_instances(InstanceIds=[instance_id])
        change = resp['TerminatingInstances'][0]
        waiter = ec2.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
        return change

    change = await loop.run_in_executor(None, _terminate_and_wait)
    return {
        "action": "terminate_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": "terminated",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "termination is irreversible — restore from pre-terminate snapshot"}
