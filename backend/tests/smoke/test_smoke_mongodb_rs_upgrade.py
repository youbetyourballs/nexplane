# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: MongoDB Replica Set Upgrade

Two phases:
  RS_UPGRADE — MongoDB 6.0 → 7.0 RS upgrade via Nexplane agent
  ROLLBACK   — verify before FCV: mongodump restore succeeds;
               after FCV: rollback surfaces irreversibility clearly

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_mongodb_rs_upgrade.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AMI cached at /nexplane/smoke-amis/mongodb-rs/6.0 (3-node MongoDB 6.0 RS).
  3. A Nexplane agent registered on the MongoDB primary host.
  4. MONGO_RS_MEMBERS env var: JSON array of {host, port, priority}.
"""

import asyncio
import hashlib
import json
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_SOURCE_VERSION = "6.0"
_TARGET_VERSION = "7.0"
_AMI_SSM_PATH = "/nexplane/smoke-amis/mongodb-rs/6.0"
_DEFAULT_RS_MEMBERS = [
    {"host": "127.0.0.1", "port": 27017, "priority": 2},
    {"host": "127.0.0.1", "port": 27018, "priority": 1},
    {"host": "127.0.0.1", "port": 27019, "priority": 1},
]


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping mongodb RS smoke")
    return val


def _rs_members() -> list:
    raw = os.environ.get("MONGO_RS_MEMBERS")
    if raw:
        return json.loads(raw)
    return _DEFAULT_RS_MEMBERS


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
                if meta.get("tags", {}).get("smoke_role") == "mongodb":
                    return str(asset.id)
        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                return str(asset.id)

    pytest.skip("No server asset with active AgentRegistration found for MongoDB smoke")


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "mongodb smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _wait_cr_terminal(jwt: str, cr_id: str, timeout: int = 3600) -> dict:
    interval = 15
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


async def _rollback_cr(jwt: str, cr_id: str, timeout: int = 7200) -> dict:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"POST /rollback failed: {r.text}"

    detail = await _wait_cr_terminal(jwt, cr_id, timeout)
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
# PHASE: RS_UPGRADE — MongoDB 6.0 → 7.0
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("RS_UPGRADE")
async def test_mongodb_rs_upgrade():
    """Upgrade MongoDB RS 6.0 → 7.0 via FCV hop chain.

    Verifies:
    - CR reaches completed status
    - fcv_set == True in execution result
    - fcv_chain == ['6.0', '7.0']
    - verify_result.fcv_matches == True
    - Rollback surfaces irreversibility (rolled_back == False, reason contains 'FCV')
    """
    token = _env("API_TOKEN")
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    jwt = await _get_jwt(token)
    asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="mongodb_rs_upgrade",
        asset_id=asset_id,
        title=f"[smoke] MongoDB RS {_SOURCE_VERSION} → {_TARGET_VERSION}",
        parameters={
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "rs_members": _rs_members(),
            "admin_user": os.environ.get("MONGO_ADMIN_USER", "admin"),
            "admin_password": os.environ.get("MONGO_ADMIN_PASSWORD"),
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
    detail = await _wait_cr_terminal(jwt, cr_id, timeout=3600)
    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')} | {detail}"

    runs = detail.get("execution_runs", [])
    latest = max(runs, key=lambda x: x.get("started_at") or "")
    raw = latest.get("result", {})
    inner = raw.get("execution", raw)
    steps = inner.get("steps", [])
    exec_result = steps[0]["result"] if steps else inner

    assert exec_result.get("fcv_set") is True, f"fcv_set should be True: {exec_result}"
    assert exec_result.get("fcv_chain") == [_SOURCE_VERSION, _TARGET_VERSION], (
        f"fcv_chain mismatch: {exec_result}"
    )
    verify = exec_result.get("verify_result", {})
    assert verify.get("fcv_matches") is True, f"FCV not matching target: {verify}"

    # Rollback should surface irreversibility (FCV already set)
    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=cr_id)
    assert rb.get("rolled_back") is False, (
        f"Expected rolled_back=False after FCV set, got: {rb}"
    )
    reason = rb.get("reason", "")
    assert "FCV" in reason or "irreversible" in reason.lower(), (
        f"Rollback reason should mention FCV irreversibility: {rb}"
    )
    assert rb.get("dump_archive_path"), f"dump_archive_path should be surfaced: {rb}"
