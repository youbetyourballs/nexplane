# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: EBS snapshot + AMI (AWS EC2). Machine tier."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_storage_backend(storage_type: str):
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend
    return get_backend(storage_type)


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config

    aws_connector_id = params.get("aws_connector_id", "")
    backup_storage_id = params.get("backup_storage_id", "")
    instance_id = params.get("instance_id", "")

    if not asset_ids:
        raise RuntimeError("ebs_snapshot: no asset_ids provided")

    creds = await _load_aws_creds(aws_connector_id, connector)

    # Allow pre-resolved storage config to be injected (useful in tests)
    storage_config = params.get("_storage_config") or await _load_storage_config(backup_storage_id)
    cfg = storage_config.get("config", {})
    prefix = f"{cfg.get('prefix', 'backups/')}{asset_ids[0]}/"

    ec2 = _ec2_client(creds)

    def _sync_snapshot():
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = resp.get("Reservations", [])
        if not reservations:
            raise RuntimeError(f"ebs_snapshot: instance {instance_id} not found")
        instance = reservations[0]["Instances"][0]
        block_devices = instance.get("BlockDeviceMappings", [])
        snapshot_ids = []
        for bd in block_devices:
            volume_id = bd["Ebs"]["VolumeId"]
            snap = ec2.create_snapshot(
                VolumeId=volume_id,
                Description=f"nexplane-ebs-snapshot {instance_id} {datetime.now(timezone.utc).isoformat()}",
                TagSpecifications=[{
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "nexplane:change_type", "Value": "server_backup"},
                        {"Key": "nexplane:instance_id", "Value": instance_id},
                    ],
                }],
            )
            snapshot_ids.append(snap["SnapshotId"])
        return snapshot_ids

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        snapshot_ids = await loop.run_in_executor(pool, _sync_snapshot)

    storage_type = storage_config["storage_type"]
    backend = _get_storage_backend(storage_type)
    manifest_key = f"{prefix}manifest.json"
    captured_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "instance_id": instance_id,
        "snapshot_ids": snapshot_ids,
        "captured_at": captured_at,
    }
    manifest_uri = await backend._put(manifest_key, json.dumps(manifest).encode(), cfg)

    artifact_refs = {
        "capture_strategy": "ebs_snapshot",
        "restore_strategy": "launch_ami",
        "backup_tier": "machine",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "bucket_or_path": cfg.get("bucket", cfg.get("path", "")),
        "prefix": prefix,
        "artifacts": {
            "snapshot_ids": snapshot_ids,
            "manifest_key": manifest_key,
            "manifest_uri": manifest_uri,
        },
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_connector_id": aws_connector_id,
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    artifact_refs = execution_result.get("artifact_refs", {})
    storage_type = artifact_refs.get("storage_type", "s3")
    prefix = artifact_refs.get("prefix", "")
    artifacts = artifact_refs.get("artifacts", {})
    snapshot_ids = artifacts.get("snapshot_ids", [])

    aws_connector_id = (
        params.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client(creds)

    loop = asyncio.get_running_loop()
    deleted_snapshots = []
    for snap_id in snapshot_ids:
        try:
            with ThreadPoolExecutor() as pool:
                await loop.run_in_executor(pool, lambda: ec2.delete_snapshot(SnapshotId=snap_id))
            deleted_snapshots.append(snap_id)
        except Exception as exc:
            logger.warning("Failed to delete snapshot %s: %s", snap_id, exc)

    bucket = artifact_refs.get("bucket_or_path", "")
    if storage_type == "s3" and bucket and prefix:
        cfg = {"bucket": bucket, "region": creds.get("region", "us-east-1")}
        cfg["aws_access_key_id"] = creds.get("access_key_id", creds.get("aws_access_key_id"))
        cfg["aws_secret_access_key"] = creds.get("secret_access_key", creds.get("aws_secret_access_key"))
        cfg["aws_session_token"] = creds.get("session_token")
        backend = get_backend("s3")
        delete_result = await backend._delete_prefix(prefix, cfg)
        return {
            "rolled_back": True,
            "deleted_snapshots": deleted_snapshots,
            **delete_result,
        }

    return {
        "rolled_back": bool(deleted_snapshots),
        "deleted_snapshots": deleted_snapshots,
        "reason": f"storage_type={storage_type} rollback not implemented" if not deleted_snapshots else None,
    }
