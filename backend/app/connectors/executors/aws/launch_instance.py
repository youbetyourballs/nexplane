# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import string

ROLLBACK_CAPABILITY = "full"


def _mock_instance_id():
    return "i-" + "".join(random.choices(string.hexdigits[:16], k=17))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    ami_id = parameters.get('ami_id', '')
    instance_type = parameters.get('instance_type', 't3.micro')
    subnet_id = parameters.get('subnet_id', '')
    security_group_ids = parameters.get('security_group_ids', [])
    name = parameters.get('name', 'nexplane-instance')
    iam_instance_profile = parameters.get('iam_instance_profile', '')
    key_name = parameters.get('key_name', '')

    if not creds:
        mock_id = _mock_instance_id()
        return {
            "action": "launch_instance",
            "instance_id": mock_id,
            "state": "pending",
            "private_ip": "10.0.1.100",
            "_auto_asset": {
                "name": name,
                "asset_type": "server",
                "environment": "prod",
                "criticality": "medium",
                "asset_metadata": {
                    "instance_id": mock_id,
                    "instance_type": instance_type,
                    "private_ip": "10.0.1.100",
                    "iam_instance_profile": iam_instance_profile,
                    "key_name": key_name,
                },
                "tags": ["ec2", "nexplane-launched"],
            },
        }

    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _call():
        kwargs = dict(
            ImageId=ami_id,
            InstanceType=instance_type,
            SubnetId=subnet_id,
            SecurityGroupIds=security_group_ids,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "ManagedBy", "Value": "nexplane"},
                ],
            }],
        )
        if iam_instance_profile:
            kwargs["IamInstanceProfile"] = {"Name": iam_instance_profile}
        if key_name:
            kwargs["KeyName"] = key_name
        return ec2.run_instances(**kwargs)

    resp = await loop.run_in_executor(None, _call)
    inst = resp['Instances'][0]
    instance_id = inst['InstanceId']
    private_ip = inst.get('PrivateIpAddress')
    return {
        "action": "launch_instance",
        "instance_id": instance_id,
        "state": inst['State']['Name'],
        "private_ip": private_ip,
        "_auto_asset": {
            "name": name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "instance_id": instance_id,
                "instance_type": instance_type,
                "ami_id": ami_id,
                "subnet_id": subnet_id,
                "private_ip": private_ip,
                "iam_instance_profile": iam_instance_profile,
                "key_name": key_name,
            },
            "tags": ["ec2", "nexplane-launched"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_id = execution_result.get('instance_id')
    if not instance_id:
        return {"rolled_back": False, "reason": "no instance_id in execution result"}
    from app.connectors.executors.aws.terminate_instance import execute as terminate
    return await terminate({"instance_id": instance_id, "confirm_terminate": True}, [], connector)
