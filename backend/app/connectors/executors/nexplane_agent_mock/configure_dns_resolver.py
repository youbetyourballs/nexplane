from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"mode": parameters.get("mode", "plain"), "resolvers": parameters.get("resolvers", []), "daemon": "systemd-resolved", "snapshot": {}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_dns_resolver"}
