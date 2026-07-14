# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"


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

    def _capture():
        desc = ec2.describe_instances(InstanceIds=[instance_id])
        instance = desc["Reservations"][0]["Instances"][0]
        return {
            "instance_id": instance_id,
            "image_id": instance["ImageId"],
            "instance_type": instance["InstanceType"],
            "subnet_id": instance.get("SubnetId"),
            "key_name": instance.get("KeyName"),
            "security_group_ids": [sg["GroupId"] for sg in instance.get("SecurityGroups", [])],
            "iam_instance_profile": instance.get("IamInstanceProfile", {}).get("Arn"),
            "tags": instance.get("Tags", []),
            "user_data": ec2.get_attribute(InstanceId=instance_id, Attribute="userData")
                .get("UserData", {}).get("Value"),
        }

    def _terminate():
        resp = ec2.terminate_instances(InstanceIds=[instance_id])
        change = resp['TerminatingInstances'][0]
        waiter = ec2.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
        return change

    # Step 1: capture pre-state (read-only, safe)
    pre_state = await loop.run_in_executor(None, _capture)

    # Step 2: persist pre-state to DB (must succeed before destruction)
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            pre_state,
        )
        await db.commit()

    # Step 3: only after DB write succeeds, terminate
    change = await loop.run_in_executor(None, _terminate)

    return {
        "action": "terminate_instance",
        "instance_id": instance_id,
        "previous_state": change['PreviousState']['Name'],
        "current_state": "terminated",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        pre_state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
        )
    if not pre_state:
        return {"rolled_back": False, "reason": "no pre-state captured — cannot reconstitute instance"}

    creds = getattr(connector, 'credentials', {})
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _launch():
        launch_kwargs = {
            "ImageId": pre_state["image_id"],
            "InstanceType": pre_state["instance_type"],
            "MinCount": 1,
            "MaxCount": 1,
        }
        if pre_state.get("subnet_id"):
            launch_kwargs["SubnetId"] = pre_state["subnet_id"]
        if pre_state.get("key_name"):
            launch_kwargs["KeyName"] = pre_state["key_name"]
        if pre_state.get("security_group_ids"):
            launch_kwargs["SecurityGroupIds"] = pre_state["security_group_ids"]
        if pre_state.get("iam_instance_profile"):
            launch_kwargs["IamInstanceProfile"] = {"Arn": pre_state["iam_instance_profile"]}
        if pre_state.get("tags"):
            launch_kwargs["TagSpecifications"] = [{"ResourceType": "instance", "Tags": pre_state["tags"]}]
        if pre_state.get("user_data"):
            import base64
            launch_kwargs["UserData"] = base64.b64decode(pre_state["user_data"]).decode()
        resp = ec2.run_instances(**launch_kwargs)
        return resp["Instances"][0]["InstanceId"]

    new_id = await loop.run_in_executor(None, _launch)
    return {
        "rolled_back": True,
        "new_instance_id": new_id,
        "note": "Replacement instance launched from pre-state config. Original instance ID cannot be restored.",
    }
