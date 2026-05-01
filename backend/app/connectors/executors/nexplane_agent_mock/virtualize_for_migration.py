from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "virtualize_for_migration",
        "image_path": parameters.get("image_path"),
        "image_size_bytes": 107374182400,
        "source_device": parameters.get("source_device", "/dev/sda"),
        "network_config_applied": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "delete_image",
        "image_path": execution_result.get("image_path"),
        "deleted": True,
    }
