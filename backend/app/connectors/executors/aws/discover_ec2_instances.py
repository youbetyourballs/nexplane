import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_ec2_instances",
        "assets": [
            {"id": "i-mock001", "name": "web-server-01", "asset_type": "server", "asset_metadata": {"instance_id": "i-mock001", "instance_type": "t3.medium", "state": "running", "public_ip": "54.1.2.3", "private_ip": "10.0.1.10", "ami_id": "ami-0abc123", "region": "us-east-1"}},
            {"id": "i-mock002", "name": "app-server-01", "asset_type": "server", "asset_metadata": {"instance_id": "i-mock002", "instance_type": "t3.large", "state": "running", "public_ip": None, "private_ip": "10.0.1.11", "ami_id": "ami-0abc456", "region": "us-east-1"}},
        ],
        "_auto_asset": {
            "name": "AWS Account 123456789012",
            "asset_type": "cloud_account",
            "asset_metadata": {"account_id": "123456789012", "provider": "aws"},
            "tags": [],
        },
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    sts = get_boto3_client(creds, 'sts')
    loop = asyncio.get_event_loop()

    def _call():
        identity = sts.get_caller_identity()
        account_id = identity['Account']

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
                        "asset_metadata": {
                            "instance_id": inst['InstanceId'],
                            "instance_type": inst.get('InstanceType'),
                            "state": inst['State']['Name'],
                            "public_ip": inst.get('PublicIpAddress'),
                            "private_ip": inst.get('PrivateIpAddress'),
                            "ami_id": inst.get('ImageId'),
                            "region": creds.get('region', 'us-east-1'),
                            "subnet_id": inst.get('SubnetId'),
                            "vpc_id": inst.get('VpcId'),
                        },
                    })
        return assets, account_id

    assets, account_id = await loop.run_in_executor(None, _call)
    return {
        "action": "discover_ec2_instances",
        "assets": assets,
        "_auto_asset": {
            "name": f"AWS Account {account_id}",
            "asset_type": "cloud_account",
            "asset_metadata": {"account_id": account_id, "provider": "aws", "region": creds.get("region", "us-east-1")},
            "tags": [],
        },
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
