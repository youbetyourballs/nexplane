# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Cloud Account Baseline Monitoring

Four phases — one per cloud. Each phase:
  1. Create + approve + execute the baseline monitoring CR
  2. Verify the execution result has expected structure
  3. Trigger rollback
  4. Verify rollback completed

Run (on EC2 inside nexplane-backend-1 container):
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_cloud_baseline_monitoring.py -v -s \\
      --smoke-phase AWS_BASELINE

Individual phases:
    pytest tests/smoke/test_smoke_cloud_baseline_monitoring.py -v -s -k "aws"
    pytest tests/smoke/test_smoke_cloud_baseline_monitoring.py -v -s -k "gcp"
    pytest tests/smoke/test_smoke_cloud_baseline_monitoring.py -v -s -k "azure"
    pytest tests/smoke/test_smoke_cloud_baseline_monitoring.py -v -s -k "oci"

Prerequisites:
  1. API_TOKEN holds a valid nxp_... token with admin/approver role.
  2. Connectors for AWS / GCP / Azure / OCI registered in the platform.
     If no connector is configured the executor falls back to mock mode and
     phases still pass (mock path returns rolled_back=True, empty undone list).
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_STATE: dict = {}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping cloud baseline smoke test")
    return val


async def _get_jwt(api_token: str) -> str:
    """Derive a short-lived JWT from an nxp_... API token."""
    from app.database import AsyncSessionLocal
    from app.models.api_token import ApiToken
    from app.services.auth_service import create_access_token
    from sqlalchemy import select

    token_hash = hashlib.sha256(api_token.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked == False,  # noqa: E712
            )
        )
        tok = r.scalar_one()
        return create_access_token(subject=str(tok.user_id))


# ---------------------------------------------------------------------------
# CR lifecycle helpers
# ---------------------------------------------------------------------------

async def _plan_and_approve_cr_via_rest(token: str, cr_id: str) -> None:
    """Generate plan, submit for approval, then approve via REST."""
    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}

        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed {r.status_code}: {r.text}"

        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed {r.status_code}: {r.text}"

        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "cloud baseline smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed {r.status_code}: {r.text}"


async def _rollback_cr_via_rest(token: str, cr_id: str) -> dict:
    """Trigger rollback via REST and return the rollback result."""
    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=120) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/rollback", headers=headers)
        assert r.status_code == 200, f"POST /rollback failed {r.status_code}: {r.text}"
        return r.json()


async def _create_and_execute_baseline_cr(
    token: str,
    change_type: str,
    title: str,
    connector_type: str,
    timeout: int = 300,
) -> dict:
    """
    Resolve connector, create CR, approve, execute, poll until completed.
    Returns the completed CR detail dict.
    """
    from app.mcp_tools.change_requests import (
        create_change_request,
        execute_change_request,
        get_change_request,
    )

    jwt = await _get_jwt(token)

    # Discover cloud account asset matching the connector type
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.get(
            "/assets",
            params={"asset_type": "cloud_account"},
            headers=headers,
        )
        assert r.status_code == 200, f"GET /assets failed: {r.text}"
        accounts = r.json()

    # Filter by connector type if possible; fall back to first available
    matching = [a for a in accounts if a.get("connector_type") == connector_type]
    asset = matching[0] if matching else (accounts[0] if accounts else None)
    assert asset is not None, (
        f"No cloud_account asset found for connector_type={connector_type}. "
        "Register a connector or set ASSET_ID."
    )
    asset_id = asset["id"]

    cr = await create_change_request(
        token=token,
        change_type=change_type,
        asset_id=asset_id,
        title=title,
        parameters={},
    )
    assert "id" in cr, f"create_change_request({change_type}) failed: {cr}"
    cr_id = cr["id"]

    await _plan_and_approve_cr_via_rest(token=token, cr_id=cr_id)

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request({change_type}) failed: {executed}"

    interval = 10
    attempts = timeout // interval
    jwt = await _get_jwt(token)
    for _ in range(attempts):
        await asyncio.sleep(interval)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200, f"GET /change-requests/{cr_id} failed: {r.text}"
            detail = r.json()
        status = detail.get("status")
        if status == "completed":
            # Merge execution_result from latest execution_run into detail
            runs = detail.get("execution_runs", [])
            if runs:
                latest = max(runs, key=lambda x: x.get("started_at") or "")
                detail["execution_result"] = latest.get("result", {})
            else:
                detail["execution_result"] = {}
            return detail
        if status in ("failed", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            latest_result = runs[-1].get("result", {}) if runs else {}
            pytest.fail(f"CR {cr_id} ({change_type}) reached terminal failure: {detail} | exec: {latest_result}")
    pytest.fail(f"CR {cr_id} ({change_type}) timed out after {timeout}s")


# ---------------------------------------------------------------------------
# PHASE: AWS_BASELINE
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_BASELINE")
async def test_aws_baseline_monitoring_execute_and_rollback():
    """
    AWS: enable CloudTrail, GuardDuty, SecurityHub, Config, S3 public-access block,
    IAM password policy. Verify summary structure and rollback.
    """
    token = _env("API_TOKEN")

    detail = await _create_and_execute_baseline_cr(
        token=token,
        change_type="aws_account_baseline_monitoring",
        title="[smoke] AWS baseline monitoring",
        connector_type="aws",
    )

    assert detail["status"] == "completed", f"CR not completed: {detail}"
    exec_result = detail.get("execution_result", {})

    # Structural checks
    assert "phases" in exec_result, f"Missing 'phases' in execution_result: {exec_result}"
    assert len(exec_result["phases"]) == 5, (
        f"Expected 5 phases, got {len(exec_result['phases'])}"
    )
    assert exec_result.get("promote_to") == "aws_account_baseline_hardening"
    assert "rollback_data" in exec_result
    assert isinstance(exec_result["rollback_data"].get("newly_enabled"), list)

    summary = exec_result.get("summary", {})
    assert "newly_enabled" in summary
    assert "already_enabled" in summary
    assert "skipped_with_warning" in summary
    assert "failed" in summary

    cr_id = detail["id"]

    # Rollback
    rb = await _rollback_cr_via_rest(token=token, cr_id=cr_id)
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"

    undone = rb.get("undone", [])
    assert len(undone) == len(exec_result["rollback_data"]["newly_enabled"]), (
        f"undone count {len(undone)} != newly_enabled count "
        f"{len(exec_result['rollback_data']['newly_enabled'])}"
    )
    assert all(u.get("rolled_back") for u in undone), (
        f"Some items failed rollback: {[u for u in undone if not u.get('rolled_back')]}"
    )


# ---------------------------------------------------------------------------
# PHASE: GCP_BASELINE
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("GCP_BASELINE")
async def test_gcp_baseline_monitoring_execute_and_rollback():
    """
    GCP: enable Cloud Audit Logs, SCC, VPC Flow Logs, Cloud DNS query logging.
    SCC may appear in skipped_with_warning if service account is project-scoped.
    """
    token = _env("API_TOKEN")

    detail = await _create_and_execute_baseline_cr(
        token=token,
        change_type="gcp_account_baseline_monitoring",
        title="[smoke] GCP baseline monitoring",
        connector_type="gcp",
    )

    assert detail["status"] == "completed", f"CR not completed: {detail}"
    exec_result = detail.get("execution_result", {})

    assert "phases" in exec_result, f"Missing 'phases' in execution_result: {exec_result}"
    assert exec_result.get("promote_to") == "gcp_account_baseline_hardening"

    summary = exec_result.get("summary", {})
    assert "newly_enabled" in summary
    assert "already_enabled" in summary
    # skipped_with_warning is present even if empty (SCC without org perms lands here)
    assert "skipped_with_warning" in summary
    assert "failed" in summary

    cr_id = detail["id"]

    rb = await _rollback_cr_via_rest(token=token, cr_id=cr_id)
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"


@pytest.mark.smoke
@pytest.mark.smoke_phase("GCP_BASELINE")
@pytest.mark.xfail(
    reason="SCC requires org-level permissions; xfail when service account is project-scoped"
)
async def test_gcp_scc_org_level_enabled():
    """
    Assert SCC appears in newly_enabled (not skipped_with_warning).
    This requires the service account to have org-level SCC permissions.
    Marked xfail — passes only if SA has sufficient scope.
    """
    token = _env("API_TOKEN")

    detail = await _create_and_execute_baseline_cr(
        token=token,
        change_type="gcp_account_baseline_monitoring",
        title="[smoke] GCP SCC org-level check",
        connector_type="gcp",
    )

    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    newly = summary.get("newly_enabled", [])
    skipped = [s.get("service") for s in summary.get("skipped_with_warning", [])]
    cr_id = detail["id"]

    try:
        assert "scc" in newly, (
            f"SCC not in newly_enabled={newly}; skipped_with_warning={skipped}"
        )
    except AssertionError:
        pytest.xfail("SCC requires org-level permissions — skipped as expected")
    finally:
        # Rollback regardless of assertion outcome
        await _rollback_cr_via_rest(token=token, cr_id=cr_id)


# ---------------------------------------------------------------------------
# PHASE: AZURE_BASELINE
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AZURE_BASELINE")
async def test_azure_baseline_monitoring_execute_and_rollback():
    """
    Azure: enable Defender for Cloud, diagnostic settings, security defaults.
    Defender may already be enabled (Standard tier) — accepted in already_enabled.
    """
    token = _env("API_TOKEN")

    detail = await _create_and_execute_baseline_cr(
        token=token,
        change_type="azure_account_baseline_monitoring",
        title="[smoke] Azure baseline monitoring",
        connector_type="azure",
    )

    assert detail["status"] == "completed", f"CR not completed: {detail}"
    exec_result = detail.get("execution_result", {})

    assert "phases" in exec_result, f"Missing 'phases' in execution_result: {exec_result}"
    assert exec_result.get("promote_to") == "azure_account_baseline_hardening"

    summary = exec_result.get("summary", {})
    assert "newly_enabled" in summary
    assert "already_enabled" in summary
    assert "skipped_with_warning" in summary
    assert "failed" in summary

    # Defender must be accounted for in one of the two positive buckets
    assert (
        "defender" in summary["newly_enabled"]
        or "defender" in summary["already_enabled"]
    ), (
        f"Defender not in newly_enabled or already_enabled: {summary}"
    )

    cr_id = detail["id"]

    rb = await _rollback_cr_via_rest(token=token, cr_id=cr_id)
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"

    undone = rb.get("undone", [])
    failed = [u for u in undone if not u.get("rolled_back")]
    assert not failed, f"Some rollback items failed: {failed}"


# ---------------------------------------------------------------------------
# PHASE: OCI_BASELINE
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("OCI_BASELINE")
async def test_oci_baseline_monitoring_execute_and_rollback():
    """
    OCI: enable Cloud Guard, audit retention, VCN flow logs, IAM password policy.
    VCN flow logs may appear in skipped_with_warning if no VCNs exist in the tenancy.
    """
    token = _env("API_TOKEN")

    detail = await _create_and_execute_baseline_cr(
        token=token,
        change_type="oci_account_baseline_monitoring",
        title="[smoke] OCI baseline monitoring",
        connector_type="oci",
    )

    assert detail["status"] == "completed", f"CR not completed: {detail}"
    exec_result = detail.get("execution_result", {})

    assert "phases" in exec_result, f"Missing 'phases' in execution_result: {exec_result}"
    assert exec_result.get("promote_to") == "oci_account_baseline_hardening"

    summary = exec_result.get("summary", {})
    assert "newly_enabled" in summary
    assert "already_enabled" in summary
    assert "skipped_with_warning" in summary
    assert "failed" in summary

    # Cloud Guard must be accounted for in one of the two positive buckets
    assert (
        "cloud_guard" in summary["newly_enabled"]
        or "cloud_guard" in summary["already_enabled"]
    ), (
        f"cloud_guard not in newly_enabled or already_enabled: {summary}"
    )

    # VCN flow logs: acceptable if skipped_with_warning when no VCNs exist
    skipped_services = [s.get("service") for s in summary.get("skipped_with_warning", [])]
    vcn_accounted = (
        "vcn_flow_logs" in summary["newly_enabled"]
        or "vcn_flow_logs" in summary["already_enabled"]
        or "vcn_flow_logs" in skipped_services
    )
    assert vcn_accounted, (
        f"vcn_flow_logs not in newly_enabled, already_enabled, or skipped_with_warning: {summary}"
    )

    cr_id = detail["id"]

    rb = await _rollback_cr_via_rest(token=token, cr_id=cr_id)
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"

    undone = rb.get("undone", [])
    failed = [u for u in undone if not u.get("rolled_back")]
    assert not failed, f"Some rollback items failed: {failed}"
