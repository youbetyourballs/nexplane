# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""server_capture executor -- forensic capture: AMI + SSM metadata + S3 upload."""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _s3_client
from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config
from app.connectors.executors.nexplane_agent.server_snapshot import _do_snapshot, _deregister_ami


async def _delete_s3_prefix(creds: dict, bucket: str, prefix: str) -> None:
    """Delete all objects under prefix from S3 (best-effort)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_delete():
        s3 = _s3_client(creds)
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync_delete)


def _ssm_client(creds: dict):
    import boto3
    return boto3.client(
        "ssm",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _run_ssm_command(ssm, instance_id: str, commands: list, timeout: int = 60) -> str:
    """Run shell commands via SSM and return stdout. Raises on failure."""
    import time
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
        TimeoutSeconds=timeout,
    )
    command_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout + 10
    while time.time() < deadline:
        time.sleep(2)
        inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        status = inv["Status"]
        if status == "Success":
            return inv.get("StandardOutputContent", "")
        if status in ("Failed", "Cancelled", "TimedOut"):
            raise RuntimeError(
                f"SSM command failed ({status}): {inv.get('StandardErrorContent', '')}"
            )
    raise TimeoutError(f"SSM command timed out after {timeout}s")


async def _do_capture(creds: dict, storage_config: dict, instance_id: str, prefix: str) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_capture():
        ssm = _ssm_client(creds)
        s3 = _s3_client(creds)
        cfg = storage_config.get("config", {})
        bucket = cfg.get("bucket", "")

        # Collect live metadata via SSM — four separate commands, stored as separate files
        ssm_tasks = {
            "processes.json": ["ps aux"],
            "netstat.json": ["ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null || echo '{}'"],
            "lsmod.json": ["lsmod 2>/dev/null || echo '{}'"],
            "infra.json": ["uname -r && cat /etc/os-release"],
        }
        artifact_map = {}
        for filename, commands in ssm_tasks.items():
            try:
                output = _run_ssm_command(ssm, instance_id, commands)
                key = f"{prefix}{filename}"
                s3.put_object(Bucket=bucket, Key=key, Body=output.encode())
                artifact_map[filename] = key
            except Exception as exc:
                logger.warning("SSM capture %s failed: %s", filename, exc)
                artifact_map[filename] = ""

        return {
            "bucket": bucket,
            "artifacts": {
                "process_list": artifact_map.get("processes.json", ""),
                "network_state": artifact_map.get("netstat.json", ""),
                "kernel_modules": artifact_map.get("lsmod.json", ""),
                "infra_config": artifact_map.get("infra.json", ""),
            },
        }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        ssm_result = await loop.run_in_executor(pool, _sync_capture)

    # AMI snapshot (reuses server_snapshot logic)
    snapshot_result = await _do_snapshot(creds, instance_id, no_reboot=True)
    ami_id = snapshot_result["artifact_refs"]["ami_id"]
    snapshot_ids = snapshot_result["artifact_refs"]["snapshot_ids"]

    return {
        "status": "completed",
        "artifact_refs": {
            "ami_id": ami_id,
            "snapshot_ids": snapshot_ids,
            "storage_type": storage_config["storage_type"],
            "bucket_or_path": ssm_result["bucket"],
            "prefix": prefix,
            "artifacts": ssm_result["artifacts"],
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("server_capture: no asset_ids provided")

    aws_connector_id = parameters.get("aws_connector_id", "")
    backup_storage_id = parameters.get("backup_storage_id", "")
    instance_id = parameters.get("instance_id", "")

    creds = await _load_aws_creds(aws_connector_id, connector)
    storage_config = await _load_storage_config(backup_storage_id)
    cfg = storage_config.get("config", {})
    prefix = f"{cfg.get('prefix', 'captures/')}{asset_ids[0]}/"

    result = await _do_capture(creds, storage_config, instance_id, prefix)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["_aws_connector_id"] = aws_connector_id
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    artifact_refs = execution_result.get("artifact_refs", {})
    ami_id = artifact_refs.get("ami_id", "")
    snapshot_ids = artifact_refs.get("snapshot_ids", [])
    bucket = artifact_refs.get("bucket_or_path", "")
    prefix = artifact_refs.get("prefix", "")

    aws_connector_id = (
        parameters.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)

    errors = []

    # Delete S3 metadata artifacts
    if bucket and prefix:
        try:
            await _delete_s3_prefix(creds, bucket, prefix)
        except Exception as exc:
            errors.append(f"S3 delete failed: {exc}")

    # Deregister AMI + associated EBS snapshots
    if ami_id:
        try:
            await _deregister_ami(creds, ami_id, snapshot_ids)
        except Exception as exc:
            errors.append(f"AMI deregister failed: {exc}")

    return {"rolled_back": not errors, "errors": errors}
