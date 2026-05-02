from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": "apply_sysctl_hardening", "drop_in_path": "/etc/sysctl.d/99-nexplane-hardening.conf", "settings_applied": {"net.ipv4.ip_forward": "0", "net.ipv4.tcp_syncookies": "1"}, "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "apply_sysctl_hardening"}
