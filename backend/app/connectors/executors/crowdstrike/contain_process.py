# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "contain_process", "process_name": parameters.get("process_name"), "assets": asset_ids, "killed": True, "contained_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "process termination has no rollback"}
