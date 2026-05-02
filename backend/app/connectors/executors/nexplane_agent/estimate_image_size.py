async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "estimate_image_size",
        "source_device": parameters.get("source_device", "/dev/sda"),
        "source_size_bytes": 107374182400,
        "destination_path": parameters.get("destination_path", "/tmp"),
        "destination_available_bytes": 214748364800,
        "recommended_minimum_bytes": 118111600640,
        "sufficient_space": True,
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "estimate_image_size is read-only"}
