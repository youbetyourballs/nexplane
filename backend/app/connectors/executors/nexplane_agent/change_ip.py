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
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def _get_probe_url(asset_ids: list) -> str:
    """Return the backend's Tailscale URL for the dead-man's-switch probe.

    Reads the nexplane_url stored in org settings (set during agent deploy).
    Falls back to the Tailscale IP stored on the asset's AgentRegistration.
    """
    if not asset_ids:
        return ""
    import uuid
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration
    from app.models.asset import Asset

    asset_id = uuid.UUID(asset_ids[0]) if isinstance(asset_ids[0], str) else asset_ids[0]
    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if not asset:
            return ""
        reg_result = await db.execute(
            select(AgentRegistration).where(
                AgentRegistration.asset_id == asset_id,
                AgentRegistration.organization_id == asset.organization_id,
            )
        )
        reg = reg_result.scalar_one_or_none()
        if reg and getattr(reg, "control_plane_url", None):
            return reg.control_plane_url
    return ""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:

    # Derive mode if not explicitly set: static when new_ip_v4 is provided, else dhcp.
    mode = parameters.get("mode") or ("static" if parameters.get("new_ip_v4") else "dhcp")

    # Inject probe_url for dead-man's-switch if not provided by caller.
    probe_url = parameters.get("probe_url") or ""
    if not probe_url and parameters.get("method") in ("commit_timer", "auto", None, ""):
        probe_url = await _get_probe_url(asset_ids)

    commit_timer_seconds = int(parameters.get("commit_timer_seconds", 30))

    agent_params = {
        # Core parameters (existing)
        "interface": parameters.get("interface"),
        "mode": mode,
        "ip_version": parameters.get("ip_version", "4"),
        "new_ip_v4": parameters.get("new_ip_v4"),
        "new_ip_v6": parameters.get("new_ip_v6"),
        "new_gateway_v4": parameters.get("new_gateway_v4"),
        "new_gateway_v6": parameters.get("new_gateway_v6"),
        # New v2 parameters
        "method": parameters.get("method", "auto"),
        "commit_timer_seconds": commit_timer_seconds,
        "probe_interval_seconds": int(parameters.get("probe_interval_seconds", 5)),
        "probe_url": probe_url,
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
        timeout_seconds=commit_timer_seconds + 120,
    )
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore previous network configuration from snapshot in execution_result."""
    snapshot = execution_result.get("snapshot", {})
    rollback_params = {
        "snapshot": snapshot,
        "interface": parameters.get("interface"),
    }

    # asset_ids comes from the original CR target_asset_ids stored in parameters by the workflow.
    asset_ids = parameters.get("_asset_ids") or parameters.get("target_asset_ids") or []

    result = await dispatch_agent_job(
        command="change_ip_rollback",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
    return {"rolled_back": True, "action": "change_ip", "agent_result": result}
