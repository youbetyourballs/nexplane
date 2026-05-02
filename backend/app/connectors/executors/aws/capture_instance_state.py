import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    if not creds:
        return {
            "action": "capture_instance_state",
            "instance_id": instance_id or "i-mock000000000000",
            "state": "running",
            "public_ip": "1.2.3.4",
            "private_ip": "10.0.0.1",
            "ami_id": "ami-0abcdef1234567890",
            "instance_type": "t2.micro",
            "security_groups": ["sg-mock000000000000"],
            "subnet_id": "subnet-mock0000000000",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[instance_id]))
    reservations = resp.get('Reservations', [])
    if not reservations:
        return {"action": "capture_instance_state", "error": f"Instance {instance_id} not found"}
    inst = reservations[0]['Instances'][0]
    return {
        "action": "capture_instance_state",
        "instance_id": inst['InstanceId'],
        "state": inst['State']['Name'],
        "public_ip": inst.get('PublicIpAddress'),
        "private_ip": inst.get('PrivateIpAddress'),
        "ami_id": inst.get('ImageId'),
        "instance_type": inst.get('InstanceType'),
        "security_groups": [sg['GroupId'] for sg in inst.get('SecurityGroups', [])],
        "subnet_id": inst.get('SubnetId'),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture has no rollback"}
