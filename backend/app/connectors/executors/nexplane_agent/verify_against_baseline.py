# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

# Pass thresholds (spec-mandated):
# - HTTP latency: ≤ 150% of baseline p95
# - DB row count: within 5% of baseline sample
# - Library version: ≥ baseline version (downgrade = warning, not fail)
# Failure in Application or Data layers sets failed=True (triggers FILO rollback by workflow).
# Failure in Infrastructure or Service layers is surfaced as warning only.


async def _resolve_agent_asset_id(profile_asset_id: str, org_id) -> str:
    """
    Resolve the server asset ID that has an agent registered,
    given a profile asset ID. Falls back to finding any agent in the org.
    """
    try:
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.agent import AgentRegistration

        async with AsyncSessionLocal() as db:
            # First try: is there an agent registered directly for this asset?
            reg = await db.execute(
                select(AgentRegistration).where(
                    AgentRegistration.asset_id == profile_asset_id,
                    AgentRegistration.organization_id == org_id,
                ).limit(1)
            )
            if reg.scalar_one_or_none():
                return profile_asset_id

            # Fallback: find any server asset with a registered agent in this org
            reg2 = await db.execute(
                select(AgentRegistration).where(
                    AgentRegistration.organization_id == org_id,
                ).order_by(AgentRegistration.last_seen.desc()).limit(1)
            )
            r = reg2.scalar_one_or_none()
            if r:
                return str(r.asset_id)
    except Exception:
        pass
    return profile_asset_id


async def _load_profile_baseline(asset_id: str) -> dict:
    """Load application_profile asset metadata and build a baseline dict for verify."""
    try:
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.asset import Asset

        async with AsyncSessionLocal() as db:
            row = await db.execute(select(Asset).where(Asset.id == asset_id))
            asset = row.scalars().first()
            if not asset:
                return {}
            meta = asset.asset_metadata or {}
            # Build a minimal baseline from the discovery profile
            endpoints = meta.get("endpoints", [])
            # Only build HTTP checks for ports with a web-server process or known HTTP ports.
            # Non-HTTP system ports (DHCP 68, NTP 323, DHCPv6 546, Tailscale 41641, etc.)
            # are checked at the TCP layer by the service layer, not the application layer.
            _HTTP_PROCESSES = {"nginx", "gunicorn", "uvicorn", "python", "python3", "node",
                               "ruby", "java", "caddy", "apache", "httpd", "flask", "fastapi"}
            _HTTP_PORTS = {80, 443, 8000, 8080, 8443, 3000, 4000, 4443, 5000, 9000, 9090}
            baseline_endpoints = []
            for ep in endpoints:
                port = ep.get("port", 0)
                if not port or port in (22,):
                    continue
                proc = (ep.get("process") or "").lower()
                is_web = port in _HTTP_PORTS or any(p in proc for p in _HTTP_PROCESSES)
                if is_web:
                    baseline_endpoints.append({
                        "url": f"http://localhost:{port}/health",
                        "port": port,
                        "status_code": 200,
                        "response_ms_p95": 5000,
                        "content_signature": "200",
                    })
            deps = meta.get("dependencies", [])
            baseline_deps = []
            for dep in deps:
                baseline_deps.append({
                    "target": f"{dep.get('host', 'localhost')}:{dep.get('port', 0)}",
                    "type": dep.get("type", "http"),
                    "row_count_sample": dep.get("row_count_sample", 0),
                    "confidence": dep.get("confidence", "runtime_only"),
                })
            return {
                "endpoints": baseline_endpoints,
                "dependencies": baseline_deps,
                "services": meta.get("services", []),
            }
    except Exception:
        return {}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    import uuid
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    profile_asset_id = asset_ids[0] if asset_ids else None

    # Resolve the org_id from the profile asset so we can find the agent
    org_id = None
    try:
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, uuid.UUID(str(profile_asset_id)))
            if asset:
                org_id = asset.organization_id
    except Exception:
        pass

    # Find the server asset that has a registered agent
    agent_asset_id = await _resolve_agent_asset_id(profile_asset_id, org_id) if org_id else profile_asset_id

    # Load the stored profile to build baseline for comparison
    baseline = await _load_profile_baseline(profile_asset_id) if profile_asset_id else {}

    result = await _dispatch.dispatch_agent_job(
        command="verify_against_baseline",
        parameters={
            "asset_id": agent_asset_id,
            "baseline": baseline,
            "target_host": parameters.get("target_host"),
            "target_port_offset": int(parameters.get("target_port_offset", 0)),
            "latency_threshold_pct": 150,
            "row_count_tolerance_pct": 5,
        },
        asset_ids=[agent_asset_id],
        timeout_seconds=120,
    )

    report = result.get("report", {})
    layers = result.get("layers", report.get("layers", {}))

    application_passed = layers.get("application", {}).get("passed", True)
    data_passed = layers.get("data", {}).get("passed", True)
    failed = not (application_passed and data_passed)

    return {
        "action": "verify_against_baseline",
        "failed": failed,
        "layers": layers,
        "report": report,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "verify_against_baseline is non-mutating — FILO unwind triggered by workflow"}
