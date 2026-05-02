from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"nla": parameters.get("require_nla", True), "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "harden_rdp"}
