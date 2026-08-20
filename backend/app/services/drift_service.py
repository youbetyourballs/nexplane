# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import ResourceState, DriftPolicy, DriftEvent
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

logger = logging.getLogger(__name__)

HOST_SURFACES: set[str] = {
    "ssh_config",
    "sudoers",
    "cron_jobs",
    "listening_ports",
    "running_services",
    "users_groups",
    "firewall_rules",
}

CLOUD_SURFACES: set[str] = {
    "aws_security_group",
    "aws_iam_policy",
    "aws_s3_bucket_policy",
    "gcp_firewall_rule",
    "gcp_iam_binding",
    "oci_security_list",
}

SURFACE_SEVERITY: dict[str, str] = {
    "sudoers": "high",
    "users_groups": "high",
    "aws_iam_policy": "high",
    "aws_s3_bucket_policy": "high",
    "gcp_iam_binding": "high",
    "firewall_rules": "high",
    "aws_security_group": "high",
    "gcp_firewall_rule": "high",
    "oci_security_list": "high",
    "ssh_config": "medium",
    "cron_jobs": "medium",
    "listening_ports": "low",
    "running_services": "low",
}


def normalize_state(surface_type: str, raw: dict) -> dict:
    """Sort keys deterministically. Arrays sorted by str() for reproducible diffs."""
    result = {}
    for k in sorted(raw.keys()):
        v = raw[k]
        if isinstance(v, list):
            v = sorted(v, key=str)
        elif isinstance(v, dict):
            v = normalize_state(surface_type, v)
        result[k] = v
    return result


def compute_diff(baseline: dict, observed: dict) -> dict:
    """Return structured diff or {} if identical."""
    added = {k: v for k, v in observed.items() if k not in baseline}
    removed = {k: v for k, v in baseline.items() if k not in observed}
    changed = {
        k: {"from": baseline[k], "to": observed[k]}
        for k in baseline
        if k in observed and baseline[k] != observed[k]
    }
    if not added and not removed and not changed:
        return {}
    return {"added": added, "removed": removed, "changed": changed}


async def upsert_resource_state(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    surface_type: str,
    state: dict,
    source: str,
    source_cr_id: Optional[uuid.UUID] = None,
    accepted_by: Optional[uuid.UUID] = None,
    accepted_at: Optional[datetime] = None,
    acceptance_note: Optional[str] = None,
) -> ResourceState:
    now = datetime.now(timezone.utc)
    normalized = normalize_state(surface_type, state)

    stmt = pg_insert(ResourceState).values(
        id=uuid.uuid4(),
        organization_id=org_id,
        asset_id=asset_id,
        surface_type=surface_type,
        state=normalized,
        captured_at=now,
        source=source,
        source_cr_id=source_cr_id,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        acceptance_note=acceptance_note,
    ).on_conflict_do_update(
        constraint="uq_resource_states_org_asset_surface",
        set_={
            "state": normalized,
            "captured_at": now,
            "source": source,
            "source_cr_id": source_cr_id,
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
            "acceptance_note": acceptance_note,
        }
    ).returning(ResourceState)

    result = await db.execute(stmt)
    await db.commit()
    return result.scalar_one()


async def load_resource_state(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    surface_type: str,
) -> Optional[ResourceState]:
    result = await db.execute(
        select(ResourceState).where(
            ResourceState.organization_id == org_id,
            ResourceState.asset_id == asset_id,
            ResourceState.surface_type == surface_type,
        )
    )
    return result.scalar_one_or_none()


async def observe_host_surface(
    db: AsyncSession,
    asset_id: uuid.UUID,
    surface_type: str,
) -> dict:
    result = await dispatch_agent_job(
        command="capture_drift_state",
        parameters={"surface_type": surface_type},
        asset_ids=[str(asset_id)],
        timeout_seconds=60,
    )
    if result.get("status") != "success":
        raise RuntimeError(result.get("error", f"agent observation failed for {surface_type}"))
    return result["state"]


async def observe_cloud_surface(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    surface_type: str,
    connector,
) -> dict:
    """Dispatch connector API call based on surface_type. connector is the org's active connector."""
    if surface_type == "aws_security_group":
        ec2 = connector.boto3_client("ec2")
        sg_id = str(asset_id)
        response = ec2.describe_security_groups(GroupIds=[sg_id])
        sg = response["SecurityGroups"][0]
        return {
            "ingress": sg.get("IpPermissions", []),
            "egress": sg.get("IpPermissionsEgress", []),
            "tags": sg.get("Tags", []),
        }
    elif surface_type == "aws_iam_policy":
        iam = connector.boto3_client("iam")
        policy_arn = str(asset_id)
        policy = iam.get_policy(PolicyArn=policy_arn)["Policy"]
        version = iam.get_policy_version(
            PolicyArn=policy_arn,
            VersionId=policy["DefaultVersionId"],
        )["PolicyVersion"]
        return {"document": version["Document"], "version_id": policy["DefaultVersionId"]}
    elif surface_type == "aws_s3_bucket_policy":
        s3 = connector.boto3_client("s3")
        bucket = str(asset_id)
        try:
            policy_str = s3.get_bucket_policy(Bucket=bucket)["Policy"]
            return {"policy": json.loads(policy_str)}
        except s3.exceptions.NoSuchBucketPolicy:
            return {"policy": None}
    else:
        raise ValueError(f"Unsupported cloud surface type: {surface_type}")


async def observe_surface(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    surface_type: str,
    connector=None,
) -> dict:
    if surface_type in HOST_SURFACES:
        return await observe_host_surface(db, asset_id, surface_type)
    elif surface_type in CLOUD_SURFACES:
        if connector is None:
            raise ValueError(f"connector required for cloud surface {surface_type}")
        return await observe_cloud_surface(db, org_id, asset_id, surface_type, connector)
    else:
        raise ValueError(f"Unknown surface type: {surface_type}")


async def _load_cr(db: AsyncSession, cr_id: uuid.UUID) -> Optional[ChangeRequest]:
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    return result.scalar_one_or_none()


def _load_catalog_entry(action_id: str) -> dict:
    import os
    catalog_path = os.path.join(
        os.path.dirname(__file__), "../connectors/catalog/nexplane_agent.json"
    )
    with open(catalog_path) as f:
        import json as _json
        catalog = _json.load(f)
    for entry in catalog.get("actions", []):
        if entry["action_id"] == action_id:
            return entry
    return {}


async def ensure_drift_policy(
    db: AsyncSession,
    org_id: uuid.UUID,
    asset_id: uuid.UUID,
    surface_type: str,
    source_cr_id: uuid.UUID,
) -> DriftPolicy:
    result = await db.execute(
        select(DriftPolicy).where(
            DriftPolicy.organization_id == org_id,
            DriftPolicy.scope_type == "asset",
            DriftPolicy.scope_value == str(asset_id),
            DriftPolicy.surface_types.contains([surface_type]),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    policy = DriftPolicy(
        organization_id=org_id,
        name=f"auto:{surface_type}:{asset_id}",
        scope_type="asset",
        scope_value=str(asset_id),
        surface_types=[surface_type],
        poll_interval_seconds=3600,
        auto_created=True,
        enabled=True,
        source_cr_id=source_cr_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(policy)
    await db.flush()
    return policy


async def create_shadow_cr(
    db: AsyncSession,
    drift_event: DriftEvent,
    asset_name: str,
    requester_id: uuid.UUID,
) -> uuid.UUID:
    """Create a DRAFT restore_resource_state CR linked to this drift event."""
    title = (
        f"Restore {drift_event.surface_type} on {asset_name} "
        f"(drift detected {drift_event.detected_at.strftime('%Y-%m-%d %H:%M')} UTC)"
    )
    cr = ChangeRequest(
        organization_id=drift_event.organization_id,
        requester_id=requester_id,
        title=title,
        description="System-generated restore CR for detected drift.",
        change_type=ChangeType.restore_resource_state,
        desired_outcome={
            "resource_state_id": str(drift_event.asset_id),
            "drift_event_id": str(drift_event.id),
        },
        target_asset_ids=[str(drift_event.asset_id)],
        status=ChangeRequestStatus.draft,
        risk_level=RiskLevel.medium,
    )
    db.add(cr)
    await db.flush()
    return cr.id


async def on_cr_completed(cr_id: uuid.UUID, db: AsyncSession) -> None:
    cr = await _load_cr(db, cr_id)
    if cr is None:
        logger.warning("on_cr_completed: CR %s not found", cr_id)
        return

    catalog_entry = _load_catalog_entry(str(cr.change_type))
    drift_surfaces = catalog_entry.get("drift_surfaces", [])
    if not drift_surfaces:
        return

    target_asset_ids = cr.target_asset_ids or []

    for asset_id_str in target_asset_ids:
        asset_id = uuid.UUID(asset_id_str) if isinstance(asset_id_str, str) else asset_id_str
        for surface_type in drift_surfaces:
            try:
                observed = await observe_surface(db, cr.organization_id, asset_id, surface_type)
                await upsert_resource_state(
                    db=db,
                    org_id=cr.organization_id,
                    asset_id=asset_id,
                    surface_type=surface_type,
                    state=observed,
                    source="cr_execution",
                    source_cr_id=cr_id,
                )
            except Exception as exc:
                logger.warning(
                    "on_cr_completed: observation failed for asset=%s surface=%s cr=%s: %s",
                    asset_id, surface_type, cr_id, exc,
                )
                # Do not blank the anchor — preserve existing state

            await ensure_drift_policy(db, cr.organization_id, asset_id, surface_type, cr_id)

            # Close any open drift events for this surface — CR resolved them
            await db.execute(
                update(DriftEvent)
                .where(
                    DriftEvent.organization_id == cr.organization_id,
                    DriftEvent.asset_id == asset_id,
                    DriftEvent.surface_type == surface_type,
                    DriftEvent.status == "open",
                )
                .values(
                    status="dismissed",
                    resolved_at=datetime.now(timezone.utc),
                    resolution_note=f"resolved by CR {cr_id}",
                )
            )
    await db.commit()
