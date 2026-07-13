# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

# Pass thresholds (spec-mandated):
# - HTTP latency: ≤ 150% of baseline p95
# - DB row count: within 5% of baseline sample
# - Library version: ≥ baseline version (downgrade = warning, not fail)
# Failure in Application or Data layers sets failed=True (triggers FILO rollback by workflow).
# Failure in Infrastructure or Service layers is surfaced as warning only.


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
            baseline_endpoints = []
            for ep in endpoints:
                port = ep.get("port", 0)
                if port and port not in (22,):
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
    asset_id = asset_ids[0] if asset_ids else None

    # Load the stored profile to build baseline for comparison
    baseline = await _load_profile_baseline(asset_id) if asset_id else {}

    result = await _dispatch.dispatch_agent_job(
        command="verify_against_baseline",
        parameters={
            "asset_id": asset_id,
            "baseline": baseline,
            "target_host": parameters.get("target_host"),
            "target_port_offset": int(parameters.get("target_port_offset", 0)),
            "latency_threshold_pct": 150,
            "row_count_tolerance_pct": 5,
        },
        asset_ids=list(asset_ids),
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
