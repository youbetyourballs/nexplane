from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "configure_ntp",
        "daemon": "chronyd",
        "config_path": "/etc/chrony.conf",
        "servers_configured": parameters.get("servers", ["time.cloudflare.com"]),
        "snapshot": "# previous chrony.conf content",
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_ntp",
            "config_path": execution_result.get("config_path")}
