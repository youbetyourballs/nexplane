import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    target_state = parameters.get('target_state', 'running')
    if not creds:
        return {"action": "wait_instance_state", "instance_id": instance_id, "reached_state": target_state, "elapsed_seconds": 0}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    start = time.time()
    for attempt in range(20):
        try:
            resp = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[instance_id]))
        except Exception as e:
            if 'InvalidInstanceID' in str(e) and attempt < 5:
                await asyncio.sleep(15)
                continue
            raise
        reservations = resp.get('Reservations', [])
        if reservations:
            state = reservations[0]['Instances'][0]['State']['Name']
            if state == target_state:
                return {
                    "action": "wait_instance_state",
                    "instance_id": instance_id,
                    "reached_state": state,
                    "elapsed_seconds": int(time.time() - start),
                }
        await asyncio.sleep(15)
    raise TimeoutError(f"Instance {instance_id} did not reach state '{target_state}' within 5 minutes")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait step has no rollback"}
