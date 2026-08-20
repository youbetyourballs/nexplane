# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.drift import ResourceState, DriftEvent
from app.services.drift_service import observe_surface, upsert_resource_state

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "none"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    resource_state_id = parameters.get("resource_state_id")
    drift_event_id = parameters.get("drift_event_id")

    if not resource_state_id or not drift_event_id:
        return {"status": "failed", "error": "resource_state_id and drift_event_id are required"}

    async with AsyncSessionLocal() as db:
        rs_result = await db.execute(
            select(ResourceState).where(ResourceState.id == uuid.UUID(resource_state_id))
        )
        resource_state = rs_result.scalar_one_or_none()

        if resource_state is None:
            return {"status": "failed", "error": f"ResourceState {resource_state_id} not found"}

        surface_type = resource_state.surface_type
        asset_id = resource_state.asset_id
        org_id = resource_state.organization_id
        anchor_state = resource_state.state

    # Dispatch surface-specific restoration
    try:
        result = await _restore_surface(surface_type, asset_id, anchor_state, connector)
    except Exception as exc:
        logger.error(
            "restore_resource_state: restoration failed asset=%s surface=%s: %s",
            asset_id, surface_type, exc,
        )
        return {"status": "failed", "error": str(exc)}

    # Re-observe to verify restoration and advance anchor
    async with AsyncSessionLocal() as db:
        try:
            observed = await observe_surface(db, org_id, asset_id, surface_type, connector)
            await upsert_resource_state(
                db=db,
                org_id=org_id,
                asset_id=asset_id,
                surface_type=surface_type,
                state=observed,
                source="cr_execution",
            )
        except Exception as exc:
            logger.warning("restore_resource_state: post-restore observation failed: %s", exc)

    return {
        "status": "success",
        "surface_type": surface_type,
        "asset_id": str(asset_id),
        "restoration_result": result,
    }


async def _restore_surface(
    surface_type: str, asset_id: uuid.UUID, anchor_state: dict, connector
) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from app.services.drift_service import HOST_SURFACES, CLOUD_SURFACES

    if surface_type in HOST_SURFACES:
        result = await dispatch_agent_job(
            command="restore_drift_state",
            parameters={"surface_type": surface_type, "target_state": anchor_state},
            asset_ids=[str(asset_id)],
            timeout_seconds=120,
        )
        if result.get("status") != "success":
            raise RuntimeError(result.get("error", "agent restore failed"))
        return result
    elif surface_type == "aws_security_group":
        ec2 = connector.boto3_client("ec2")
        sg_id = str(asset_id)
        existing = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
        if existing.get("IpPermissions"):
            ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=existing["IpPermissions"])
        if anchor_state.get("ingress"):
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=anchor_state["ingress"])
        return {"restored": "aws_security_group", "sg_id": sg_id}
    else:
        raise ValueError(f"No restoration handler for surface_type: {surface_type}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # ROLLBACK_CAPABILITY = "none" — this should never be called
    return {"status": "skipped", "reason": "restore_resource_state has no rollback by design"}
