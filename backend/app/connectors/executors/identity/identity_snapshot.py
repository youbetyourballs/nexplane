# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
identity_snapshot — capture full directory state across all identity connectors to S3.

S3 layout:
  {prefix}/identity-snapshot-{timestamp}/
    manifest.json
    {connector_id}/
      users.json
      groups.json
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _discover_users(connector, action: str, params: dict) -> dict:
    from app.services.connector_service import execute_action
    return await execute_action(connector, action, params)


def _s3_put(bucket: str, key: str, body: str, creds: dict | None = None) -> None:
    import boto3
    kwargs: dict = {}
    if creds:
        if creds.get("aws_access_key_id"):
            kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
            kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
        if creds.get("region"):
            kwargs["region_name"] = creds["region"]
    s3 = boto3.client("s3", **kwargs)
    s3.put_object(Bucket=bucket, Key=key, Body=body.encode())


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    bucket = parameters.get("s3_bucket") or creds.get("s3_bucket") or creds.get("bucket", "")
    prefix = parameters.get("s3_prefix") or creds.get("s3_prefix") or creds.get("prefix", "")

    if not bucket:
        return {"status": "failed", "reason": "s3_bucket is required (parameter or connector credential)"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"identity-snapshot-{ts}"
    base_key = f"{prefix.rstrip('/')}/{snapshot_id}"

    try:
        result = await _discover_users(connector, "discover_users", {})
        users = result.get("users") or result.get("accounts") or []
    except Exception as exc:
        return {"status": "failed", "reason": f"discover_users failed: {exc}"}

    groups = []
    try:
        grp_result = await _discover_users(connector, "discover_groups", {})
        groups = grp_result.get("groups") or []
    except Exception:
        pass

    connector_id = str(connector.id)
    connector_type = getattr(connector, "connector_type", "unknown")
    artifact_counts = {"users": len(users), "groups": len(groups)}
    connector_prefix = f"{base_key}/{connector_id}"

    manifest = {
        "format": "identity_snapshot_v1",
        "snapshot_id": snapshot_id,
        "snapshot_timestamp": ts,
        "systems": [connector_type],
        "connector_ids": [connector_id],
        "artifact_counts": artifact_counts,
    }

    try:
        _s3_put(bucket, f"{connector_prefix}/users.json", json.dumps(users, default=str), creds=creds)
        _s3_put(bucket, f"{connector_prefix}/groups.json", json.dumps(groups, default=str), creds=creds)
        _s3_put(bucket, f"{base_key}/manifest.json", json.dumps(manifest, default=str), creds=creds)
    except Exception as exc:
        return {"status": "failed", "reason": f"S3 upload failed: {exc}"}

    return {
        "status": "completed",
        "snapshot_id": snapshot_id,
        "s3_prefix": base_key,
        "artifact_counts": artifact_counts,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_prefix = execution_result.get("s3_prefix", "")
    return {
        "rolled_back": False,
        "reason": (
            f"Snapshots are read-only artifacts and are not deleted on rollback. "
            f"If needed, manually delete S3 objects at: {snapshot_prefix}"
        ),
    }
