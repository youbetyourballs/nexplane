# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: launch a new EC2 instance from an AMI."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _load_source_artifact_refs(source_backup_cr_id: str) -> dict:
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest
    from app.models.execution_run import ExecutionRun
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, _uuid.UUID(source_backup_cr_id))
        if not cr:
            raise RuntimeError(f"Source backup CR {source_backup_cr_id} not found")
        if cr.artifact_refs:
            return cr.artifact_refs
        result = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr.id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        er = result.scalar_one_or_none()
        if not er or not er.result:
            return {}
        for step in (er.result.get("execution") or {}).get("steps", []):
            refs = (step.get("result") or {}).get("artifact_refs")
            if refs:
                return refs
        return {}


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client

    if not asset_ids:
        raise RuntimeError("launch_ami: no asset_ids provided")

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    restore_mode = params.get("restore_mode", "hybrid")
    target = params.get("target", {"type": "new"})
    aws_connector_id = params.get("aws_connector_id", "")

    if not source_backup_cr_id:
        raise RuntimeError("launch_ami: source_backup_cr_id is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    ami_id = artifact_refs.get("ami_id", "")
    if not ami_id:
        raise RuntimeError(
            f"launch_ami: source CR {source_backup_cr_id} has no ami_id in artifact_refs"
        )

    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client(creds)

    def _sync_launch():
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

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        new_instance_id = await loop.run_in_executor(pool, _sync_launch)

    return {
        "status": "completed",
        "restore_mode": restore_mode,
        "new_instance_id": new_instance_id,
        "source_backup_cr_id": source_backup_cr_id,
        "ami_id": ami_id,
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_connector_id": aws_connector_id,
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client

    new_instance_id = execution_result.get("new_instance_id", "")
    if not new_instance_id:
        return {"rolled_back": False, "reason": "no new_instance_id in execution_result"}

    aws_connector_id = (
        params.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client(creds)

    def _sync_terminate():
        ec2.terminate_instances(InstanceIds=[new_instance_id])
        return {"rolled_back": True, "terminated_instance_id": new_instance_id}

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_terminate)
