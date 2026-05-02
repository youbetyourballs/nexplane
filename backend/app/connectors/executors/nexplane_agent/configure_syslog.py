from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "configure_syslog",
        "destination_host": parameters.get("destination_host"),
        "destination_port": parameters.get("destination_port"),
        "protocol": parameters.get("protocol"),
        "applied": True,
        "config_backup": "# previous syslog config snapshot",
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_syslog", "config_restored": True}
