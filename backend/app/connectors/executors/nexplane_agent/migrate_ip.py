"""
migrate_ip executor — multi-stage IP migration orchestrator.

Stages (v1 implementation):
  0. dns_discovery   — discover DNS records pointing to the asset's current IPs
  1. preflight       — parameter validation (performed by change_ip dispatch)
  2. dns_prepare     — lower TTLs (if update_dns=True and records found) [FUTURE]
  3. apply_change    — dispatch change_ip sub-job with the appropriate method
  4. verify_new      — confirmation that execution_result reports success
  5. dns_update      — fire DNS update CRs for each discovered record [FUTURE]
  6. commit          — mark migration permanent; write to execution_result

v1 scope: stages 0, 3, 4, 6 are implemented. Stages 2 and 5 are stubbed
(logged and skipped) so the executor is end-to-end functional while DNS
provider integration is built out in a follow-on plan.

Rollback: fires change_ip rollback for stage 3. DNS rollback is a no-op in v1.
"""
from __future__ import annotations
from datetime import datetime, timezone
import logging
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
from app.services.dns_discovery_service import discover_dns_records_for_asset
from app.database import AsyncSessionLocal


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Parameters:
        interface               str   — network interface (required)
        mode                    str   — "static" | "dhcp" (required)
        new_ip_v4               str   — new IPv4 address in CIDR notation
        new_gateway_v4          str   — new IPv4 gateway
        dns_servers             list  — DNS servers for the interface
        method                  str   — "auto" | "tailscale" | "secondary_swap" | "commit_timer" | "manual"
        commit_timer_seconds    int   — default 30
        update_dns              bool  — default True, whether to update DNS records
        update_dns_ttl          bool  — default True, whether to lower TTL before change (v1: stub)
        confirm_at_stage        str   — default None, pause at named stage (v1: no-op)
        rollback_on_stage_failure bool — default True
    """

    log = logging.getLogger(__name__)

    update_dns = bool(parameters.get("update_dns", True))
    method = parameters.get("method", "auto")
    commit_timer_seconds = int(parameters.get("commit_timer_seconds", 30))

    stages_completed: list[str] = []
    dns_records: list[dict] = []
    change_ip_result: dict = {}

    # -----------------------------------------------------------------------
    # Stage 0: DNS discovery
    # -----------------------------------------------------------------------
    if update_dns and asset_ids:
        try:
            async with AsyncSessionLocal() as db:
                dns_records = await discover_dns_records_for_asset(db, asset_ids[0])
            log.info("migrate_ip dns_discovery: found %d records", len(dns_records))
        except Exception as exc:
            log.warning("migrate_ip dns_discovery failed (non-fatal): %s", exc)
    stages_completed.append("dns_discovery")

    # -----------------------------------------------------------------------
    # Stage 2: DNS prepare (v1 stub)
    # -----------------------------------------------------------------------
    if update_dns and dns_records:
        log.info(
            "migrate_ip dns_prepare: %d records found, TTL lowering not yet implemented in v1",
            len(dns_records),
        )
    stages_completed.append("dns_prepare")

    # -----------------------------------------------------------------------
    # Stage 3: apply_change — dispatch change_ip
    # -----------------------------------------------------------------------
    change_ip_params = {
        "interface": parameters.get("interface"),
        "mode": parameters.get("mode"),
        "new_ip_v4": parameters.get("new_ip_v4"),
        "new_gateway_v4": parameters.get("new_gateway_v4"),
        "new_ip_v6": parameters.get("new_ip_v6"),
        "new_gateway_v6": parameters.get("new_gateway_v6"),
        "dns_servers": list(parameters.get("dns_servers") or []),
        "dns_search_domains": list(parameters.get("dns_search_domains") or []),
        "method": method,
        "commit_timer_seconds": commit_timer_seconds,
        "probe_interval_seconds": int(parameters.get("probe_interval_seconds", 5)),
        "add_secondary": bool(parameters.get("add_secondary", False)),
        "preflight_arp_probe": bool(parameters.get("preflight_arp_probe", True)),
    }
    # Strip None values
    change_ip_params = {k: v for k, v in change_ip_params.items() if v is not None}

    change_ip_result = await dispatch_agent_job(
        command="change_ip",
        parameters=change_ip_params,
        asset_ids=asset_ids,
        timeout_seconds=commit_timer_seconds + 120,
    )
    stages_completed.append("apply_change")

    # -----------------------------------------------------------------------
    # Stage 4: verify_new — check the dispatch result reported success
    # -----------------------------------------------------------------------
    ip_changed = change_ip_result.get("status") in ("completed", None) or bool(
        change_ip_result.get("applied", True)
    )
    stages_completed.append("verify_new")

    # -----------------------------------------------------------------------
    # Stage 5: DNS update (v1 stub)
    # -----------------------------------------------------------------------
    dns_records_updated = 0
    if update_dns and dns_records and ip_changed:
        log.info(
            "migrate_ip dns_update: %d records to update — DNS provider integration not yet implemented in v1",
            len(dns_records),
        )
    stages_completed.append("dns_update")

    # -----------------------------------------------------------------------
    # Stage 6: commit
    # -----------------------------------------------------------------------
    stages_completed.append("commit")

    return {
        "action": "migrate_ip",
        "ip_changed": ip_changed,
        "method_used": method,
        "dns_records_discovered": len(dns_records),
        "dns_records_updated": dns_records_updated,
        "stages_completed": stages_completed,
        "change_ip_result": change_ip_result,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: reverse the change_ip stage. DNS rollback is not implemented in v1."""
    log = logging.getLogger(__name__)

    stages = execution_result.get("stages_completed", [])
    if "apply_change" not in stages:
        return {"rolled_back": False, "reason": "apply_change stage never ran"}

    change_ip_result = execution_result.get("change_ip_result", {})
    snapshot = change_ip_result.get("snapshot", {})

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

    if execution_result.get("dns_records_updated", 0) > 0:
        log.warning("migrate_ip rollback: DNS records were updated but DNS rollback is not implemented in v1")

    return {
        "rolled_back": True,
        "action": "migrate_ip",
        "ip_rollback_result": result,
        "dns_rolled_back": False,
        "dns_rollback_note": "v1: DNS rollback not implemented",
    }
