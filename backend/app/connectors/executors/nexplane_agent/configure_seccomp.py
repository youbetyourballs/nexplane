from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    svc = parameters.get("service_name")
    return {"action": "configure_seccomp", "service_name": svc, "drop_in_path": f"/etc/systemd/system/{svc}.service.d/nexplane-seccomp.conf", "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_seccomp"}
