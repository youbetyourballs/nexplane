# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift import ResourceState, DriftPolicy, DriftEvent

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
