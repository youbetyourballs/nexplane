# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: SSH CA Rotation

Phases:
  SSH_CA_ROTATION  — run 7-phase ssh_ca_rotation against a live agent host
  SSH_CA_ROLLBACK  — rollback restores TrustedUserCAKeys on all hosts

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_ssh_ca_rotation.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. At least one server asset with an active Nexplane agent registered.
  3. Agent host must have ssh-keygen available (standard on any Linux host).
  4. /etc/ssh/ must be writable by the agent process user (or sudo configured).
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"

_state: dict = {}


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


async def _find_agent_asset_id(jwt: str) -> str:
    """Discover a server asset with an active Nexplane agent (checked in within 24h)."""
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration
    from app.models.asset import Asset, AssetType
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select

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
                if meta.get("os_type", "linux") == "linux":
                    return str(asset.id)

        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                return str(asset.id)

    pytest.skip("No server asset with active Nexplane agent found — skipping SSH CA rotation smoke")


async def _plan_and_approve_cr(jwt: str, cr_id: str, comment: str = "smoke self-approval") -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": comment},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _poll_cr(jwt: str, cr_id: str, token: str, timeout: int = 300) -> dict:
    interval = 8
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
            pytest.fail(f"CR {cr_id} reached terminal failure: {detail}")
    pytest.fail(f"CR {cr_id} timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Phase: SSH_CA_ROTATION
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("SSH_CA_ROTATION")
async def test_ssh_ca_rotation_live():
    """Run ssh_ca_rotation against a live agent host; verify 7-phase execution result."""
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)
    asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="catalog_action",
        asset_id=asset_id,
        title="[smoke] SSH CA rotation",
        parameters={
            "connector_type": "nexplane_agent",
            "action_id": "ssh_ca_rotation",
            "params": {
                "key_type": "ed25519",
                "ca_key_path": "/etc/ssh/nexplane_ca_smoke",
                "trusted_user_ca_keys_path": "/etc/ssh/trusted_user_ca_keys_smoke",
            },
        },
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    _state["asset_id"] = asset_id

    await _plan_and_approve_cr(jwt=jwt, cr_id=cr_id, comment="ssh-ca-rotation smoke self-approval")

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute failed: {executed}"

    detail = await _poll_cr(jwt=jwt, cr_id=cr_id, token=token, timeout=300)
    result = detail.get("execution_result", {})

    # Verify all 7 phases completed
    expected_phases = ["preflight", "snapshot", "generate", "distribute", "verify", "revoke", "report"]
    phases_present = result.get("phases", [])
    for phase in expected_phases:
        assert phase in phases_present, (
            f"Expected phase '{phase}' in result phases: {phases_present}\nFull result: {result}"
        )

    # Verify CA was generated
    assert result.get("new_ca_fingerprint"), f"Missing new_ca_fingerprint in result: {result}"
    assert result.get("new_ca_comment"), f"Missing new_ca_comment in result: {result}"

    # Verify hosts were updated
    hosts_updated = result.get("hosts_updated", [])
    assert len(hosts_updated) > 0, (
        f"No hosts updated in result: {result}"
    )

    # Snapshot stored for rollback
    assert result.get("old_ca_snapshots") is not None, (
        f"Missing old_ca_snapshots in result: {result}"
    )

    print(
        f"\nSSH CA rotation complete: new CA {result.get('new_ca_fingerprint')}, "
        f"{len(hosts_updated)} host(s) updated"
    )


# ---------------------------------------------------------------------------
# Phase: SSH_CA_ROLLBACK
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("SSH_CA_ROLLBACK")
async def test_ssh_ca_rotation_rollback():
    """Rollback restores TrustedUserCAKeys from snapshot on all hosts."""
    if "cr_id" not in _state:
        pytest.skip("SSH_CA_ROTATION phase did not run — skipping rollback phase")

    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)
    cr_id = _state["cr_id"]

    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"Rollback request failed: {r.text}"

    interval = 8
    for _ in range(180 // interval):
        await asyncio.sleep(interval)
        refreshed = await _get_jwt(token)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {refreshed}"},
            )
            detail = r.json()
        status = detail.get("status")
        if status == "rolled_back":
            break
        if status == "rollback_failed":
            pytest.fail(f"Rollback failed: {detail}")
    else:
        pytest.fail(f"Rollback timed out for CR {cr_id}")

    runs = detail.get("execution_runs", [])
    rollback_run = next(
        (run for run in reversed(runs) if "rollback" in (run.get("workflow_id") or "")),
        runs[-1] if runs else None,
    )
    raw = rollback_run.get("result", {}) if rollback_run else {}
    inner = raw.get("execution", raw)

    assert inner.get("rolled_back") is True, (
        f"Rollback result did not confirm rolled_back=True: {inner}"
    )

    hosts_restored = inner.get("hosts_restored", [])
    print(f"\nSSH CA rollback complete: {len(hosts_restored)} host(s) restored")
