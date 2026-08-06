# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: check AWS MGN source server replication status.

Read-only — calls describe_source_servers and returns structured replication
health. Does not launch test instances (that is the backup strategy in
backup_strategies/mgn_replication.py).
"""
import asyncio
import re
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Read-only replication status check — no changes were made"


def _mgn_client(creds: dict):
    import boto3
    return boto3.client(
        "mgn",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _parse_lag(lag_str: str) -> int:
    """Parse ISO 8601 duration string (e.g. 'PT60S', 'PT1H30M') → seconds."""
    if not lag_str:
        return -1
    m = re.match(
        r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        lag_str,
    )
    if not m:
        return -1
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _replication_health(state: str) -> str:
    if state == "CONTINUOUS":
        return "HEALTHY"
    if state in ("PAUSED_AGENT_STALLED", "PAUSED_SNAPSHOT_PENDING", "BACKLOG", "CREATING_SNAPSHOT"):
        return "STALLED"
    return "DISCONNECTED"


def _check_sync(creds: dict, source_server_id: str) -> dict:
    mgn = _mgn_client(creds)
    items = mgn.describe_source_servers(
        filters={"sourceServerIDs": [source_server_id]}
    ).get("items", [])
    if not items:
        raise RuntimeError(f"Source server {source_server_id} not found in MGN")
    server = items[0]
    dr_info = server.get("dataReplicationInfo", {})
    state = dr_info.get("dataReplicationState", "UNKNOWN")
    lag_str = dr_info.get("lagDuration", "")
    lag_seconds = _parse_lag(lag_str)
    return {
        "source_server_id": source_server_id,
        "replication_status": _replication_health(state),
        "raw_state": state,
        "lag_seconds": lag_seconds,
        "lag_duration_raw": lag_str,
        "lifecycle_state": server.get("lifeCycle", {}).get("state", ""),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    source_server_id = parameters.get("mgn_source_server_id", "")
    if not source_server_id:
        raise ValueError("mgn_source_server_id is required")

    aws_connector_id = parameters.get("aws_connector_id", "")
    creds = getattr(connector, "credentials", {}) or {}
    if aws_connector_id:
        from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds
        creds = await _load_aws_creds(aws_connector_id, connector)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check_sync, creds, source_server_id)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "reason": "read-only check — no changes to undo",
    }
