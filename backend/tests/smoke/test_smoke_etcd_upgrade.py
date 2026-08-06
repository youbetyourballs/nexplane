# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: etcd Cluster Upgrade

Two phases:
  ROLLING_UPGRADE — etcd 3.4 → 3.5 rolling member upgrade via Nexplane agent
  ROLLBACK        — verify etcdctl snapshot restore succeeds

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_etcd_upgrade.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AMI cached at /nexplane/smoke-amis/etcd/3.4 (3-member etcd 3.4 cluster).
  3. A Nexplane agent registered on the etcd host.
  4. ETCD_MEMBERS env var: JSON array of {name, peer_url, client_url}.
"""

import asyncio
import hashlib
import json
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_SOURCE_VERSION = "3.4"
_TARGET_VERSION = "3.5"
_AMI_SSM_PATH = "/nexplane/smoke-amis/etcd/3.4"
_DEFAULT_MEMBERS = [
    {"name": "etcd0", "peer_url": "http://127.0.0.1:2380", "client_url": "http://127.0.0.1:2379"},
    {"name": "etcd1", "peer_url": "http://127.0.0.1:2382", "client_url": "http://127.0.0.1:2381"},
    {"name": "etcd2", "peer_url": "http://127.0.0.1:2384", "client_url": "http://127.0.0.1:2383"},
]


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping etcd smoke")
    return val


def _members() -> list:
    raw = os.environ.get("ETCD_MEMBERS")
    if raw:
        return json.loads(raw)
    return _DEFAULT_MEMBERS


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


async def _find_agent_asset_id(jwt: str) -> str:
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration
    from app.models.asset import Asset, AssetType
    from sqlalchemy import select
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(AgentRegistration)
            .where(AgentRegistration.last_seen > cutoff)
            .order_by(AgentRegistration.last_seen.desc())
            .limit(20)
        )
        registrations = r.scalars().all()
        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                meta = asset.asset_metadata or {}
                if meta.get("tags", {}).get("smoke_role") == "etcd":
                    return str(asset.id)
        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                return str(asset.id)

    pytest.skip("No server asset with active AgentRegistration found for etcd smoke")


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "etcd smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _wait_cr_terminal(jwt: str, cr_id: str, timeout: int = 900) -> dict:
    interval = 10
    detail = {}
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            assert r.status_code == 200
            detail = r.json()
        status = detail.get("status")
        if status in ("completed", "failed", "rolled_back", "rollback_failed"):
            return detail
    pytest.fail(f"CR {cr_id} timed out after {timeout}s, last status: {detail.get('status')}")


async def _rollback_cr(jwt: str, cr_id: str, timeout: int = 900) -> dict:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"POST /rollback failed: {r.text}"

    detail = await _wait_cr_terminal(jwt, cr_id, timeout)
    assert detail.get("status") == "rolled_back", f"Rollback status: {detail.get('status')}"

    runs = detail.get("execution_runs", [])
    rollback_run = next(
        (run for run in reversed(runs) if "rollback" in (run.get("workflow_id") or "")),
        runs[-1] if runs else None,
    )
    raw = rollback_run.get("result", {}) if rollback_run else {}
    inner = raw.get("execution", raw)
    steps = inner.get("steps", [])
    return steps[0]["result"] if steps else inner


# ---------------------------------------------------------------------------
# PHASE: ROLLING_UPGRADE — etcd 3.4 → 3.5
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROLLING_UPGRADE")
async def test_etcd_rolling_upgrade():
    """Rolling upgrade etcd 3.4 → 3.5.

    Verifies:
    - CR reaches completed status
    - All members in members_upgraded list
    - verify_result.all_members_healthy == True
    - snapshot_path set in execution result
    - Rollback succeeds (etcd_snapshot_restore strategy)
    - Rollback surfaces data loss warning
    """
    token = _env("API_TOKEN")
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    jwt = await _get_jwt(token)
    asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="etcd_upgrade",
        asset_id=asset_id,
        title=f"[smoke] etcd {_SOURCE_VERSION} → {_TARGET_VERSION} rolling upgrade",
        parameters={
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "members": _members(),
            "dry_run": False,
        },
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]

    jwt = await _get_jwt(token)
    await _plan_and_approve_cr(jwt=jwt, cr_id=cr_id)

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request failed: {executed}"

    jwt = await _get_jwt(token)
    detail = await _wait_cr_terminal(jwt, cr_id, timeout=900)
    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')} | {detail}"

    runs = detail.get("execution_runs", [])
    latest = max(runs, key=lambda x: x.get("started_at") or "")
    raw = latest.get("result", {})
    inner = raw.get("execution", raw)
    steps = inner.get("steps", [])
    exec_result = steps[0]["result"] if steps else inner

    assert exec_result.get("target_version") == _TARGET_VERSION, f"target_version mismatch: {exec_result}"
    assert exec_result.get("snapshot_path"), f"snapshot_path should be set: {exec_result}"
    members_upgraded = exec_result.get("members_upgraded", [])
    expected_names = [m["name"] for m in _members()]
    for name in expected_names:
        assert name in members_upgraded, f"Member {name} not in members_upgraded: {members_upgraded}"
    verify = exec_result.get("verify_result", {})
    assert verify.get("all_members_healthy") is True, f"Not all members healthy: {verify}"

    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=cr_id)
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("strategy") == "etcd_snapshot_restore", f"Wrong rollback strategy: {rb}"
    assert rb.get("warning"), f"Data loss warning should be surfaced: {rb}"
