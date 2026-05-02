from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "change_ip",
        "interface": parameters.get("interface"),
        "mode": parameters.get("mode"),
        "ip_version": parameters.get("ip_version", "4"),
        "applied": True,
        "snapshot": {
            "interface": parameters.get("interface"),
            "ipv4": {"mode": "static", "address": "10.0.0.100/24", "gateway": "10.0.0.1"},
            "ipv6": {"mode": "dhcp", "address": None, "gateway": None},
            "dns_servers": ["8.8.8.8"],
        },
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "change_ip", "snapshot_restored": True}
