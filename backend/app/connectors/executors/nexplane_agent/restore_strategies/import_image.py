# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: import a VHD/VMDK from S3 into AWS EC2 as an AMI. Machine tier.

Prerequisites in the target AWS account:
  - IAM service role named ``vmimport`` with trust policy for ``vmie.amazonaws.com``
    and policies granting S3 read + EC2 import/describe/deregister + snapshot delete.
  - The S3 bucket must be accessible by the vmimport role.
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.connectors.executors.nexplane_agent.restore_strategies import (
    _load_source_artifact_refs,
)

logger = logging.getLogger(__name__)

_NAME = "import_image"

_FORMAT_MAP = {
    ".vhd": "VHD",
    ".vhdx": "VHD",
    ".vmdk": "VMDK",
    ".raw": "RAW",
    ".img": "RAW",
}

_POLL_INTERVAL = 30
_TIMEOUT_SECONDS = 7200  # 2 hours; AWS VM Import typically takes 30-90 min


def _ec2_client_from_creds(creds: dict):
    import boto3
    return boto3.client(
        "ec2",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Return (bucket, key) from s3://bucket/key."""
    if not uri.startswith("s3://"):
        raise ValueError(f"import_image: artifact_uri must be an s3:// URI, got {uri!r}")
    path = uri[5:]
    bucket, _, key = path.partition("/")
    if not bucket or not key:
        raise ValueError(f"import_image: malformed s3 URI {uri!r}")
    return bucket, key


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("import_image: source_backup_cr_id is required")

    aws_connector_id = params.get("aws_connector_id", "")
    license_type = params.get("license_type", "BYOL")  # BYOL or AWS
    description = params.get("description", f"nexplane import from {source_backup_cr_id[:8]}")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    artifact_uri = artifact_refs.get("artifact_uri", "")
    if not artifact_uri:
        raise RuntimeError(
            f"import_image: no artifact_uri in source CR {source_backup_cr_id}"
        )

    bucket, key = _parse_s3_uri(artifact_uri)
    suffix = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
    disk_format = _FORMAT_MAP.get(suffix, "VHD")

    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client_from_creds(creds)

    def _sync_import():
        resp = ec2.import_image(
            Description=description,
            DiskContainers=[{
                "Description": description,
                "Format": disk_format,
                "UserBucket": {"S3Bucket": bucket, "S3Key": key},
            }],
            LicenseType=license_type,
        )
        task_id = resp["ImportTaskId"]
        logger.info("import_image: started task %s for %s", task_id, artifact_uri)

        deadline = time.time() + _TIMEOUT_SECONDS
        while time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            tasks = ec2.describe_import_image_tasks(ImportTaskIds=[task_id]).get(
                "ImportImageTasks", []
            )
            if not tasks:
                raise RuntimeError(f"import_image: task {task_id} disappeared")
            task = tasks[0]
            status = task.get("Status", "")
            progress = task.get("Progress", "")
            logger.info("import_image: task %s status=%s progress=%s", task_id, status, progress)
            if status == "completed":
                ami_id = task.get("ImageId", "")
                snapshot_ids = [
                    bd.get("Ebs", {}).get("SnapshotId", "")
                    for bd in task.get("SnapshotDetails", [])
                    if bd.get("Ebs", {}).get("SnapshotId")
                ]
                return task_id, ami_id, snapshot_ids
            if status in ("deleted", "failed"):
                status_msg = task.get("StatusMessage", "no details")
                raise RuntimeError(
                    f"import_image: task {task_id} {status}: {status_msg}"
                )

        raise TimeoutError(
            f"import_image: task {task_id} did not complete within {_TIMEOUT_SECONDS}s"
        )

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        task_id, ami_id, snapshot_ids = await loop.run_in_executor(pool, _sync_import)

    logger.info("import_image: completed — ami=%s snapshots=%s", ami_id, snapshot_ids)
    return {
        "status": "completed",
        "restore_strategy": _NAME,
        "backup_tier": "machine",
        "source_backup_cr_id": source_backup_cr_id,
        "artifact_uri": artifact_uri,
        "import_task_id": task_id,
        "ami_id": ami_id,
        "snapshot_ids": snapshot_ids,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_connector_id": aws_connector_id,
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    """Deregister the imported AMI and delete its backing snapshots."""
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds

    ami_id = execution_result.get("ami_id", "")
    snapshot_ids = execution_result.get("snapshot_ids", [])
    aws_connector_id = (
        params.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )

    if not ami_id:
        return {"rolled_back": False, "reason": "no ami_id in execution_result"}

    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client_from_creds(creds)

    def _sync_cleanup():
        try:
            ec2.deregister_image(ImageId=ami_id)
            logger.info("import_image rollback: deregistered AMI %s", ami_id)
        except Exception as exc:
            if "InvalidAMIID" in str(exc) or "does not exist" in str(exc).lower():
                logger.info("import_image rollback: AMI %s already gone", ami_id)
            else:
                raise

        deleted_snapshots = []
        for snap_id in snapshot_ids:
            if not snap_id:
                continue
            try:
                ec2.delete_snapshot(SnapshotId=snap_id)
                deleted_snapshots.append(snap_id)
                logger.info("import_image rollback: deleted snapshot %s", snap_id)
            except Exception as exc:
                if "InvalidSnapshot" in str(exc) or "does not exist" in str(exc).lower():
                    logger.info("import_image rollback: snapshot %s already gone", snap_id)
                else:
                    raise
        return deleted_snapshots

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        deleted_snapshots = await loop.run_in_executor(pool, _sync_cleanup)

    return {
        "rolled_back": True,
        "deregistered_ami": ami_id,
        "deleted_snapshots": deleted_snapshots,
    }
