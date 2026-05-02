from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": "deploy_ebpf_policy", "program_path": parameters.get("program_path"), "attach_type": parameters.get("attach_type"), "pin_path": "/sys/fs/bpf/nexplane_prog", "snapshot": {"pin_path": "/sys/fs/bpf/nexplane_prog"}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "deploy_ebpf_policy"}
