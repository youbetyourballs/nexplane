from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    hosts = [{"asset_id": a, "agent_installed": True, "agent_version": "8.12.0"} for a in asset_ids]
    return {"action": "install_agent", "agent_type": parameters.get("agent_type"), "hosts": hosts, "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    hosts = execution_result.get("hosts", [])
    return {"rolled_back": True, "action": "agent_uninstalled", "hosts_cleaned": [h["asset_id"] for h in hosts], "completed_at": datetime.now(timezone.utc).isoformat()}
