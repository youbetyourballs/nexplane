# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import logging
from app.connectors.executors.nexplane_agent import _dispatch
from app.database import AsyncSessionLocal
from app.models.drift import ResourceState
from sqlalchemy import select

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "manual"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    resource_state_id = parameters.get("resource_state_id")
    if not resource_state_id:
        return {"status": "error", "error": "resource_state_id required in desired_outcome"}

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ResourceState).where(ResourceState.id == uuid.UUID(str(resource_state_id)))
        )
        rs = result.scalar_one_or_none()

    if rs is None:
        return {"status": "error", "error": f"ResourceState {resource_state_id} not found"}

    result = await _dispatch.dispatch_agent_job(
        command="restore_drift_state",
        parameters={"surface_type": rs.surface_type, "target_state": rs.state},
        asset_ids=[str(a) for a in asset_ids],
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["_surface_type"] = rs.surface_type
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    logger.warning("restore_drift_state rollback: no automated rollback for state restoration")
    return {"status": "success", "note": "no automated rollback for drift state restoration"}
