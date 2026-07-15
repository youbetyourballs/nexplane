# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


_QUICK_AMI_FILTERS = {
    "amazon_linux": [
        {"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]},
        {"Name": "owner-alias", "Values": ["amazon"]},
        {"Name": "state", "Values": ["available"]},
    ],
    "ubuntu": [
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]},
        {"Name": "architecture", "Values": ["x86_64"]},
        {"Name": "state", "Values": ["available"]},
    ],
}

# Canonical's official owner ID for free Ubuntu AMIs (excludes Marketplace opt-in AMIs)
_QUICK_AMI_OWNERS = {
    "amazon_linux": [],  # filter uses owner-alias
    "ubuntu": ["099720109477"],
}

_MOCK_AMIS = {
    "amazon_linux": "ami-0abcdef1234567890",
    "ubuntu": "ami-0fedcba9876543210",
}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    mode = parameters.get('mode', 'quick')
    iam_instance_profile = parameters.get('iam_instance_profile', '')
    key_name = parameters.get('key_name', '')

    if mode == 'spec':
        required = ['ami_id', 'instance_type', 'subnet_id', 'security_group_ids', 'name']
        missing = [f for f in required if not parameters.get(f)]
        if missing:
            return {"action": "resolve_launch_config", "error": f"Missing required spec fields: {missing}"}
        return {
            "action": "resolve_launch_config",
            "mode": "spec",
            "ami_id": parameters['ami_id'],
            "instance_type": parameters['instance_type'],
            "subnet_id": parameters['subnet_id'],
            "security_group_ids": parameters['security_group_ids'],
            "name": parameters['name'],
            "iam_instance_profile": iam_instance_profile,
            "key_name": key_name,
        }

    if mode == 'clone':
        source_id = parameters.get('source_instance_id')
        if not source_id:
            return {"action": "resolve_launch_config", "error": "clone mode requires source_instance_id"}
        if not creds:
            return {
                "action": "resolve_launch_config",
                "mode": "clone",
                "ami_id": "ami-0abcdef1234567890",
                "instance_type": "t3.micro",
                "subnet_id": "subnet-mock0000000000",
                "security_group_ids": ["sg-mock000000000000"],
                "name": parameters.get('name', f"clone-of-{source_id}"),
                "iam_instance_profile": iam_instance_profile,
                "key_name": key_name,
            }
        from ._client import get_ec2_client
        ec2 = get_ec2_client(creds)
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: ec2.describe_instances(InstanceIds=[source_id]))
        if not resp.get('Reservations'):
            return {"action": "resolve_launch_config", "error": f"Source instance {source_id} not found"}
        inst = resp['Reservations'][0]['Instances'][0]
        return {
            "action": "resolve_launch_config",
            "mode": "clone",
            "ami_id": inst['ImageId'],
            "instance_type": inst['InstanceType'],
            "subnet_id": inst['SubnetId'],
            "security_group_ids": [sg['GroupId'] for sg in inst.get('SecurityGroups', [])],
            "name": parameters.get('name', f"clone-of-{source_id}"),
            "iam_instance_profile": iam_instance_profile,
            "key_name": key_name,
        }

    # quick mode
    os_family = parameters.get('os', 'amazon_linux')
    name = parameters.get('name', 'nexplane-instance')
    if not creds:
        return {
            "action": "resolve_launch_config",
            "mode": "quick",
            "ami_id": _MOCK_AMIS.get(os_family, _MOCK_AMIS["amazon_linux"]),
            "instance_type": "t3.micro",
            "subnet_id": "subnet-mock0000000000",
            "security_group_ids": ["sg-mock000000000000"],
            "name": name,
            "iam_instance_profile": iam_instance_profile,
            "key_name": key_name,
        }
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    filters = _QUICK_AMI_FILTERS.get(os_family, _QUICK_AMI_FILTERS["amazon_linux"])
    owners = _QUICK_AMI_OWNERS.get(os_family, [])
    if owners:
        imgs = await loop.run_in_executor(None, lambda: ec2.describe_images(Owners=owners, Filters=filters))
    else:
        imgs = await loop.run_in_executor(None, lambda: ec2.describe_images(Filters=filters))
    images = sorted(imgs.get('Images', []), key=lambda i: i['CreationDate'], reverse=True)
    ami_id = images[0]['ImageId'] if images else _MOCK_AMIS.get(os_family, _MOCK_AMIS["amazon_linux"])
    vpcs = await loop.run_in_executor(None, lambda: ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]))
    vpc_id = vpcs['Vpcs'][0]['VpcId'] if vpcs.get('Vpcs') else None
    subnets = await loop.run_in_executor(None, lambda: ec2.describe_subnets(Filters=[{"Name": "vpcId", "Values": [vpc_id]}])) if vpc_id else {"Subnets": []}
    subnet_id = subnets['Subnets'][0]['SubnetId'] if subnets.get('Subnets') else 'subnet-default'
    sgs = await loop.run_in_executor(None, lambda: ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "group-name", "Values": ["default"]}])) if vpc_id else {"SecurityGroups": []}
    sg_ids = [sg['GroupId'] for sg in sgs.get('SecurityGroups', [])] or ['sg-default']
    return {
        "action": "resolve_launch_config",
        "mode": "quick",
        "ami_id": ami_id,
        "instance_type": "t3.micro",
        "subnet_id": subnet_id,
        "security_group_ids": sg_ids,
        "name": name,
        "iam_instance_profile": iam_instance_profile,
        "key_name": key_name,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "resolve_launch_config has no rollback"}
