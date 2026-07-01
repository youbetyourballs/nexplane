# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Fan-out executor — spawns child CRs for each IdentityAccount on a target profile.

Called for fan-out ChangeTypes: emergency_user_lockout, user_suspension,
user_scope_reduction, enforce_mfa.

Signature matches executor contract but adds change_request_id and change_type
which are injected by the modified execute_change_workflow.
"""
from __future__ import annotations
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


async def _lookup_profile(profile_id: uuid.UUID, organization_id=None, db=None):
    from app.database import db_factory
    from app.models.identity_profile import IdentityProfile
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    ctx = db_factory() if db is None else None
    _db = await ctx.__aenter__() if ctx else db
    try:
        q = select(IdentityProfile).where(IdentityProfile.id == profile_id)
        if organization_id is not None:
            q = q.where(IdentityProfile.organization_id == organization_id)
        q = q.options(selectinload(IdentityProfile.accounts))
        result = await _db.execute(q)
        return result.scalar_one_or_none()
    finally:
        if ctx:
            await ctx.__aexit__(None, None, None)


async def _spawn_child_cr(
    parent_cr_id: uuid.UUID,
    connector_id: uuid.UUID,
    connector_type: str,
    external_id: str,
    action: str,
    parameters: dict,
    organization_id: Optional[uuid.UUID] = None,
    requester_id: Optional[uuid.UUID] = None,
):
    """Create and auto-approve a child ChangeRequest."""
    from app.database import db_factory
    from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType
    from datetime import datetime, timezone

    async with db_factory() as db:
        # Resolve change_type: use _parent_change_type from parameters if provided,
        # otherwise fall back to emergency_user_lockout
        parent_change_type = parameters.get("_parent_change_type", "emergency_user_lockout")
        try:
            cr_change_type = ChangeType(parent_change_type)
        except ValueError:
            cr_change_type = ChangeType.emergency_user_lockout

        child = ChangeRequest(
            id=uuid.uuid4(),
            title=f"Fan-out: {action} on {connector_type} ({external_id})",
            description=f"Auto-spawned child CR for {action} on {connector_type} account {external_id}",
            change_type=cr_change_type,
            status=ChangeRequestStatus.approved,
            target_asset_ids=[],
            desired_outcome={
                **parameters,
                "external_id": external_id,
                "connector_id": str(connector_id),
                "connector_type": connector_type,
                "fan_out_action": action,
            },
            parent_change_request_id=parent_cr_id,
            stateful_approved_at=datetime.now(timezone.utc),
            organization_id=organization_id,
            requester_id=requester_id,
        )
        db.add(child)
        await db.commit()
        await db.refresh(child)
        return child


async def execute(
    parameters: dict,
    asset_ids: list,
    connector,
    change_request_id: Optional[uuid.UUID] = None,
    change_type: str = "",
    db=None,
) -> dict:
    from app.connectors.executors.identity.fan_out_registry import get_fan_out_action

    profile_id_str = parameters.get("identity_profile_id") or parameters.get("profile_id")
    if not profile_id_str:
        return {"status": "failed", "reason": "identity_profile_id is required"}

    try:
        profile_id = uuid.UUID(str(profile_id_str))
    except ValueError:
        return {"status": "failed", "reason": f"invalid identity_profile_id: {profile_id_str}"}

    # organization_id and requester_id: prefer from the parent CR (connector may be None)
    organization_id = getattr(connector, "organization_id", None)
    requester_id = None
    if change_request_id is not None:
        from app.database import db_factory
        from app.models.change_request import ChangeRequest
        from sqlalchemy import select as _select
        async with db_factory() as _cr_db:
            _cr_r = await _cr_db.execute(
                _select(ChangeRequest).where(ChangeRequest.id == change_request_id)
            )
            _parent_cr = _cr_r.scalar_one_or_none()
            if _parent_cr is not None:
                if organization_id is None:
                    organization_id = _parent_cr.organization_id
                requester_id = _parent_cr.requester_id

    profile = await _lookup_profile(profile_id, organization_id=organization_id, db=db)
    if profile is None:
        return {"status": "failed", "reason": f"IdentityProfile {profile_id} not found"}

    accounts = [a for a in profile.accounts if not a.is_stale]
    if not accounts:
        return {"status": "failed", "reason": "no non-stale accounts found for this profile"}

    tasks = []
    skipped = []
    for account in accounts:
        action = get_fan_out_action(change_type, account.connector_type)
        if action is None:
            skipped.append({"connector_type": account.connector_type, "reason": "no action mapped"})
            continue
        tasks.append((account, action))

    child_results = []
    for account, action in tasks:
        try:
            child = await _spawn_child_cr(
                parent_cr_id=change_request_id,
                connector_id=account.connector_id,
                connector_type=account.connector_type,
                external_id=account.external_id,
                action=action,
                parameters={**parameters, "_parent_change_type": change_type},
                organization_id=organization_id,
                requester_id=requester_id,
            )
            child_results.append({
                "child_cr_id": str(child.id),
                "connector_type": account.connector_type,
                "action": action,
                "status": "spawned",
            })
        except Exception as exc:
            child_results.append({
                "connector_type": account.connector_type,
                "action": action,
                "status": "error",
                "reason": str(exc),
            })

    succeeded = [r for r in child_results if r["status"] == "spawned"]
    failed = [r for r in child_results if r["status"] == "error"]

    if not succeeded:
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "completed"

    return {
        "status": status,
        "profile_id": str(profile_id),
        "profile_email": profile.primary_email,
        "child_crs": child_results,
        "skipped": skipped,
        "warnings": [f"{r['connector_type']}: {r['reason']}" for r in failed],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Parent rollback: trigger rollback on all child CRs that succeeded."""
    child_crs = execution_result.get("child_crs", [])
    spawned_ids = [r["child_cr_id"] for r in child_crs if r.get("status") == "spawned"]

    if not spawned_ids:
        return {"rolled_back": True, "reason": "no child CRs to roll back"}

    rollback_results = []
    from app.database import db_factory
    from app.models.change_request import ChangeRequest
    from sqlalchemy import select

    async with db_factory() as db:
        for child_id in spawned_ids:
            try:
                result = await db.execute(
                    select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(child_id))
                )
                child_cr = result.scalar_one_or_none()
                if child_cr is None:
                    rollback_results.append({"child_cr_id": child_id, "status": "not_found"})
                    continue
                from app.services.change_request_service import rollback_change_request
                rb = await rollback_change_request(child_cr, db)
                rollback_results.append({"child_cr_id": child_id, "status": rb.get("status", "unknown")})
            except Exception as exc:
                rollback_results.append({"child_cr_id": child_id, "status": "error", "reason": str(exc)})

    all_ok = all(r["status"] in ("completed", "skipped") for r in rollback_results)
    any_ok = any(r["status"] in ("completed", "skipped") for r in rollback_results)

    return {
        "rolled_back": True,
        "status": "completed" if all_ok else ("partial" if any_ok else "failed"),
        "child_rollbacks": rollback_results,
    }
