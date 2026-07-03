# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""server_backup executor -- EBS snapshot + S3 manifest backup."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _ec2_client(creds: dict):
    import boto3
    return boto3.client(
        "ec2",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _s3_client(creds: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


async def _load_storage_config(backup_storage_id: str) -> dict:
    """Load BackupStorage config from DB."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.backup_storage import BackupStorage
    async with AsyncSessionLocal() as db:
        bs = await db.get(BackupStorage, _uuid.UUID(backup_storage_id))
        if not bs:
            raise RuntimeError(f"BackupStorage {backup_storage_id} not found")
        return {"storage_type": bs.storage_type, "config": bs.config}


async def _load_aws_creds(aws_connector_id: str, fallback_connector) -> dict:
    """Load AWS credentials from connector record, falling back to the passed connector."""
    creds = getattr(fallback_connector, "credentials", {}) or {}
    if aws_connector_id:
        try:
            import uuid as _uuid
            from app.database import AsyncSessionLocal
            from app.models.connector import Connector as _Connector
            from app.services.connector_service import _attach_credentials
            async with AsyncSessionLocal() as db:
                conn = await db.get(_Connector, _uuid.UUID(aws_connector_id))
                if conn:
                    await _attach_credentials(conn, db)
                    creds = conn.credentials or {}
        except Exception as exc:
            logger.warning("Could not load AWS connector %s: %s", aws_connector_id, exc)
    return creds


async def _do_backup(creds: dict, storage_config: dict, instance_id: str, prefix: str) -> dict:
    """Create EBS snapshots of all volumes attached to the instance and upload a manifest."""
    import json
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    ec2 = _ec2_client(creds)

    def _sync_backup():
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = resp.get("Reservations", [])
        if not reservations:
            raise RuntimeError(f"Instance {instance_id} not found")
        instance = reservations[0]["Instances"][0]
        block_devices = instance.get("BlockDeviceMappings", [])

        snapshot_ids = []
        for bd in block_devices:
            volume_id = bd["Ebs"]["VolumeId"]
            snap_resp = ec2.create_snapshot(
                VolumeId=volume_id,
                Description=f"nexplane-server-backup {instance_id} {datetime.now(timezone.utc).isoformat()}",
                TagSpecifications=[{
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "nexplane:change_type", "Value": "server_backup"},
                        {"Key": "nexplane:instance_id", "Value": instance_id},
                    ],
                }],
            )
            snapshot_ids.append(snap_resp["SnapshotId"])

        if storage_config["storage_type"] == "s3":
            s3_cfg = storage_config["config"]
            bucket = s3_cfg["bucket"]
            manifest_key = f"{prefix}manifest.json"
            manifest = {
                "instance_id": instance_id,
                "snapshot_ids": snapshot_ids,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            s3 = _s3_client(creds)
            s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest))
            return {
                "status": "completed",
                "artifact_refs": {
                    "storage_type": "s3",
                    "bucket_or_path": bucket,
                    "prefix": prefix,
                    "artifacts": {
                        "snapshot_ids": snapshot_ids,
                        "manifest_key": manifest_key,
                    },
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                },
            }

        return {
            "status": "completed",
            "artifact_refs": {
                "storage_type": storage_config["storage_type"],
                "artifacts": {"snapshot_ids": snapshot_ids},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_backup)


async def _delete_s3_prefix(creds: dict, bucket: str, prefix: str) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_delete():
        s3 = _s3_client(creds)
        deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                deleted += len(objects)
        return {"deleted_count": deleted}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_delete)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    aws_connector_id = parameters.get("aws_connector_id", "")
    backup_storage_id = parameters.get("backup_storage_id", "")
    instance_id = parameters.get("instance_id", "")

    if not asset_ids:
        raise RuntimeError("server_backup: no asset_ids provided")

    creds = await _load_aws_creds(aws_connector_id, connector)
    storage_config = await _load_storage_config(backup_storage_id)

    cfg = storage_config.get("config", {})
    prefix = f"{cfg.get('prefix', 'backups/')}{asset_ids[0]}/"

    result = await _do_backup(creds, storage_config, instance_id, prefix)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["_aws_connector_id"] = aws_connector_id
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    artifact_refs = execution_result.get("artifact_refs", {})
    storage_type = artifact_refs.get("storage_type", "s3")
    bucket = artifact_refs.get("bucket_or_path", "")
    prefix = artifact_refs.get("prefix", "")
    artifacts = artifact_refs.get("artifacts", {})
    snapshot_ids = artifacts.get("snapshot_ids", [])

    if not bucket or not prefix:
        return {"rolled_back": False, "reason": "no artifact_refs in execution_result"}

    aws_connector_id = (
        parameters.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)

    # Delete EBS snapshots first
    deleted_snapshots = []
    if snapshot_ids:
        ec2 = _ec2_client(creds)
        for snap_id in snapshot_ids:
            try:
                ec2.delete_snapshot(SnapshotId=snap_id)
                deleted_snapshots.append(snap_id)
            except Exception as exc:
                logger.warning("Failed to delete snapshot %s: %s", snap_id, exc)

    if storage_type == "s3":
        delete_result = await _delete_s3_prefix(creds, bucket, prefix)
        return {
            "rolled_back": True,
            "deleted_snapshots": deleted_snapshots,
            **delete_result,
        }

    return {"rolled_back": False, "reason": f"rollback not implemented for storage_type={storage_type}"}
