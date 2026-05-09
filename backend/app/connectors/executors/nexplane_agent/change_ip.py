"""change_ip executor — dispatches to the Nexplane Go agent.

New parameters (v2, all optional and backward-compatible):
    method                  str   "auto" | "tailscale" | "secondary_swap" | "commit_timer" | "manual"
    commit_timer_seconds    int   default 30 — dead-man's-switch window
    probe_interval_seconds  int   default 5 — how often commit-timer polls control plane
    add_secondary           bool  default False — keep old IP as secondary after swap
    dns_servers             list  default [] — DNS servers to configure on the interface
    dns_search_domains      list  default [] — DNS search domains
    preflight_arp_probe     bool  default True — ARP-probe destination IP before applying
"""
from __future__ import annotations
from datetime import datetime, timezone
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:

    agent_params = {
        # Core parameters (existing)
        "interface": parameters.get("interface"),
        "mode": parameters.get("mode"),
        "ip_version": parameters.get("ip_version", "4"),
        "new_ip_v4": parameters.get("new_ip_v4"),
        "new_ip_v6": parameters.get("new_ip_v6"),
        "new_gateway_v4": parameters.get("new_gateway_v4"),
        "new_gateway_v6": parameters.get("new_gateway_v6"),
        # New v2 parameters
        "method": parameters.get("method", "auto"),
        "commit_timer_seconds": int(parameters.get("commit_timer_seconds", 30)),
        "probe_interval_seconds": int(parameters.get("probe_interval_seconds", 5)),
        "add_secondary": bool(parameters.get("add_secondary", False)),
        "dns_servers": list(parameters.get("dns_servers") or []),
        "dns_search_domains": list(parameters.get("dns_search_domains") or []),
        "preflight_arp_probe": bool(parameters.get("preflight_arp_probe", True)),
    }
    # Strip None values so the Go agent receives a clean params dict
    agent_params = {k: v for k, v in agent_params.items() if v is not None}

    result = await dispatch_agent_job(
        command="change_ip",
        parameters=agent_params,
        asset_ids=asset_ids,
        timeout_seconds=int(parameters.get("commit_timer_seconds", 30)) + 120,
    )
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore previous network configuration from snapshot in execution_result."""
    snapshot = execution_result.get("snapshot", {})
    rollback_params = {
        "previous_result": snapshot,
        "interface": parameters.get("interface"),
    }

    result = await dispatch_agent_job(
        command="change_ip_rollback",
        parameters=rollback_params,
        asset_ids=parameters.get("_asset_ids", []),
        timeout_seconds=120,
    )
    return {"rolled_back": True, "action": "change_ip", "agent_result": result}
