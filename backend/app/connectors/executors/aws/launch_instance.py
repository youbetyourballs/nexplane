import asyncio
import random
import string


def _mock_instance_id():
    return "i-" + "".join(random.choices(string.hexdigits[:16], k=17))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    ami_id = parameters.get('ami_id', '')
    instance_type = parameters.get('instance_type', 't2.micro')
    subnet_id = parameters.get('subnet_id', '')
    security_group_ids = parameters.get('security_group_ids', [])
    name = parameters.get('name', 'nexplane-instance')
    if not creds:
        mock_id = _mock_instance_id()
        return {"action": "launch_instance", "instance_id": mock_id, "state": "pending", "private_ip": "10.0.1.100"}
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: ec2.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        SubnetId=subnet_id,
        SecurityGroupIds=security_group_ids,
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": name}, {"Key": "ManagedBy", "Value": "nexplane"}],
        }],
    ))
    inst = resp['Instances'][0]
    return {
        "action": "launch_instance",
        "instance_id": inst['InstanceId'],
        "state": inst['State']['Name'],
        "private_ip": inst.get('PrivateIpAddress'),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_id = execution_result.get('instance_id')
    if not instance_id:
        return {"rolled_back": False, "reason": "no instance_id in execution result"}
    from app.connectors.executors.aws.terminate_instance import execute as terminate
    return await terminate({"instance_id": instance_id, "confirm_terminate": True}, [], connector)
