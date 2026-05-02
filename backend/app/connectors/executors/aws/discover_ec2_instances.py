import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_ec2_instances",
        "assets": [
            {"id": "i-mock001", "name": "web-server-01", "asset_type": "server", "metadata": {"instance_type": "t3.medium", "state": "running", "public_ip": "54.1.2.3", "ami_id": "ami-0abc123"}},
            {"id": "i-mock002", "name": "app-server-01", "asset_type": "server", "metadata": {"instance_type": "t3.large", "state": "running", "public_ip": None, "ami_id": "ami-0abc456"}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()

    def _call():
        paginator = ec2.get_paginator('describe_instances')
        assets = []
        for page in paginator.paginate():
            for reservation in page['Reservations']:
                for inst in reservation['Instances']:
                    name = next((t['Value'] for t in inst.get('Tags', []) if t['Key'] == 'Name'), inst['InstanceId'])
                    assets.append({
                        "id": inst['InstanceId'],
                        "name": name,
                        "asset_type": "server",
                        "metadata": {
                            "instance_type": inst.get('InstanceType'),
                            "state": inst['State']['Name'],
                            "public_ip": inst.get('PublicIpAddress'),
                            "ami_id": inst.get('ImageId'),
                            "region": creds.get('region', 'us-east-1'),
                        },
                    })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_ec2_instances", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
