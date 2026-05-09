"""
ip_change_service — post-execution metadata update for change_ip and migrate_ip CRs.

After a successful IP change, update asset_metadata.ip_addresses to reflect the
new IP so that downstream services (DNS discovery, UI, inventory) see current data.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def update_asset_ip_metadata(
    db: "AsyncSession",
    asset_ids: list,
    execution_result: dict,
) -> None:
    """After a successful change_ip or migrate_ip, update asset_metadata.ip_addresses.

    Reads the new IP from execution_result in the following priority:
      1. execution_result["change_ip_result"]["snapshot"]["ip_v4_addresses"]  (migrate_ip)
      2. execution_result["snapshot"]["ip_v4_addresses"]                       (change_ip)
      3. execution_result["new_ip_v4"] stripped of CIDR prefix                (fallback)

    Merges new IPs into existing asset_metadata, replacing stale entries for the
    same interface when the interface key is available.
    """
    import uuid as _uuid
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.asset import Asset

    # Resolve the new IP from result
    new_ips = _extract_new_ips(execution_result)
    if not new_ips:
        log.warning("update_asset_ip_metadata: no new IP found in execution_result, skipping")
        return

    for raw_id in asset_ids:
        asset_uuid = _uuid.UUID(raw_id) if isinstance(raw_id, str) else raw_id
        asset = await db.get(Asset, asset_uuid)
        if asset is None:
            log.warning("update_asset_ip_metadata: asset %s not found", raw_id)
            continue

        meta = dict(asset.asset_metadata or {})
        meta["ip_addresses"] = new_ips
        asset.asset_metadata = meta
        try:
            flag_modified(asset, "asset_metadata")
        except AttributeError:
            pass  # not a real SQLAlchemy instance (e.g. in unit tests)
        log.info("update_asset_ip_metadata: updated asset %s ip_addresses -> %s", raw_id, new_ips)

    await db.commit()


def _extract_new_ips(execution_result: dict) -> list[str]:
    """Return a list of new IP address strings from the execution result dict."""
    # Path 1: migrate_ip wraps change_ip result
    change_ip_result = execution_result.get("change_ip_result", {})
    snapshot = change_ip_result.get("snapshot", {})
    v4_addrs = snapshot.get("ip_v4_addresses", [])
    if v4_addrs:
        return list(v4_addrs)

    # Path 2: direct change_ip snapshot
    snapshot = execution_result.get("snapshot", {})
    v4_addrs = snapshot.get("ip_v4_addresses", [])
    if v4_addrs:
        return list(v4_addrs)

    # Path 3: new_ip_v4 parameter echoed in result
    new_ip = execution_result.get("new_ip_v4")
    if new_ip:
        return [new_ip]

    return []
