from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": "configure_host_firewall", "tool": "iptables", "action_applied": parameters.get("action"), "snapshot": "# iptables-save output", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_host_firewall"}
