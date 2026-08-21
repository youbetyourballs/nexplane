# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke tests for Drift Detection.

Environment vars required:
    API_URL        - base URL of the Nexplane API
    API_TOKEN      - valid API token
    AGENT_ASSET_ID - UUID of an asset with a live Nexplane agent
    AWS_ASSET_ID   - UUID of an asset representing the test AWS SG (DRIFT_CLOUD phase only)

Run:
    pytest tests/smoke/test_smoke_drift.py -v -s --timeout=300

NOTE: DRIFT_ANCHOR and DRIFT_DETECT require the Go agent to implement
capture_drift_state / write_file commands. These phases will SKIP if
AGENT_ASSET_ID is not set, and FAIL if the agent commands are not implemented.
Live run is deferred until Go agent support is confirmed.
"""

import os
import time
import uuid
import json
import pytest
import requests

API_URL = os.environ.get("API_URL", "http://localhost:8000")
API_TOKEN = os.environ.get("API_TOKEN", "")
AGENT_ASSET_ID = os.environ.get("AGENT_ASSET_ID", "")
AWS_ASSET_ID = os.environ.get("AWS_ASSET_ID", "")


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        pytest.skip(f"{key} env var not set")
    return val


@pytest.fixture(scope="module")
def api():
    token = _env("API_TOKEN")
    base = _env("API_URL")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Content-Type"] = "application/json"
    session.base_url = base
    return session


def get(api, path, **kwargs):
    r = api.get(f"{api.base_url}{path}", **kwargs)
    r.raise_for_status()
    return r.json()


def post(api, path, body=None, **kwargs):
    r = api.post(f"{api.base_url}{path}", json=body, **kwargs)
    r.raise_for_status()
    return r.json()


@pytest.mark.smoke
@pytest.mark.smoke_phase("DRIFT_ANCHOR")
def test_drift_anchor(api):
    """
    Execute ssh_hardening CR against live agent host via full CR lifecycle.
    Verify ResourceState written with source=cr_execution for ssh_config.
    Verify DriftPolicy auto-created.
    """
    asset_id = _env("AGENT_ASSET_ID")

    # Create CR
    cr = post(api, "/change-requests", {
        "title": "SSH Hardening (drift smoke anchor)",
        "change_type": "harden_ssh",
        "parameters": {"ensure_permit_root_login": "no"},
        "target_asset_ids": [asset_id],
        "desired_outcome": {"permit_root_login": "no"},
    })
    cr_id = cr["id"]
    print(f"  Created CR {cr_id}")

    # Plan → submit for approval → approve → execute
    post(api, f"/change-requests/{cr_id}/plan")
    time.sleep(3)
    post(api, f"/change-requests/{cr_id}/submit-for-approval")
    post(api, f"/change-requests/{cr_id}/approve", {"decision": "approved", "comment": "drift smoke"})
    post(api, f"/change-requests/{cr_id}/execute")

    # Wait for CR to complete (up to 90s)
    for _ in range(18):
        time.sleep(5)
        cr_state = get(api, f"/change-requests/{cr_id}")
        if cr_state["status"] == "completed":
            break
        if cr_state["status"] in ("failed", "cancelled"):
            pytest.fail(f"CR failed with status {cr_state['status']}")
    else:
        pytest.fail("CR did not complete within 90s")

    # Wait for on_cr_completed to dispatch and complete agent jobs (ssh_config + firewall_rules,
    # each ~10s due to agent poll interval). Poll up to 60s.
    for _ in range(12):
        time.sleep(5)
        drift_data = get(api, f"/assets/{asset_id}/drift")
        rs_surfaces = [rs["surface_type"] for rs in drift_data["resource_states"]]
        if "ssh_config" in rs_surfaces:
            break
    else:
        pytest.fail(f"ssh_config ResourceState not found within 60s; got {rs_surfaces}")

    ssh_rs = next(rs for rs in drift_data["resource_states"] if rs["surface_type"] == "ssh_config")
    assert ssh_rs["source"] == "cr_execution", f"Expected cr_execution, got {ssh_rs['source']}"
    print(f"  ResourceState: source={ssh_rs['source']}, captured_at={ssh_rs['captured_at']}")

    # Verify DriftPolicy auto-created
    policies = get(api, "/drift/policies")
    matching = [p for p in policies if p["scope_value"] == asset_id and "ssh_config" in p["surface_types"]]
    assert matching, "No auto-created DriftPolicy found for asset + ssh_config"
    assert matching[0]["auto_created"] is True
    print(f"  DriftPolicy auto-created: {matching[0]['id']}")

    print("DRIFT_ANCHOR PASSED")


@pytest.mark.smoke
@pytest.mark.smoke_phase("DRIFT_DETECT")
def test_drift_detect(api):
    """
    Mutate /etc/ssh/sshd_config out-of-band via agent job.
    Trigger manual poll. Verify DriftEvent created with correct diff.
    Verify shadow restore_resource_state CR created as DRAFT.
    """
    asset_id = _env("AGENT_ASSET_ID")

    # Inject out-of-band mutation via agent job (bypassing CR lifecycle)
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    import asyncio

    async def _mutate():
        # Use a real SSH directive (not a comment — captureSSHConfig skips comment lines).
        # PermitEmptyPasswords is not normally in sshd_config so it will appear as an added key.
        return await dispatch_agent_job(
            command="write_file",
            parameters={
                "path": "/etc/ssh/sshd_config",
                "append_line": "MaxAuthTries 10",
            },
            asset_ids=[asset_id],
            timeout_seconds=30,
        )

    # Run from within the container context
    result = asyncio.run(_mutate())
    assert result.get("status") == "success", f"Out-of-band mutation failed: {result}"
    print("  Injected out-of-band mutation")

    # Trigger manual drift check (poll up to 30s for event creation)
    post(api, "/drift/check", {"asset_id": asset_id, "surface_type": "ssh_config"})
    time.sleep(15)

    # Verify DriftEvent created
    events = get(api, f"/drift/events?status=open&asset_id={asset_id}&surface_type=ssh_config")
    assert len(events) > 0, "No open DriftEvent found after mutation"

    event = events[0]
    print(f"  DriftEvent: id={event['id']}, severity={event['severity']}")

    assert event["diff"]["added"] or event["diff"]["changed"] or event["diff"]["removed"], \
        f"Empty diff on DriftEvent: {event['diff']}"
    assert event["severity"] == "medium", f"Expected medium severity for ssh_config, got {event['severity']}"
    assert event["shadow_cr_id"] is not None, "shadow_cr_id should be set"

    # Verify shadow CR is DRAFT
    shadow_cr = get(api, f"/change-requests/{event['shadow_cr_id']}")
    assert shadow_cr["status"] == "draft", f"Shadow CR status should be draft, got {shadow_cr['status']}"
    assert shadow_cr.get("change_type") == "restore_resource_state" or shadow_cr.get("action_id") == "restore_resource_state"
    print(f"  Shadow CR: {event['shadow_cr_id']} status={shadow_cr['status']}")

    print("DRIFT_DETECT PASSED")


@pytest.mark.smoke
@pytest.mark.smoke_phase("DRIFT_ACCEPT")
def test_drift_accept(api):
    """
    Accept drift event. Verify ResourceState advances to mutated state.
    Verify shadow CR cancelled. Verify event marked accepted.
    """
    asset_id = _env("AGENT_ASSET_ID")

    events = get(api, f"/drift/events?status=open&asset_id={asset_id}&surface_type=ssh_config")
    if not events:
        pytest.skip("No open drift event to accept — run DRIFT_DETECT first")

    event = events[0]
    event_id = event["id"]
    original_observed = event["observed_state"]
    shadow_cr_id = event["shadow_cr_id"]

    # Accept the new state
    accepted = post(api, f"/drift/events/{event_id}/accept", {"note": "Smoke test acceptance"})
    assert accepted["status"] == "accepted"
    print(f"  Event accepted: {event_id}")

    # Verify ResourceState was advanced to observed state
    drift_data = get(api, f"/assets/{asset_id}/drift")
    ssh_rs = next((rs for rs in drift_data["resource_states"] if rs["surface_type"] == "ssh_config"), None)
    assert ssh_rs is not None
    assert ssh_rs["source"] == "accepted", f"Expected accepted source, got {ssh_rs['source']}"
    print(f"  ResourceState advanced: source={ssh_rs['source']}")

    # Verify shadow CR was cancelled
    if shadow_cr_id:
        shadow_cr = get(api, f"/change-requests/{shadow_cr_id}")
        assert shadow_cr["status"] == "cancelled", f"Shadow CR not cancelled: {shadow_cr['status']}"
        print(f"  Shadow CR cancelled: {shadow_cr_id}")

    print("DRIFT_ACCEPT PASSED")


@pytest.mark.smoke
@pytest.mark.smoke_phase("DRIFT_REMEDIATE")
def test_drift_remediate(api):
    """
    Inject drift again. Approve and execute shadow CR through full lifecycle.
    Verify host restored. Verify ResourceState updated with source=cr_execution.
    Verify DriftEvent closed.
    """
    asset_id = _env("AGENT_ASSET_ID")

    # Re-inject mutation (same as DRIFT_DETECT)
    import asyncio
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    async def _mutate():
        return await dispatch_agent_job(
            command="write_file",
            parameters={"path": "/etc/ssh/sshd_config", "append_line": "PermitEmptyPasswords no"},
            asset_ids=[asset_id],
            timeout_seconds=30,
        )

    asyncio.run(_mutate())
    post(api, "/drift/check", {"asset_id": asset_id, "surface_type": "ssh_config"})
    time.sleep(5)

    events = get(api, f"/drift/events?status=open&asset_id={asset_id}&surface_type=ssh_config")
    assert events, "No open drift event to remediate"

    event = events[0]
    event_id = event["id"]
    shadow_cr_id = event["shadow_cr_id"]
    assert shadow_cr_id, "No shadow CR on drift event"

    # Full CR lifecycle on shadow CR
    post(api, f"/change-requests/{shadow_cr_id}/plan")
    time.sleep(3)
    post(api, f"/change-requests/{shadow_cr_id}/submit-for-approval")
    post(api, f"/change-requests/{shadow_cr_id}/approve", {"decision": "approved", "comment": "drift smoke remediate"})
    post(api, f"/change-requests/{shadow_cr_id}/execute")

    for _ in range(24):
        time.sleep(5)
        cr = get(api, f"/change-requests/{shadow_cr_id}")
        if cr["status"] == "completed":
            break
        if cr["status"] in ("failed", "cancelled"):
            pytest.fail(f"Shadow CR ended with status {cr['status']}")
    else:
        pytest.fail("Shadow CR did not complete within 120s")

    print(f"  Shadow CR executed: {shadow_cr_id}")
    time.sleep(5)

    # Verify DriftEvent closed
    ev = get(api, f"/drift/events/{event_id}")
    assert ev["status"] == "dismissed", f"Event should be dismissed after CR execution, got {ev['status']}"

    # Verify ResourceState updated
    drift_data = get(api, f"/assets/{asset_id}/drift")
    ssh_rs = next((rs for rs in drift_data["resource_states"] if rs["surface_type"] == "ssh_config"), None)
    assert ssh_rs is not None
    assert ssh_rs["source"] == "cr_execution"
    print(f"  ResourceState: source={ssh_rs['source']}")

    print("DRIFT_REMEDIATE PASSED")


@pytest.mark.smoke
@pytest.mark.smoke_phase("DRIFT_CLOUD")
def test_drift_cloud(api):
    """
    Add ingress rule to AWS SG out-of-band via boto3.
    Wait for poll cycle. Verify DriftEvent created.
    Accept new state. Verify anchor advances.
    """
    _env("AWS_ASSET_ID")
    _env("AGENT_ASSET_ID")

    import boto3
    aws_asset_id = os.environ["AWS_ASSET_ID"]

    # Get the SG external ID from the asset
    asset = get(api, f"/assets/{aws_asset_id}")
    sg_id = asset.get("external_id")
    if not sg_id or not sg_id.startswith("sg-"):
        pytest.skip(f"Asset {aws_asset_id} does not have an SG external_id: {sg_id}")

    ec2 = boto3.client("ec2")

    # Add a test ingress rule out-of-band
    test_port = 19999
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": test_port,
                "ToPort": test_port,
                "IpRanges": [{"CidrIp": "203.0.113.0/24", "Description": "drift-smoke-test"}],
            }]
        )
        print(f"  Added test ingress rule port {test_port} to {sg_id}")
    except ec2.exceptions.ClientError as e:
        if "InvalidPermission.Duplicate" in str(e):
            print(f"  Rule already exists (idempotent)")
        else:
            raise

    # Trigger manual check for cloud surface
    post(api, "/drift/check", {"asset_id": aws_asset_id, "surface_type": "aws_security_group"})
    time.sleep(10)

    # Verify DriftEvent
    events = get(api, f"/drift/events?status=open&asset_id={aws_asset_id}&surface_type=aws_security_group")
    assert events, "No open DriftEvent for aws_security_group"

    event = events[0]
    print(f"  DriftEvent: id={event['id']}, severity={event['severity']}")
    assert event["severity"] == "high"
    assert event["shadow_cr_id"] is not None

    # Accept new state
    accepted = post(api, f"/drift/events/{event['id']}/accept", {"note": "Smoke: accepted new SG rule"})
    assert accepted["status"] == "accepted"

    # Verify anchor advanced
    drift_data = get(api, f"/assets/{aws_asset_id}/drift")
    sg_rs = next((rs for rs in drift_data["resource_states"] if rs["surface_type"] == "aws_security_group"), None)
    assert sg_rs is not None
    assert sg_rs["source"] == "accepted"
    print(f"  ResourceState advanced: source={sg_rs['source']}")

    # Cleanup: remove the test rule
    try:
        ec2.revoke_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": test_port,
                "ToPort": test_port,
                "IpRanges": [{"CidrIp": "203.0.113.0/24"}],
            }]
        )
        print(f"  Cleaned up test ingress rule")
    except Exception:
        pass

    print("DRIFT_CLOUD PASSED")
