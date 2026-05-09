"""Service to persist containerize_build results to asset_metadata."""
import uuid
from app.models.asset import Asset


async def write_build_result_to_metadata(db, asset_ids: list, execution_result: dict) -> None:
    """Read build artifacts from execution_result steps and write to asset_metadata.build_results."""
    build_data = None
    for step in execution_result.get("steps", []):
        result = step.get("result", {})
        if isinstance(result, dict) and result.get("action") == "containerize_build":
            build_data = result
            break
    if build_data is None:
        return

    app_name = build_data.get("app_name", "")
    if not app_name:
        return

    for asset_id_str in asset_ids:
        try:
            asset_uuid = uuid.UUID(str(asset_id_str))
        except ValueError:
            continue
        asset = await db.get(Asset, asset_uuid)
        if not asset:
            continue

        metadata = dict(asset.asset_metadata or {})
        build_results = dict(metadata.get("build_results", {}))
        build_results[app_name] = {
            "image_name": build_data.get("image_name", ""),
            "image_digest": build_data.get("image_digest", ""),
            "dockerfile": build_data.get("dockerfile", ""),
            "manifests": build_data.get("manifests", {}),
            "containerization_status": "image_pushed" if build_data.get("image_digest") else "dockerfile_generated",
        }
        metadata["build_results"] = build_results

        # Also update containerization_status on applications[] entry if present
        applications = metadata.get("applications", [])
        for app in applications:
            if app.get("name") == app_name:
                app["containerization_status"] = build_results[app_name]["containerization_status"]
                app["image_digest"] = build_data.get("image_digest")
                break
        metadata["applications"] = applications
        asset.asset_metadata = metadata

    await db.commit()
