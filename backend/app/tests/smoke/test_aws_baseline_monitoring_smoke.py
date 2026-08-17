# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""AWS account baseline monitoring live smoke test.

Verifies execute→rollback lifecycle against live AWS:
  - GuardDuty detector created (or skipped if already enabled)
  - Security Hub enabled
  - CloudTrail trail created and logging
  - Config recorder started
  - Rollback restores pre-existing state

Runs against the default AWS connector (ID: 666e237d) unless
SMOKE_AWS_CONNECTOR_ID is set. Skips if credentials are absent.
"""

import boto3
import pytest
from app.connectors.executors.aws.aws_account_baseline_monitoring import (
    execute,
    rollback,
)


def _boto_client(creds: dict, service: str, region: str = "us-east-1"):
    return boto3.client(
        service,
        region_name=region,
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        aws_session_token=creds.get("session_token"),
    )


async def test_aws_baseline_monitoring_execute_and_rollback(live_aws_connector):
    creds = live_aws_connector.credentials

    # ── Execute ───────────────────────────────────────────────────────────────
    result = await execute({}, [], live_aws_connector)

    assert not result.get("mock"), "Executor ran in mock mode — credentials not loaded"

    phases = result["phases"]
    phase_names = [p["phase"] for p in phases]
    assert "preflight" in phase_names
    assert "snapshot" in phase_names
    assert "enable" in phase_names
    assert "verify" in phase_names
    assert "report" in phase_names

    verify_phase = next(p for p in phases if p["phase"] == "verify")
    failed_checks = verify_phase.get("failed_checks", [])
    assert not failed_checks, f"Verify phase has failed checks: {failed_checks}"

    summary = result["summary"]
    # At least one service should have been acted on (enabled or skipped)
    acted = set(summary.get("newly_enabled", [])) | set(summary.get("already_enabled", []))
    assert acted, "No services were enabled or skipped — nothing happened"

    # If GuardDuty was newly enabled, verify the detector exists live
    rd = result.get("rollback_data", {})
    gd_items = [i for i in rd.get("newly_enabled", []) if i["service"] == "guardduty"]
    for item in gd_items:
        region = item["region"]
        did = item["detector_id"]
        gd = _boto_client(creds, "guardduty", region)
        det = gd.get_detector(DetectorId=did)
        assert det["Status"] == "ENABLED", f"GuardDuty detector {did} not enabled in {region}"

    # If CloudTrail was newly created, verify it is logging
    ct_items = [i for i in rd.get("newly_enabled", []) if i["service"] == "cloudtrail"]
    if ct_items:
        ct = _boto_client(creds, "cloudtrail", "us-east-1")
        status = ct.get_trail_status(Name="nexplane-baseline")
        assert status["IsLogging"], "CloudTrail trail is not logging after execute"

    # ── Rollback ──────────────────────────────────────────────────────────────
    rb = await rollback({}, result, live_aws_connector)

    undone = rb.get("undone", [])
    failed_rb = [u for u in undone if not u.get("rolled_back")]
    assert not failed_rb, f"Rollback failed for: {failed_rb}"
    assert rb.get("rolled_back") is True, "Rollback did not report success"

    # Verify GuardDuty detectors we created are gone
    for item in gd_items:
        region = item["region"]
        did = item["detector_id"]
        gd = _boto_client(creds, "guardduty", region)
        detectors = gd.list_detectors().get("DetectorIds", [])
        assert did not in detectors, f"GuardDuty detector {did} still exists after rollback in {region}"

    # Verify CloudTrail trail is gone if we created it
    if ct_items:
        ct = _boto_client(creds, "cloudtrail", "us-east-1")
        trails = ct.describe_trails(includeShadowTrails=False).get("trailList", [])
        trail_names = [t["Name"] for t in trails]
        assert "nexplane-baseline" not in trail_names, "CloudTrail trail still exists after rollback"
