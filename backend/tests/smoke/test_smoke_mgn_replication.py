# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# Requires env: MGN_SMOKE_API_TOKEN (or API_TOKEN), NEXPLANE_BASE_URL
# Requires: an asset with mgn_source_server_id in the platform DB
# Must be run from EC2 runner on Tailscale, not local Docker

"""
Smoke test: MGN Replication

Four phases:
  1. Auth & Discovery — find a nexplane_agent connector and an asset with mgn_source_server_id
  2. Create and Plan CR — create backup_mgn_replication CR, plan, submit, approve
  3. Execute and Verify — execute, poll until completed, assert execution_result shape
  4. Rollback — trigger rollback (no-op for read check), assert rolled_back=True

Run (inside nexplane-backend-1 on EC2):
    MGN_SMOKE_API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_mgn_replication.py -v -s

Prerequisites:
  - An asset registered in the platform with attribute/tag mgn_source_server_id
  - nexplane_agent connector with AWS credentials that have MGN read access
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
_STATE: dict = {}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _api_token() -> str:
    val = os.environ.get("MGN_SMOKE_API_TOKEN") or os.environ.get("API_TOKEN")
    if not val:
        pytest.skip("Neither MGN_SMOKE_API_TOKEN nor API_TOKEN is set — skipping MGN smoke test")
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
# Phase 1: Auth & Discovery
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("MGN_REPLICATION")
async def test_mgn_phase1_auth_and_discovery():
    """Find a nexplane_agent connector and an asset with mgn_source_server_id."""
    token = _api_token()
    jwt = await _get_jwt(token)
    headers = {"Authorization": f"Bearer {jwt}"}

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        # Find nexplane_agent connector
        r = await client.get("/connectors", headers=headers)
        assert r.status_code == 200, f"GET /connectors failed: {r.text}"
        connectors = r.json()

    agent_connectors = [c for c in connectors if c.get("connector_type") == "nexplane_agent"]
    if not agent_connectors:
        pytest.skip("No nexplane_agent connector registered — skipping MGN smoke test")

    connector = agent_connectors[0]
    _STATE["connector_id"] = connector["id"]

    # Find an asset with mgn_source_server_id
    jwt = await _get_jwt(token)
    headers = {"Authorization": f"Bearer {jwt}"}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        r = await client.get("/assets", headers=headers)
        assert r.status_code == 200, f"GET /assets failed: {r.text}"
        assets = r.json()

    mgn_asset = None
    mgn_server_id = None
    for asset in assets:
        # Check tags, attributes, and metadata for mgn_source_server_id
        tags = asset.get("tags") or {}
        attributes = asset.get("attributes") or {}
        metadata = asset.get("metadata") or {}
        sid = (
            tags.get("mgn_source_server_id")
            or attributes.get("mgn_source_server_id")
            or metadata.get("mgn_source_server_id")
        )
        if sid:
            mgn_asset = asset
            mgn_server_id = sid
            break

    if not mgn_asset:
        pytest.skip("No asset with mgn_source_server_id available — skipping MGN smoke test")

    _STATE["asset_id"] = mgn_asset["id"]
    _STATE["mgn_source_server_id"] = mgn_server_id
    _STATE["api_token"] = token

    assert _STATE["connector_id"]
    assert _STATE["mgn_source_server_id"]


# ---------------------------------------------------------------------------
# Phase 2: Create and Plan CR
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("MGN_REPLICATION")
async def test_mgn_phase2_create_and_plan_cr():
    """Create backup_mgn_replication CR, plan, submit for approval, and approve."""
    if not _STATE.get("connector_id"):
        pytest.skip("Phase 1 did not complete — skipping")

    token = _STATE["api_token"]
    jwt = await _get_jwt(token)
    headers = {"Authorization": f"Bearer {jwt}"}

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
        r = await client.post(
            "/change-requests",
            json={
                "title": "[smoke] MGN replication verification",
                "connector_id": _STATE["connector_id"],
                "change_type": "backup_mgn_replication",
                "desired_outcome": {"mgn_source_server_id": _STATE["mgn_source_server_id"]},
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"POST /change-requests failed {r.status_code}: {r.text}"
        cr = r.json()

    cr_id = cr.get("id") or cr.get("change_request", {}).get("id")
    assert cr_id, f"No CR id in response: {cr}"
    _STATE["cr_id"] = cr_id

    jwt = await _get_jwt(token)
    headers = {"Authorization": f"Bearer {jwt}"}

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed {r.status_code}: {r.text}"

        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed {r.status_code}: {r.text}"

        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "MGN replication smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# Phase 3: Execute and Verify
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("MGN_REPLICATION")
async def test_mgn_phase3_execute_and_verify():
    """Execute CR, poll until completed, verify execution_result structure."""
    if not _STATE.get("cr_id"):
        pytest.skip("Phase 2 did not complete — skipping")

    token = _STATE["api_token"]
    cr_id = _STATE["cr_id"]

    jwt = await _get_jwt(token)
    headers = {"Authorization": f"Bearer {jwt}"}

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
        r = await client.post(f"/change-requests/{cr_id}/execute", headers=headers)
        assert r.status_code == 200, f"POST /execute failed {r.status_code}: {r.text}"

    # Poll until completed, timeout 600s
    timeout = 600
    interval = 10
    attempts = timeout // interval
    exec_result = None

    for _ in range(attempts):
        await asyncio.sleep(interval)
        jwt = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200, f"GET /change-requests/{cr_id} failed: {r.text}"
            detail = r.json()

        status = detail.get("status")
        if status == "completed":
            runs = detail.get("execution_runs", [])
            if runs:
                latest = max(runs, key=lambda x: x.get("started_at") or "")
                raw = latest.get("result", {})
                steps = raw.get("execution", {}).get("steps", [])
                exec_result = steps[0]["result"] if steps else raw
            else:
                exec_result = {}
            break
        if status in ("failed", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            latest_result = runs[-1].get("result", {}) if runs else {}
            pytest.fail(
                f"CR {cr_id} (backup_mgn_replication) reached terminal failure: "
                f"status={status} | exec: {latest_result}"
            )

    if exec_result is None:
        pytest.fail(f"CR {cr_id} did not reach completed status within {timeout}s")

    _STATE["exec_result"] = exec_result

    # Structural assertions — we check the check ran, not that the server is healthy
    assert "replication_status" in exec_result, f"Missing replication_status: {exec_result}"
    assert "lag_seconds" in exec_result, f"Missing lag_seconds: {exec_result}"
    assert "source_server_id" in exec_result, f"Missing source_server_id: {exec_result}"

    valid_statuses = ("HEALTHY", "STALLED", "DISCONNECTED")
    assert exec_result["replication_status"] in valid_statuses, (
        f"replication_status={exec_result['replication_status']!r} not in {valid_statuses}"
    )


# ---------------------------------------------------------------------------
# Phase 4: Rollback
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("MGN_REPLICATION")
async def test_mgn_phase4_rollback():
    """Rollback the CR (no-op for read-only check) and assert rolled_back=True."""
    if not _STATE.get("cr_id"):
        pytest.skip("Phase 3 did not complete — skipping")

    token = _STATE["api_token"]
    cr_id = _STATE["cr_id"]

    jwt = await _get_jwt(token)
    headers = {"Authorization": f"Bearer {jwt}"}

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        r = await client.post(f"/change-requests/{cr_id}/rollback", headers=headers)
        assert r.status_code == 200, f"POST /rollback failed {r.status_code}: {r.text}"

    # Poll until rolled_back or rollback_failed
    timeout = 120
    interval = 5
    attempts = timeout // interval
    rb_result = None

    for _ in range(attempts):
        await asyncio.sleep(interval)
        jwt = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200
            detail = r.json()

        status = detail.get("status")
        if status in ("rolled_back", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            rollback_run = next(
                (run for run in reversed(runs) if "rollback" in (run.get("workflow_id") or "")),
                runs[-1] if runs else None,
            )
            raw = rollback_run.get("result", {}) if rollback_run else {}
            steps = raw.get("execution", {}).get("steps", [])
            rb_result = steps[0]["result"] if steps else raw
            break

    if rb_result is None:
        pytest.fail(f"Rollback for CR {cr_id} timed out after {timeout}s")

    assert rb_result.get("rolled_back") is True, f"Rollback did not confirm rolled_back=True: {rb_result}"
