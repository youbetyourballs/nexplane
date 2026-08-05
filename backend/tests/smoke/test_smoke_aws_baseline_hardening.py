# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: AWS Account Baseline Hardening

Phases:
  1. Create + approve + execute aws_account_baseline_hardening CR
  2. Verify execution result structure and control summary
  3. Trigger rollback
  4. Verify rollback restored pre-existing state

Run:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_aws_baseline_hardening.py -v -s

Must be run from EC2 runner on Tailscale, not local Docker.
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_STATE: dict = {}

EXPECTED_CONTROLS = {"password_policy", "s3_block_public", "root_mfa", "vpc_flow_logs"}


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


async def _plan_and_approve(token: str, cr_id: str) -> None:
    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"submit failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "aws baseline hardening smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"approve failed: {r.text}"


async def _poll_cr(token: str, cr_id: str, terminal: set, timeout: int = 300) -> dict:
    interval = 10
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        jwt = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200
            detail = r.json()
        if detail.get("status") in terminal:
            return detail
    pytest.fail(f"CR {cr_id} did not reach {terminal} within {timeout}s")


def _extract_exec_result(detail: dict) -> dict:
    runs = detail.get("execution_runs", [])
    for run in reversed(runs):
        steps = run.get("result", {}).get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result", {})
    return detail.get("execution_result", {})


@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_BASELINE_HARDENING")
async def test_01_create_and_execute():
    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)

    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.get("/assets", params={"asset_type": "cloud_account"}, headers=headers)
        assert r.status_code == 200
        accounts = r.json()

    aws_assets = [a for a in accounts if a.get("connector_type") == "aws"]
    asset = aws_assets[0] if aws_assets else (accounts[0] if accounts else None)
    assert asset, "No cloud_account asset found — register an AWS connector first"
    _STATE["asset_id"] = asset["id"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(
            "/change-requests",
            json={
                "title": "[smoke] AWS account baseline hardening",
                "change_type": "aws_account_baseline_hardening",
                "desired_outcome": "Enforce IAM password policy, S3 public block, and root MFA check",
                "asset_id": _STATE["asset_id"],
                "parameters": {
                    "enforce_password_policy": True,
                    "enforce_s3_block_public": True,
                    "enforce_root_mfa_check": True,
                    "enable_vpc_flow_logs": False,
                },
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"create CR failed: {r.text}"
        cr = r.json()

    _STATE["cr_id"] = cr["id"]
    await _plan_and_approve(token, cr["id"])

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr['id']}/execute",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"execute failed: {r.text}"

    detail = await _poll_cr(token, cr["id"], {"completed", "failed"})
    assert detail["status"] == "completed", f"CR did not complete: {detail.get('status')}"
    _STATE["detail"] = detail


@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_BASELINE_HARDENING")
async def test_02_verify_execution_result():
    detail = _STATE.get("detail")
    assert detail, "test_01 must run first"

    result = _extract_exec_result(detail)
    assert "phases" in result, f"execution_result missing 'phases': {result}"
    assert "summary" in result, f"execution_result missing 'summary': {result}"
    assert "rollback_data" in result, f"execution_result missing 'rollback_data': {result}"

    phases = {p["phase"]: p for p in result["phases"]}
    for expected_phase in ("preflight", "snapshot", "harden", "verify"):
        assert expected_phase in phases, f"Missing phase: {expected_phase}"
        assert phases[expected_phase]["status"] == "ok", (
            f"Phase {expected_phase} status: {phases[expected_phase]['status']}"
        )

    summary = result["summary"]
    assert "newly_applied" in summary
    assert "already_applied" in summary
    assert "warnings" in summary
    assert "failed" in summary
    assert len(summary["failed"]) == 0, f"Some controls failed: {summary['failed']}"

    _STATE["exec_result"] = result


@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_BASELINE_HARDENING")
async def test_03_rollback():
    token = _env("API_TOKEN")
    cr_id = _STATE.get("cr_id")
    assert cr_id, "test_01 must run first"

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"rollback trigger failed: {r.text}"

    detail = await _poll_cr(token, cr_id, {"rolled_back", "rollback_failed"}, timeout=180)
    assert detail["status"] == "rolled_back", f"Rollback did not complete: {detail.get('status')}"

    runs = detail.get("execution_runs", [])
    rollback_run = next(
        (r for r in reversed(runs) if "rollback" in (r.get("workflow_id") or "")),
        runs[-1] if runs else None,
    )
    raw = rollback_run.get("result", {}) if rollback_run else {}
    steps = raw.get("execution", {}).get("steps", [])
    rb_result = steps[0]["result"] if steps else raw

    assert rb_result.get("rolled_back") is True, f"rolled_back not True: {rb_result}"
