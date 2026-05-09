import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.asset import Asset

log = logging.getLogger(__name__)


async def write_discovered_apps_to_metadata(
    db: AsyncSession,
    asset_ids: list[str],
    execution_result: dict,
) -> None:
    """
    After an agent_appdiscovery CR completes, extract the discovered applications
    from the execution result and persist them to asset_metadata["applications"].

    Follows the same pattern as write_cis_score_to_metadata in compliance/drift.py.
    """
    # Extract applications from the first step result that contains them
    applications = None
    for step in execution_result.get("steps", []):
        result = step.get("result", {})
        if isinstance(result, dict) and "applications" in result:
            applications = result["applications"]
            break

    if applications is None:
        log.debug("write_discovered_apps_to_metadata: no applications found in execution result")
        return

    for asset_id_str in asset_ids:
        try:
            asset_id = uuid.UUID(asset_id_str)
        except ValueError:
            asset_id = asset_id_str  # type: ignore[assignment]

        result = await db.execute(select(Asset).where(Asset.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            log.warning("write_discovered_apps_to_metadata: asset %s not found", asset_id_str)
            continue

        metadata = dict(asset.asset_metadata or {})
        metadata["applications"] = applications
        asset.asset_metadata = metadata
        log.info("write_discovered_apps_to_metadata: wrote %d apps to asset %s", len(applications), asset_id_str)

    await db.commit()
