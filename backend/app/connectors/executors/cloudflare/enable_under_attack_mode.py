from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "enable_under_attack_mode", "mock": True}
    import httpx
    zone_id = creds['zone_id']
    headers = {"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.patch(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/settings/security_level", headers=headers, json={"value": "under_attack"})
        resp.raise_for_status()
    return {"action": "enable_under_attack_mode", "zone_id": zone_id, "executed_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "use disable_under_attack_mode to roll back"}
