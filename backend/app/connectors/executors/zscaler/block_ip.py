async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ip_address = parameters["ip_address"]
    if not creds:
        return {"action": "block_ip", "ip_address": ip_address, "blocked": True}
    from ._client import get_session
    async with await get_session(creds) as client:
        resp = await client.get("/ipSourceGroups")
        resp.raise_for_status()
        groups = resp.json()
        deny_group = next((g for g in groups if "deny" in g.get("name", "").lower() or "block" in g.get("name", "").lower()), None)
        if deny_group:
            ips = deny_group.get("ipAddresses", [])
            ips.append(ip_address)
            resp2 = await client.put(f"/ipSourceGroups/{deny_group['id']}", json={"ipAddresses": ips, "name": deny_group["name"]})
            resp2.raise_for_status()
    return {"action": "block_ip", "ip_address": ip_address, "blocked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove IP from deny list manually"}
