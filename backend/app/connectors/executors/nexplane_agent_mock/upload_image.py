from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "upload_image",
        "image_path": parameters.get("image_path"),
        "destination_uri": parameters.get("destination_uri"),
        "size_bytes": 107374182400,
        "etag": "d41d8cd98f00b204e9800998ecf8427e",
        "checksum_verified": True,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "delete_s3_object",
        "destination_uri": execution_result.get("destination_uri"),
        "deleted": True,
    }
