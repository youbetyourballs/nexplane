async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_zpa_applications", "applications": [], "count": 0}
    # ZPA uses a different API endpoint
    cloud = creds.get("cloud", "")
    import httpx
    token_resp_data = {"applications": [], "count": 0, "action": "discover_zpa_applications"}
    return token_resp_data

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
