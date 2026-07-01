# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Service to mark an asset's applications as retired after agent_containerize_retire."""
import uuid
from app.models.asset import Asset


async def mark_asset_retired(db, asset_ids: list, execution_result: dict) -> None:
    """Update asset_metadata.applications[].containerization_status to 'retired'."""
    retire_data = None
    for step in execution_result.get("steps", []):
        result = step.get("result", {})
        if isinstance(result, dict) and result.get("action") == "containerize_retire":
            retire_data = result
            break
    if retire_data is None or not retire_data.get("service_stopped"):
        return

    unit = retire_data.get("systemd_unit", "")
    app_name = unit.replace(".service", "") if unit else ""

    for asset_id_str in asset_ids:
        try:
            asset_uuid = uuid.UUID(str(asset_id_str))
        except ValueError:
            continue
        asset = await db.get(Asset, asset_uuid)
        if not asset:
            continue

        metadata = dict(asset.asset_metadata or {})
        apps = metadata.get("applications", [])
        for app in apps:
            if app.get("name") == app_name or app.get("systemd_unit") == unit:
                app["containerization_status"] = "retired"
        metadata["applications"] = apps
        asset.asset_metadata = metadata

    await db.commit()
