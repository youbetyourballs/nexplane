# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    log_group_name = parameters.get("log_group_name", "nexplane-logs")
    log_name = parameters.get("log_name", "nexplane-audit-log")
    log_type = parameters.get("log_type", "AUDIT")
    is_enabled = parameters.get("is_enabled", True)
    retention_duration = parameters.get("retention_duration", 30)

    if not creds:
        return {
            "action": "enable_logging",
            "log_group_id": "mock-log-group-id",
            "log_id": "mock-log-id",
            "mock": True,
        }

    import oci
    from ._client import get_logging_client
    log_client = get_logging_client(creds)
    loop = asyncio.get_running_loop()

    def _create_group():
        details = oci.logging.models.CreateLogGroupDetails(
            compartment_id=compartment_id,
            display_name=log_group_name,
        )
        response = log_client.create_log_group(create_log_group_details=details)
        # OCI may return 202 with a work request; poll list_log_groups to get the ID
        if response.data and getattr(response.data, "id", None):
            return response.data
        # Work request path — poll until the log group appears
        import time
        for _ in range(30):
            time.sleep(5)
            groups = log_client.list_log_groups(compartment_id=compartment_id).data
            for g in groups:
                if g.display_name == log_group_name:
                    return g
        raise RuntimeError(f"Log group '{log_group_name}' not found after creation")

    log_group = await loop.run_in_executor(None, _create_group)
    log_group_id = log_group.id

    def _create_log():
        details = oci.logging.models.CreateLogDetails(
            display_name=log_name,
            log_type=log_type,
            is_enabled=is_enabled,
            retention_duration=retention_duration,
            configuration=oci.logging.models.Configuration(
                compartment_id=compartment_id,
            ),
        )
        return log_client.create_log(
            log_group_id=log_group_id,
            create_log_details=details,
        ).data

    log = await loop.run_in_executor(None, _create_log)
    return {
        "action": "enable_logging",
        "log_group_id": log_group_id,
        "log_group_name": log_group_name,
        "log_id": log.id,
        "log_name": log_name,
        "log_type": log_type,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the log and log group that were created."""
    creds = getattr(connector, "credentials", {})
    log_group_id = execution_result.get("log_group_id", "")
    log_id = execution_result.get("log_id", "")
    if not creds or not log_group_id:
        return {"action": "rollback_enable_logging", "mock": True}

    from ._client import get_logging_client
    log_client = get_logging_client(creds)
    loop = asyncio.get_running_loop()

    if log_id:
        await loop.run_in_executor(
            None,
            lambda: log_client.delete_log(log_group_id=log_group_id, log_id=log_id),
        )
    await loop.run_in_executor(
        None,
        lambda: log_client.delete_log_group(log_group_id=log_group_id),
    )
    return {
        "action": "rollback_enable_logging",
        "log_group_id": log_group_id,
        "log_id": log_id,
        "status": "DELETED",
    }
