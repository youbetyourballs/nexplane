from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": parameters.get("action"), "mechanism": parameters.get("mechanism", "registry"), "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "enforce_powershell_clm"}
