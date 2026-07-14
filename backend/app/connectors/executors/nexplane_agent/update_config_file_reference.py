# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import logging
from app.connectors.executors.nexplane_agent import _dispatch

logger = logging.getLogger(__name__)


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    file_path = params["file_path"]
    old_value = params["old_value"]
    new_value = params["new_value"]
    backup_dir = params.get("backup_dir", "/tmp/nexplane-backups")
    asset_id = str(cr.asset_id) if getattr(cr, "asset_id", None) else None

    result = await _dispatch.dispatch_agent_job(
        command="reference-update",
        parameters={
            "file": file_path,
            "old-value": old_value,
            "new-value": new_value,
            "backup-dir": backup_dir,
        },
        asset_ids=[asset_id] if asset_id else [],
        timeout_seconds=60,
    )

    if result.get("exit_code", 1) != 0:
        return {"status": "failed", "error": result.get("output", "agent job failed")}

    try:
        data = json.loads(result.get("output", "{}"))
    except (json.JSONDecodeError, TypeError):
        return {"status": "failed", "error": "parse_failure"}

    return {
        "status": data.get("status", "unknown"),
        "replacements": data.get("replacements", 0),
        "rollback_data": {
            "backup_path": data.get("backup_path"),
            "target_path": file_path,
            "asset_id": asset_id,
        },
    }


async def rollback(cr, connector, db) -> dict:
    rb = (getattr(cr, "execution_result", None) or {}).get("rollback_data", {})
    asset_id = rb.get("asset_id")

    result = await _dispatch.dispatch_agent_job(
        command="reference-restore",
        parameters={
            "backup-path": rb.get("backup_path", ""),
            "target-path": rb.get("target_path", ""),
        },
        asset_ids=[asset_id] if asset_id else [],
        timeout_seconds=60,
    )

    status = "rolled_back" if result.get("exit_code") == 0 else "rollback_failed"
    return {"status": status, "agent_output": result.get("output")}
