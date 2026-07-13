# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

# Pass thresholds (spec-mandated):
# - HTTP latency: ≤ 150% of baseline p95
# - DB row count: within 5% of baseline sample
# - Library version: ≥ baseline version (downgrade = warning, not fail)
# Failure in Application or Data layers sets failed=True (triggers FILO rollback by workflow).
# Failure in Infrastructure or Service layers is surfaced as warning only.


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    asset_id = asset_ids[0] if asset_ids else None

    result = await _dispatch.dispatch_agent_job(
        command="verify_against_baseline",
        parameters={
            "asset_id": asset_id,
            "target_host": parameters.get("target_host"),
            "target_port_offset": int(parameters.get("target_port_offset", 0)),
            "latency_threshold_pct": 150,
            "row_count_tolerance_pct": 5,
        },
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )

    report = result.get("report", {})
    layers = report.get("layers", {})

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
