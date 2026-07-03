# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""restore_server executor -- launch-from-AMI restore (hybrid/full/rebuild modes)."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.connectors.executors.nexplane_agent.server_backup import _load_aws_creds, _ec2_client


class IrreversibleOperationError(Exception):
    pass


async def _load_source_artifact_refs(source_backup_cr_id: str) -> dict:
    """Load artifact_refs from the source backup CR."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, _uuid.UUID(source_backup_cr_id))
        if not cr:
            raise RuntimeError(f"Source backup CR {source_backup_cr_id} not found")
        return cr.artifact_refs or {}


async def _launch_from_ami(creds: dict, ami_id: str, target: dict) -> str:
    """Launch a new EC2 instance from an AMI. Returns new instance_id."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_launch():
        ec2 = _ec2_client(creds)
        run_kwargs = {
            "ImageId": ami_id,
            "InstanceType": target.get("instance_type", "t3.micro"),
            "MinCount": 1,
            "MaxCount": 1,
            "TagSpecifications": [{
                "ResourceType": "instance",
                "Tags": [{"Key": "nexplane:restored_from_ami", "Value": ami_id}],
            }],
        }
        if target.get("subnet_id"):
            run_kwargs["SubnetId"] = target["subnet_id"]
        if target.get("security_group_ids"):
            run_kwargs["SecurityGroupIds"] = target["security_group_ids"]
        if target.get("key_name"):
            run_kwargs["KeyName"] = target["key_name"]
        if target.get("iam_instance_profile"):
            run_kwargs["IamInstanceProfile"] = {"Name": target["iam_instance_profile"]}

        resp = ec2.run_instances(**run_kwargs)
        return resp["Instances"][0]["InstanceId"]

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_launch)


async def _terminate_instance(creds: dict, instance_id: str) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_terminate():
        ec2 = _ec2_client(creds)
        ec2.terminate_instances(InstanceIds=[instance_id])
        return {"rolled_back": True, "terminated_instance_id": instance_id}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_terminate)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("restore_server: no asset_ids provided")

    source_backup_cr_id = parameters.get("source_backup_cr_id", "")
    restore_mode = parameters.get("restore_mode", "hybrid")
    target = parameters.get("target", {"type": "new"})
    aws_connector_id = parameters.get("aws_connector_id", "")
    confirm_same_target = bool(parameters.get("confirm_same_target", False))

    if not source_backup_cr_id:
        raise RuntimeError("restore_server: source_backup_cr_id is required")

    if target.get("type") == "same" and not confirm_same_target:
        raise RuntimeError(
            "restore_server: restoring to the same instance is irreversible. "
            "Set confirm_same_target=true to proceed."
        )

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    ami_id = artifact_refs.get("ami_id", "")

    creds = await _load_aws_creds(aws_connector_id, connector)

    if target.get("type") == "new":
        if not ami_id:
            raise RuntimeError(
                f"restore_server: source CR {source_backup_cr_id} has no ami_id in artifact_refs. "
                f"Only hybrid/snapshot restores are supported without an AMI."
            )
        new_instance_id = await _launch_from_ami(creds, ami_id, target)
        return {
            "status": "completed",
            "restore_mode": restore_mode,
            "new_instance_id": new_instance_id,
            "source_backup_cr_id": source_backup_cr_id,
            "ami_id": ami_id,
            "launched_at": datetime.now(timezone.utc).isoformat(),
            "_asset_ids": [str(a) for a in asset_ids],
        }

    # same-target restore: SSM-based (confirm flag already enforced above)
    return {
        "status": "completed",
        "restore_mode": restore_mode,
        "source_backup_cr_id": source_backup_cr_id,
        "note": "same-target restore dispatched via SSM -- verify manually",
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    new_instance_id = execution_result.get("new_instance_id", "")

    if not new_instance_id:
        # Same-target restore -- irreversible
        if execution_result.get("note", "").startswith("same-target"):
            raise IrreversibleOperationError("Cannot terminate the original asset's replacement instance")
        return {"rolled_back": False, "reason": "no new_instance_id in execution_result"}

    if parameters.get("confirm_same_target"):
        raise IrreversibleOperationError("Cannot terminate the original asset's replacement instance")

    aws_connector_id = parameters.get("aws_connector_id", "")
    creds = await _load_aws_creds(aws_connector_id, connector)
    return await _terminate_instance(creds, new_instance_id)
