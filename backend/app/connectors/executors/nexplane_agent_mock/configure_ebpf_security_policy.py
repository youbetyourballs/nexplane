from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    fw = parameters.get("framework", "falco")
    return {"action": "configure_ebpf_security_policy", "framework": fw, "policy_path": f"/etc/nexplane/ebpf/{fw}-policy.yaml", "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_ebpf_security_policy"}
