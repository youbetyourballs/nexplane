# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Privileged Account Audit

Phases:
  AD_AUDIT — run privileged_account_audit CR against live AD; verify findings structure

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_privileged_account_audit.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. Active Directory connector registered in the platform.
  3. DC accessible on the platform VPC (use DC AMI).
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set")
    return val


async def _get_jwt(api_token: str) -> str:
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


async def _find_connector_id(jwt: str, connector_type: str) -> str:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.get(
            "/connectors",
            params={"connector_type": connector_type},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"GET /connectors failed: {r.text}"
        items = r.json()
    matching = [c for c in items if c.get("connector_type") == connector_type]
    if not matching:
        pytest.skip(f"No connector of type '{connector_type}' found — skipping smoke")
    return str(matching[0]["id"])


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "privileged-audit smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _poll_cr(jwt: str, cr_id: str, token: str, timeout: int = 180) -> dict:
    interval = 5
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        refreshed = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {refreshed}"},
            )
            detail = r.json()
        status = detail.get("status")
        if status == "completed":
            runs = detail.get("execution_runs", [])
            if runs:
                latest = max(runs, key=lambda x: x.get("started_at") or "")
                raw = latest.get("result", {})
                inner = raw.get("execution", raw)
                steps = inner.get("steps", [])
                detail["execution_result"] = steps[0]["result"] if steps else inner
            return detail
        if status in ("failed", "rollback_failed"):
            pytest.fail(f"CR {cr_id} failed: {detail}")
    pytest.fail(f"CR {cr_id} timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Phase: AD_AUDIT
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AD_AUDIT")
async def test_privileged_account_audit_live():
    """Run privileged_account_audit against live AD; verify findings structure and rollback noop."""
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)
    connector_id = await _find_connector_id(jwt, "active_directory")

    # Create CR
    cr = await create_change_request(
        token=token,
        change_type="catalog_action",
        asset_id=None,
        title="[smoke] privileged account audit",
        parameters={
            "connector_type": "active_directory",
            "action_id": "privileged_account_audit",
            "connector_id": connector_id,
            "params": {
                "stale_threshold_days": 90,
                "generate_remediations": True,
            },
        },
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]

    await _plan_and_approve_cr(jwt=jwt, cr_id=cr_id)

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute failed: {executed}"

    detail = await _poll_cr(jwt=jwt, cr_id=cr_id, token=token, timeout=120)
    result = detail.get("execution_result", {})

    # Verify structure
    assert "group_summary" in result, f"Missing group_summary in result: {result}"
    assert "accounts" in result, f"Missing accounts in result: {result}"
    assert "risky_accounts" in result, f"Missing risky_accounts in result: {result}"
    assert "proposed_remediations" in result, f"Missing proposed_remediations: {result}"
    assert "audited_at" in result, f"Missing audited_at: {result}"

    # If the executor returned an error (e.g., DC unreachable), skip rather than fail
    if "error" in result:
        pytest.skip(f"AD audit returned error (DC unreachable?): {result['error']}")

    # group_summary should have the four privileged groups
    group_summary = result["group_summary"]
    for group in ("Domain Admins", "Administrators"):
        assert group in group_summary, f"Expected {group} in group_summary: {group_summary}"

    # All risky accounts should have required fields
    for acc in result.get("risky_accounts", []):
        assert "sAMAccountName" in acc, f"Missing sAMAccountName: {acc}"
        assert "risk_flags" in acc, f"Missing risk_flags: {acc}"
        assert "severity" in acc, f"Missing severity: {acc}"

    # All proposed remediations should have required fields
    for rem in result.get("proposed_remediations", []):
        assert "change_type" in rem, f"Missing change_type in remediation: {rem}"
        assert "parameters" in rem, f"Missing parameters in remediation: {rem}"
        assert "reason" in rem, f"Missing reason in remediation: {rem}"

    print(
        f"\nAudit complete: {len(result['accounts'])} accounts scanned, "
        f"{len(result['risky_accounts'])} risky, "
        f"{len(result['proposed_remediations'])} remediations proposed"
    )

    # Rollback is a no-op — verify it succeeds cleanly
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"Rollback request failed: {r.text}"
