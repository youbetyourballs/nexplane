# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Ceph Cluster Upgrade

Two phases:
  UPGRADE  — Ceph 18.2 (Reef) → 19.2 (Squid) upgrade via Nexplane agent
  ROLLBACK — verify rollback surfaces daemon state and irreversibility

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_ceph_cluster_upgrade.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AMI cached at /nexplane/smoke-amis/ceph/18.2 (3-node Ceph Reef cluster).
  3. A Nexplane agent registered on the Ceph admin host.
  4. CEPH_MGR_HOSTS, CEPH_MON_HOSTS, CEPH_OSD_HOSTS env vars:
     JSON arrays of {host} for each daemon type.
"""

import asyncio
import hashlib
import json
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_SOURCE_VERSION = "18.2"
_TARGET_VERSION = "19.2"
_AMI_SSM_PATH = "/nexplane/smoke-amis/ceph/18.2"
_DEFAULT_HOSTS = [{"host": "127.0.0.1"}]


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping ceph smoke")
    return val


def _host_list(env_key: str) -> list:
    raw = os.environ.get(env_key)
    if raw:
        return json.loads(raw)
    return _DEFAULT_HOSTS


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
                if meta.get("tags", {}).get("smoke_role") == "ceph":
                    return str(asset.id)
        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                return str(asset.id)

    pytest.skip("No server asset with active AgentRegistration found for Ceph smoke")


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "ceph smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _wait_cr_terminal(jwt: str, cr_id: str, timeout: int = 7200) -> dict:
    interval = 30
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


async def _rollback_cr(jwt: str, cr_id: str, timeout: int = 300) -> dict:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"POST /rollback failed: {r.text}"

    # Ceph rollback is synchronous (surfaces state immediately, no async ops)
    await asyncio.sleep(5)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.get(
            f"/change-requests/{cr_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        detail = r.json()

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
# PHASE: UPGRADE — Ceph 18.2 (Reef) → 19.2 (Squid)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("UPGRADE")
async def test_ceph_cluster_upgrade():
    """Upgrade Ceph cluster 18.2 → 19.2 (Reef → Squid).

    Verifies:
    - CR reaches completed or verify_failed status (health may still be HEALTH_WARN post-upgrade)
    - All daemon types appear in daemons_upgraded
    - noout_unset == True
    - Rollback surfaces irreversibility (rolled_back == False, strategy == partial_rollback)
    - Rollback surfaces manual_steps and daemons_on_target_version
    """
    token = _env("API_TOKEN")
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    jwt = await _get_jwt(token)
    asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="ceph_cluster_upgrade",
        asset_id=asset_id,
        title=f"[smoke] Ceph {_SOURCE_VERSION} (Reef) → {_TARGET_VERSION} (Squid)",
        parameters={
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "mgr_hosts": _host_list("CEPH_MGR_HOSTS"),
            "mon_hosts": _host_list("CEPH_MON_HOSTS"),
            "osd_hosts": _host_list("CEPH_OSD_HOSTS"),
            "rgw_hosts": json.loads(os.environ.get("CEPH_RGW_HOSTS", "[]")),
            "mds_hosts": json.loads(os.environ.get("CEPH_MDS_HOSTS", "[]")),
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
    detail = await _wait_cr_terminal(jwt, cr_id, timeout=7200)
    # Ceph may finish with verify_failed if cluster is still settling (HEALTH_WARN is acceptable)
    assert detail["status"] in ("completed", "verify_failed"), (
        f"CR reached unexpected status: {detail.get('status')} | {detail}"
    )

    runs = detail.get("execution_runs", [])
    latest = max(runs, key=lambda x: x.get("started_at") or "")
    raw = latest.get("result", {})
    inner = raw.get("execution", raw)
    steps = inner.get("steps", [])
    exec_result = steps[0]["result"] if steps else inner

    daemons_upgraded = exec_result.get("daemons_upgraded", [])
    assert any("mgr:" in d for d in daemons_upgraded), f"No MGR daemons upgraded: {daemons_upgraded}"
    assert any("mon:" in d for d in daemons_upgraded), f"No MON daemons upgraded: {daemons_upgraded}"
    assert any("osd:" in d for d in daemons_upgraded), f"No OSD daemons upgraded: {daemons_upgraded}"
    assert exec_result.get("noout_unset") is True, f"noout_unset should be True: {exec_result}"

    # Rollback: Ceph surfaces irreversibility
    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=cr_id)
    assert rb.get("rolled_back") is False, (
        f"Ceph rollback should surface irreversibility (rolled_back=False): {rb}"
    )
    assert rb.get("strategy") == "partial_rollback", f"Wrong rollback strategy: {rb}"
    assert rb.get("daemons_on_target_version"), f"daemons_on_target_version should be surfaced: {rb}"
    assert rb.get("manual_steps"), f"manual_steps should be surfaced: {rb}"
    reason = rb.get("reason", "")
    assert "downgrade" in reason.lower() or "not support" in reason.lower(), (
        f"Reason should explain Ceph downgrade limitation: {rb}"
    )
