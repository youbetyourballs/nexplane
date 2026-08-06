# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: CockroachDB Cluster Upgrade

Two phases:
  ROLLING_UPGRADE  — CockroachDB 23.1 → 23.2 binary rolling upgrade (auto_finalize=false)
  ROLLBACK         — binary rollback before finalization

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_cockroachdb_cluster_upgrade.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AMI cached at /nexplane/smoke-amis/cockroachdb/23.1 (3-node CockroachDB 23.1).
  3. A Nexplane agent registered on the CockroachDB host.
  4. CRDB_NODES env var: JSON array of {host, http_port, sql_port}.
"""

import asyncio
import hashlib
import json
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_SOURCE_VERSION = "23.1"
_TARGET_VERSION = "23.2"
_AMI_SSM_PATH = "/nexplane/smoke-amis/cockroachdb/23.1"
_DEFAULT_NODES = [
    {"host": "127.0.0.1", "http_port": 8080, "sql_port": 26257},
    {"host": "127.0.0.1", "http_port": 8081, "sql_port": 26258},
    {"host": "127.0.0.1", "http_port": 8082, "sql_port": 26259},
]


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping cockroachdb smoke")
    return val


def _nodes() -> list:
    raw = os.environ.get("CRDB_NODES")
    if raw:
        return json.loads(raw)
    return _DEFAULT_NODES


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
                if meta.get("tags", {}).get("smoke_role") == "cockroachdb":
                    return str(asset.id)
        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                return str(asset.id)

    pytest.skip("No server asset with active AgentRegistration found for CockroachDB smoke")


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "cockroachdb smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _wait_cr_terminal(jwt: str, cr_id: str, timeout: int = 1800) -> dict:
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


async def _rollback_cr(jwt: str, cr_id: str, timeout: int = 1800) -> dict:
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
# PHASE: ROLLING_UPGRADE — CockroachDB 23.1 → 23.2 (auto_finalize=false)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ROLLING_UPGRADE")
async def test_cockroachdb_rolling_upgrade():
    """Rolling upgrade CockroachDB 23.1 → 23.2, auto_finalize=false.

    Verifies:
    - CR reaches completed/awaiting_finalize status
    - All nodes listed in nodes_upgraded
    - version_finalized == False (since auto_finalize=False)
    - Rollback succeeds (binary_rollback strategy)
    """
    token = _env("API_TOKEN")
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    jwt = await _get_jwt(token)
    asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="cockroachdb_cluster_upgrade",
        asset_id=asset_id,
        title=f"[smoke] CockroachDB {_SOURCE_VERSION} → {_TARGET_VERSION} (no auto_finalize)",
        parameters={
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "nodes": _nodes(),
            "sql_user": os.environ.get("CRDB_SQL_USER", "root"),
            "sql_password": os.environ.get("CRDB_SQL_PASSWORD"),
            "auto_finalize": False,
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
    detail = await _wait_cr_terminal(jwt, cr_id, timeout=1800)
    assert detail["status"] in ("completed", "awaiting_finalize"), (
        f"CR status unexpected: {detail.get('status')} | {detail}"
    )

    runs = detail.get("execution_runs", [])
    latest = max(runs, key=lambda x: x.get("started_at") or "")
    raw = latest.get("result", {})
    inner = raw.get("execution", raw)
    steps = inner.get("steps", [])
    exec_result = steps[0]["result"] if steps else inner

    assert exec_result.get("version_finalized") is False, (
        f"version_finalized should be False (auto_finalize=False): {exec_result}"
    )
    nodes_upgraded = exec_result.get("nodes_upgraded", [])
    assert len(nodes_upgraded) == len(_nodes()), (
        f"Not all nodes upgraded: {nodes_upgraded}"
    )

    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=cr_id)
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("strategy") == "binary_rollback", f"Wrong rollback strategy: {rb}"
