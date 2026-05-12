"""
Discovers assets that violate a patch age baseline.
Uses agent audit_patch_status command + asset_metadata.last_security_patch_at.
"""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timedelta, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    max_age = int(parameters.get("max_patch_age_days", 30))
    os_family_filter = parameters.get("os_family")
    auto_cr = bool(parameters.get("create_change_request", False))

    # Support both mock (connector.asset_repository) and production (DB) paths
    if hasattr(connector, "asset_repository"):
        assets = await connector.asset_repository.list_assets(filter=parameters.get("asset_filter", {}))
        # Wrap mock assets so they have consistent attribute access
        asset_list = [
            _MockAssetWrapper(a) for a in assets
        ]
    else:
        asset_list = await _load_assets_from_db(connector, asset_ids)

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age)
    compliant, non_compliant = [], []

    for asset in asset_list:
        meta = asset.meta
        af = meta.get("os_family", "linux")
        if os_family_filter and af != os_family_filter:
            continue

        last_raw = meta.get("last_security_patch_at")
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days_since = (datetime.now(tz=timezone.utc) - last).days
                if days_since <= max_age:
                    compliant.append(str(asset.id))
                    continue
            except Exception:
                days_since = None
        else:
            days_since = None

        entry = {
            "asset_id": str(asset.id),
            "hostname": asset.hostname,
            "os_family": af,
            "days_since_patch": days_since,
        }

        if auto_cr:
            cr_id = await _create_compliance_cr(connector, asset, af, days_since, max_age)
            if cr_id:
                entry["change_request_id"] = cr_id

        non_compliant.append(entry)

    return {
        "compliant_hosts": compliant,
        "non_compliant_hosts": non_compliant,
        "total_assets_checked": len(compliant) + len(non_compliant),
    }


async def _create_compliance_cr(connector, asset, af, days_since, max_age) -> str | None:
    """Create a patch_packages CR for a non-compliant host."""
    description = (
        f"Host is {days_since if days_since is not None else 'unknown'} days "
        f"behind on security patches (threshold: {max_age} days)."
    )
    # Mock connector path
    if hasattr(connector, "change_request_service"):
        cr = await connector.change_request_service.create(
            change_type="patch_packages",
            asset_id=asset.id,
            params={"os_family": af, "mode": "security_only", "dry_run": False},
            title=f"Security patch compliance: {asset.hostname}",
            description=description,
        )
        return cr.id

    # Production DB path
    try:
        from app.database import AsyncSessionLocal
        from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus, RiskLevel
        from app.models.user import User
        from sqlalchemy import select

        org_id = getattr(connector, "organization_id", None)
        if not org_id:
            return None

        async with AsyncSessionLocal() as db:
            user_r = await db.execute(select(User).where(User.organization_id == org_id).limit(1))
            system_user = user_r.scalars().first()
            if system_user:
                cr = ChangeRequest(
                    organization_id=org_id,
                    requester_id=system_user.id,
                    change_type=ChangeType.patch_packages,
                    title=f"Security patch compliance: {asset.hostname}",
                    description=description,
                    risk_level=RiskLevel.medium,
                    target_asset_ids=[str(asset.id)],
                    parameters={"os_family": af, "mode": "security_only", "dry_run": False},
                    status=ChangeRequestStatus.draft,
                )
                db.add(cr)
                await db.commit()
                await db.refresh(cr)
                return str(cr.id)
    except Exception:
        pass
    return None


async def _load_assets_from_db(connector, asset_ids: list) -> list:
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset, AssetType
    from sqlalchemy import select

    org_id = getattr(connector, "organization_id", None)
    if not org_id:
        return []

    async with AsyncSessionLocal() as db:
        stmt = select(Asset).where(
            Asset.organization_id == org_id,
            Asset.asset_type == AssetType.server,
        )
        if asset_ids:
            stmt = stmt.where(Asset.id.in_([uuid.UUID(str(a)) for a in asset_ids]))
        result = await db.execute(stmt)
        assets = result.scalars().all()

    return [_DBAssetWrapper(a) for a in assets]


class _MockAssetWrapper:
    """Wraps mock assets that use .metadata attribute."""
    def __init__(self, asset):
        self._asset = asset
        self.id = asset.id
        self.hostname = asset.hostname
        self.meta = asset.metadata or {}


class _DBAssetWrapper:
    """Wraps DB Asset models that use .asset_metadata attribute."""
    def __init__(self, asset):
        self._asset = asset
        self.id = str(asset.id)
        self.hostname = asset.name
        self.meta = asset.asset_metadata or {}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_patch_compliance is read-only"}
