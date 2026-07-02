# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Nexplane MCP tools — Planning Context domain (8 tools).

Read-only tools for AI agents to gather context before creating change requests.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlalchemy import select, func

from app.mcp_server import mcp
from app.database import AsyncSessionLocal


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user, agent_token = await resolve_mcp_token(token, db)
        # Return whichever principal is non-None as the first element
        principal = user if user is not None else agent_token
        return principal, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def get_asset_history(
    token: str,
    asset_id: str,
    since_days: int = 90,
    cr_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Return the change history for a specific asset.

    Queries all change requests whose target_asset_ids contains this asset_id,
    filtered to the last `since_days` days and optionally to specific change types.

    Returns a list of CR summaries with outcome, approver, duration, and rollback status.
    Use before creating a CR to understand what has already been done to this asset.
    """
    from app.models.change_request import ChangeRequest
    from app.models.user import User
    from app.models.approval import Approval

    principal, db, db_cm = await _auth(token)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

        stmt = select(ChangeRequest).where(
            ChangeRequest.organization_id == principal.organization_id,
            ChangeRequest.created_at >= cutoff,
        ).order_by(ChangeRequest.created_at.desc())

        if cr_types:
            stmt = stmt.where(ChangeRequest.change_type.in_(cr_types))

        result = await db.execute(stmt)
        all_crs = result.scalars().all()

        # Filter to those targeting this asset (JSON list membership check in Python)
        asset_id_str = str(asset_id)
        matching = [cr for cr in all_crs if asset_id_str in (cr.target_asset_ids or [])]

        rows = []
        for cr in matching:
            # Derive outcome from status
            status_str = str(cr.status.value) if hasattr(cr.status, "value") else str(cr.status)
            if status_str == "completed":
                outcome = "success"
            elif status_str == "rolled_back":
                outcome = "rolled_back"
            elif status_str == "failed":
                outcome = "failed"
            else:
                outcome = status_str

            rolled_back = status_str == "rolled_back"

            # Duration: stateful_approved_at -> updated_at
            duration_seconds = None
            if cr.stateful_approved_at and cr.updated_at:
                try:
                    approved = cr.stateful_approved_at
                    updated = cr.updated_at
                    if approved.tzinfo is None:
                        approved = approved.replace(tzinfo=timezone.utc)
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    duration_seconds = (updated - approved).total_seconds()
                    if duration_seconds < 0:
                        duration_seconds = None
                except Exception:
                    duration_seconds = None

            # Approver: look up first approval
            approver_name = None
            approvals_result = await db.execute(
                select(Approval).where(Approval.change_request_id == cr.id).limit(1)
            )
            approval = approvals_result.scalar_one_or_none()
            if approval:
                user_result = await db.execute(
                    select(User).where(User.id == approval.approver_id)
                )
                approver_user = user_result.scalar_one_or_none()
                if approver_user:
                    approver_name = approver_user.email

            rows.append({
                "cr_id": str(cr.id),
                "change_type": str(cr.change_type.value) if hasattr(cr.change_type, "value") else str(cr.change_type),
                "title": cr.title,
                "status": status_str,
                "outcome": outcome,
                "approver_name": approver_name,
                "approved_at": cr.stateful_approved_at.isoformat() if cr.stateful_approved_at else None,
                "applied_at": cr.applied_at.isoformat() if cr.applied_at else None,
                "rolled_back": rolled_back,
                "duration_seconds": duration_seconds,
                "notes": cr.description,
            })

        return rows
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_fleet_context(
    token: str,
    os: Optional[str] = None,
    kernel_version_lt: Optional[str] = None,
    environment: Optional[str] = None,
    tag_key: Optional[str] = None,
    tag_value: Optional[str] = None,
    connector_type: Optional[str] = None,
    has_open_findings: Optional[bool] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Query the asset fleet with rich filters for planning purposes.

    Filters: os (ILIKE match on metadata->os), kernel_version_lt (semver less-than),
    environment, tag_key+tag_value pair, connector_type, has_open_findings (bool).

    For each asset returns: asset_id, name, asset_type, environment, connector_type,
    tags, open_findings_count, last_cr_at, metadata.

    Use to scope a change to a specific fleet segment (e.g., all prod Linux hosts
    running kernel < 5.15 with open findings).
    """
    from app.models.asset import Asset
    from app.models.connector import Connector
    from app.models.change_request import ChangeRequest
    from app.models.vulnerability import VulnerabilityFinding

    principal, db, db_cm = await _auth(token)
    try:
        stmt = select(Asset).where(
            Asset.organization_id == principal.organization_id
        ).limit(limit)

        if environment:
            stmt = stmt.where(Asset.environment == environment)

        result = await db.execute(stmt)
        assets = result.scalars().all()

        def _parse_version(v: str) -> tuple:
            """Parse a version string like '5.15.0-89-generic' -> (5, 15, 0)."""
            import re
            parts = re.findall(r"\d+", v)
            return tuple(int(p) for p in parts[:3]) if parts else (0,)

        def _version_lt(v_str: str, lt_str: str) -> bool:
            return _parse_version(v_str) < _parse_version(lt_str)

        filtered = []
        for asset in assets:
            meta = asset.asset_metadata or {}

            # os filter
            if os:
                asset_os = (meta.get("os") or "").lower()
                if os.lower().replace("%", "") not in asset_os:
                    continue

            # kernel_version_lt filter
            if kernel_version_lt:
                kv = meta.get("kernel_version") or ""
                if not kv or not _version_lt(kv, kernel_version_lt):
                    continue

            # tag_key/tag_value filter (tags is a list of "key=value" strings)
            if tag_key:
                tags = asset.tags or []
                if tag_value:
                    needle = f"{tag_key}={tag_value}"
                    if needle not in tags:
                        continue
                else:
                    if not any(t.startswith(f"{tag_key}=") for t in tags):
                        continue

            filtered.append(asset)

        # Hoist CR fetch: one query for all org CRs, filter per-asset in Python
        cr_full_result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.organization_id == principal.organization_id
            ).order_by(ChangeRequest.created_at.desc()).limit(500)
        )
        all_crs = cr_full_result.scalars().all()

        rows = []
        for asset in filtered:
            # connector_type check
            connector_type_val = None
            if asset.connector_id:
                conn_result = await db.execute(
                    select(Connector).where(Connector.id == asset.connector_id)
                )
                conn = conn_result.scalar_one_or_none()
                if conn:
                    connector_type_val = str(conn.connector_type.value) if hasattr(conn.connector_type, "value") else str(conn.connector_type)

            if connector_type and connector_type_val != connector_type:
                continue

            # open findings count
            findings_result = await db.execute(
                select(func.count()).select_from(VulnerabilityFinding).where(
                    VulnerabilityFinding.asset_id == asset.id,
                    VulnerabilityFinding.status == "open",
                )
            )
            open_findings_count = findings_result.scalar() or 0

            if has_open_findings is True and open_findings_count == 0:
                continue
            if has_open_findings is False and open_findings_count > 0:
                continue

            # last CR: filter the already-fetched org CRs in Python for JSON containment
            asset_id_str = str(asset.id)
            asset_crs = [cr for cr in all_crs if asset_id_str in (cr.target_asset_ids or [])]
            last_cr_at = max(cr.created_at for cr in asset_crs).isoformat() if asset_crs else None

            rows.append({
                "asset_id": str(asset.id),
                "name": asset.name,
                "asset_type": str(asset.asset_type.value) if hasattr(asset.asset_type, "value") else str(asset.asset_type),
                "environment": str(asset.environment.value) if hasattr(asset.environment, "value") else str(asset.environment),
                "connector_type": connector_type_val,
                "tags": asset.tags,
                "open_findings_count": open_findings_count,
                "last_cr_at": last_cr_at,
                "metadata": asset.asset_metadata,
            })

        return rows
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def find_similar_assets(
    token: str,
    asset_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Find assets similar to the given asset using Jaccard similarity on tags.

    Matches assets of the same type and environment within the same org.
    Similarity score = |shared_tags| / |union_tags| (0.0–1.0).

    Returns sorted list with shared_tags and different_tags for each candidate.
    Use to find peer assets when planning fleet-wide changes.
    """
    import uuid
    from app.models.asset import Asset

    _user, db, db_cm = await _auth(token)
    try:
        asset_uuid = uuid.UUID(asset_id)

        # Fetch target asset
        target_result = await db.execute(
            select(Asset).where(
                Asset.id == asset_uuid,
                Asset.organization_id == _user.organization_id,
            )
        )
        target = target_result.scalar_one_or_none()
        if target is None:
            return [{"error": "Asset not found"}]

        target_tags = set(target.tags or [])

        # Fetch candidates: same type + environment, different id
        candidates_result = await db.execute(
            select(Asset).where(
                Asset.organization_id == _user.organization_id,
                Asset.asset_type == target.asset_type,
                Asset.environment == target.environment,
                Asset.id != asset_uuid,
            )
        )
        candidates = candidates_result.scalars().all()

        scored = []
        for c in candidates:
            c_tags = set(c.tags or [])
            union = target_tags | c_tags
            shared = target_tags & c_tags
            if not union:
                score = 0.0
            else:
                score = len(shared) / len(union)

            different = (target_tags | c_tags) - shared

            scored.append({
                "asset_id": str(c.id),
                "name": c.name,
                "similarity_score": round(score, 4),
                "shared_tags": sorted(shared),
                "different_tags": sorted(different),
            })

        scored.sort(key=lambda x: x["similarity_score"], reverse=True)
        return scored[:limit]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_migration_precedents(
    token: str,
    change_type: str,
    asset_type: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Return statistics on past executions of a specific change type in this org.

    Covers completed, failed, and rolled_back CRs. Provides: success_rate,
    avg_duration_minutes, rollback_frequency, and the top 5 failure error strings.

    Use before creating a CR to understand historical risk and common failure modes.
    """
    import uuid
    from collections import Counter
    from app.models.change_request import ChangeRequest
    from app.models.asset import Asset

    _user, db, db_cm = await _auth(token)
    try:
        stmt = select(ChangeRequest).where(
            ChangeRequest.organization_id == _user.organization_id,
            ChangeRequest.change_type == change_type,
            ChangeRequest.status.in_(["completed", "failed", "rolled_back"]),
        ).order_by(ChangeRequest.created_at.desc()).limit(limit)

        result = await db.execute(stmt)
        crs = result.scalars().all()

        if asset_type:
            # Filter: keep only CRs that targeted at least one asset of the given type
            filtered = []
            for cr in crs:
                if not cr.target_asset_ids:
                    continue
                for aid in cr.target_asset_ids:
                    try:
                        asset_result = await db.execute(
                            select(Asset).where(Asset.id == uuid.UUID(aid))
                        )
                        a = asset_result.scalar_one_or_none()
                        if a and (str(a.asset_type.value) if hasattr(a.asset_type, "value") else str(a.asset_type)) == asset_type:
                            filtered.append(cr)
                            break
                    except Exception:
                        continue
            crs = filtered

        total = len(crs)
        if total == 0:
            return {
                "total_executions": 0,
                "success_rate": None,
                "avg_duration_minutes": None,
                "rollback_frequency": None,
                "common_failure_modes": [],
                "sample_cr_ids": [],
            }

        completed = sum(1 for cr in crs if str(getattr(cr.status, "value", cr.status)) == "completed")
        rolled_back = sum(1 for cr in crs if str(getattr(cr.status, "value", cr.status)) == "rolled_back")

        success_rate = round(completed / total, 4)
        rollback_frequency = round(rolled_back / total, 4)

        # Duration: stateful_approved_at -> updated_at
        durations_min = []
        for cr in crs:
            if cr.stateful_approved_at and cr.updated_at:
                try:
                    approved = cr.stateful_approved_at
                    updated = cr.updated_at
                    if approved.tzinfo is None:
                        approved = approved.replace(tzinfo=timezone.utc)
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    secs = (updated - approved).total_seconds()
                    if secs > 0:
                        durations_min.append(secs / 60.0)
                except Exception:
                    pass

        avg_duration_minutes = round(sum(durations_min) / len(durations_min), 2) if durations_min else None

        # Failure mode extraction via ExecutionRun
        error_counter: Counter = Counter()
        try:
            from app.models.execution_run import ExecutionRun
            failed_ids = [cr.id for cr in crs if str(getattr(cr.status, "value", cr.status)) in ("failed", "rolled_back")]
            for cr_id in failed_ids:
                runs_result = await db.execute(
                    select(ExecutionRun).where(ExecutionRun.change_request_id == cr_id).limit(1)
                )
                run = runs_result.scalar_one_or_none()
                if run and run.result:
                    err = run.result.get("error") or run.result.get("message") or ""
                    if err:
                        error_counter[err] += 1
        except ImportError:
            pass

        common_failures = [{"error": err, "count": cnt} for err, cnt in error_counter.most_common(5)]

        sample_ids = [str(cr.id) for cr in crs[-5:]]

        return {
            "total_executions": total,
            "success_rate": success_rate,
            "avg_duration_minutes": avg_duration_minutes,
            "rollback_frequency": rollback_frequency,
            "common_failure_modes": common_failures,
            "sample_cr_ids": sample_ids,
        }
    finally:
        await db_cm.__aexit__(None, None, None)
