# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Cross-Cloud Container Image Transfer

Two phases:
  AGENT_TRANSFER — ECR → ECR via Nexplane agent docker pull/tag/push
  ACR_IMPORT     — ECR → ACR via Azure ACR import API (server-side)

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_container_image_transfer.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AWS (ECR) connector registered in the platform with full credentials
     (account_id + region). For ACR_IMPORT, Azure connector also needed.
  3. alpine:3.19 pushed to the source ECR repo: nexplane-smoke/alpine:3.19
  4. A Nexplane agent (server asset) registered and active in the platform.
     The test discovers it via GET /assets?asset_type=server filtered to those
     with an active AgentRegistration.
  5. For ACR_IMPORT: Azure connector credentials must include registry_name,
     resource_group, and subscription_id pointing to an accessible subscription.
     The phase is skipped if these fields are absent or the subscription is not
     accessible — this is acceptable when Azure ACR is not provisioned.
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_SOURCE_IMAGE = "nexplane-smoke/alpine:3.19"
_DEST_REPO = "nexplane-smoke/alpine-xfer"
# AGENT_TRANSFER uses ECR→ECR (same connector, different repo).
# This exercises the full agent_docker pull/tag/push path with a registry
# that is fully accessible from the platform VPC.
_AGENT_TRANSFER_DEST_CONNECTOR_TYPE = "aws"
# ACR_IMPORT tests ECR→ACR; skipped automatically if Azure ACR is not provisioned.
_ACR_IMPORT_DEST_CONNECTOR_TYPE = "azure"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping image transfer smoke")
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
    """Discover connector UUID by type via GET /connectors."""
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.get(
            "/connectors",
            params={"connector_type": connector_type},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"GET /connectors failed: {r.text}"
        items = r.json()
    matching = [c for c in items if c.get("connector_type") == connector_type]
    assert matching, f"No connector of type '{connector_type}' found. Register one first."
    return str(matching[0]["id"])


async def _find_agent_asset_id(jwt: str) -> str:
    """Discover a server asset that has an active AgentRegistration."""
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration
    from app.models.asset import Asset, AssetType
    from sqlalchemy import select
    from datetime import datetime, timezone, timedelta

    # Find assets that have had an agent check-in within 24h
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
                # Prefer Linux agents (have Docker)
                meta = asset.asset_metadata or {}
                if meta.get("os_type") == "linux":
                    return str(asset.id)

        # Fall back to any server asset with recent registration
        for reg in registrations:
            asset = await db.get(Asset, reg.asset_id)
            if asset and asset.asset_type == AssetType.server:
                return str(asset.id)

    pytest.skip(
        "No server asset with active AgentRegistration found. "
        "Deploy the Nexplane agent on a Linux host with Docker first."
    )


async def _check_azure_acr_ready(connector_id: str) -> str | None:
    """
    Return skip reason if Azure connector lacks ACR fields or subscription is inaccessible.
    Returns None if Azure ACR is ready to use.
    """
    from app.database import AsyncSessionLocal
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select
    import requests as _requests
    from azure.identity import ClientSecretCredential

    async with AsyncSessionLocal() as db:
        cr = await db.execute(
            select(ConnectorCredential)
            .where(ConnectorCredential.connector_id == connector_id)
            .limit(1)
        )
        cred = cr.scalar_one_or_none()
        if not cred:
            return "No credentials found for Azure connector"

        backend = get_secret_backend()
        creds = backend.decrypt_json(cred.credentials_encrypted)

    # Check required ACR fields
    for field in ("registry_name", "resource_group", "subscription_id"):
        if not creds.get(field):
            return (
                f"Azure connector missing '{field}' field — ACR not provisioned. "
                "Add registry_name, resource_group to the Azure connector credentials."
            )

    # Verify subscription accessibility
    try:
        def _do():
            az_cred = ClientSecretCredential(
                creds["tenant_id"], creds["client_id"], creds["client_secret"]
            )
            token = az_cred.get_token("https://management.azure.com/.default").token
            sub_id = creds["subscription_id"]
            url = (
                f"https://management.azure.com/subscriptions/{sub_id}"
                f"?api-version=2022-12-01"
            )
            r = _requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
            return r.status_code

        status = await asyncio.get_running_loop().run_in_executor(None, _do)
        if status != 200:
            return (
                f"Azure subscription not accessible (HTTP {status}). "
                "Ensure the service principal has access to the subscription."
            )
    except Exception as exc:
        return f"Azure auth error: {exc}"

    return None


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "image-transfer smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _rollback_cr(jwt: str, cr_id: str, timeout: int = 180) -> dict:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"POST /rollback failed: {r.text}"

    interval = 5
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        jwt_refreshed = await _get_jwt(_env("API_TOKEN"))
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(
                f"/change-requests/{cr_id}",
                headers={"Authorization": f"Bearer {jwt_refreshed}"},
            )
            detail = r.json()
        status = detail.get("status")
        if status in ("rolled_back", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            rollback_run = next(
                (run for run in reversed(runs) if "rollback" in (run.get("workflow_id") or "")),
                runs[-1] if runs else None,
            )
            raw = rollback_run.get("result", {}) if rollback_run else {}
            # container_image_transfer rollback result is directly in raw["execution"]
            # (no steps wrapper); other CR types may use steps[0]["result"].
            inner = raw.get("execution", raw)
            steps = inner.get("steps", [])
            return steps[0]["result"] if steps else inner
    pytest.fail(f"Rollback for CR {cr_id} timed out after {timeout}s")


async def _create_and_execute_transfer_cr(
    token: str,
    source_connector_type: str,
    dest_connector_type: str,
    dest_repo: str,
    timeout: int = 300,
) -> dict:
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    jwt = await _get_jwt(token)
    src_connector_id = await _find_connector_id(jwt, source_connector_type)
    dst_connector_id = await _find_connector_id(jwt, dest_connector_type)
    agent_asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="container_image_transfer",
        asset_id=agent_asset_id,
        title=f"[smoke] {source_connector_type}→{dest_connector_type} image transfer",
        parameters={
            "source_connector_id": src_connector_id,
            "dest_connector_id": dst_connector_id,
            "source_image": _SOURCE_IMAGE,
            "dest_repo": dest_repo,
            "overwrite_existing": False,
        },
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]

    await _plan_and_approve_cr(jwt=jwt, cr_id=cr_id)

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request failed: {executed}"

    interval = 10
    jwt = await _get_jwt(token)
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
        if status == "completed":
            runs = detail.get("execution_runs", [])
            if runs:
                latest = max(runs, key=lambda x: x.get("started_at") or "")
                raw = latest.get("result", {})
                # container_image_transfer stores result under raw["execution"];
                # other CR types may use raw["execution"]["steps"][0]["result"].
                inner = raw.get("execution", raw)
                steps = inner.get("steps", [])
                detail["execution_result"] = steps[0]["result"] if steps else inner
            else:
                detail["execution_result"] = {}
            return detail
        if status in ("failed", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            latest_result = runs[-1].get("result", {}) if runs else {}
            pytest.fail(
                f"CR {cr_id} reached terminal failure: {detail} | exec: {latest_result}"
            )
    pytest.fail(f"CR {cr_id} timed out after {timeout}s")


# ---------------------------------------------------------------------------
# PHASE: AGENT_TRANSFER — ECR → ECR via Nexplane agent docker pull/tag/push
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AGENT_TRANSFER")
async def test_agent_transfer_ecr_to_ecr():
    """Transfer alpine:3.19 from ECR to ECR via agent docker pull/tag/push.

    Transfers between the same AWS account but different ECR repos via Nexplane
    agent docker pull/tag/push. This exercises the full agent docker path with
    a registry that is fully accessible from the platform VPC.

    Verifies:
    - CR completes successfully
    - digest_matched == True (image integrity confirmed)
    - transfer_method == "agent_docker"
    - overwrote_existing == False (net-new tag)
    - Rollback deletes the net-new tag (note == "deleted_net_new_tag")
    """
    token = _env("API_TOKEN")

    detail = await _create_and_execute_transfer_cr(
        token=token,
        source_connector_type="aws",
        dest_connector_type=_AGENT_TRANSFER_DEST_CONNECTOR_TYPE,
        dest_repo=_DEST_REPO,
    )
    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')}"
    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    assert summary.get("digest_matched") is True, (
        f"Digest mismatch after agent transfer: {summary}"
    )
    assert summary.get("transfer_method") == "agent_docker", (
        f"Wrong transfer method: {summary}"
    )
    assert summary.get("overwrote_existing") is False, (
        f"Should not have overwritten existing tag: {summary}"
    )

    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=detail["id"])
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("note") == "deleted_net_new_tag", (
        f"Expected 'deleted_net_new_tag' rollback note, got: {rb}"
    )


# ---------------------------------------------------------------------------
# PHASE: ACR_IMPORT — ECR → ACR via Azure ACR import API
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("ACR_IMPORT")
async def test_acr_import_ecr_to_acr():
    """Transfer alpine:3.19 from ECR to ACR via Azure ACR import API (server-side pull).

    Prerequisites:
    - Azure connector credentials must include: registry_name, resource_group,
      subscription_id (pointing to a subscription the SP has access to).
    - The phase is skipped if Azure ACR is not provisioned.

    Verifies:
    - CR completes successfully
    - digest_matched == True
    - transfer_method == "acr_import"
    - Rollback deletes the net-new tag
    """
    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)

    # Pre-flight: verify Azure connector has required ACR fields and subscription access
    azure_connector_id = await _find_connector_id(jwt, "azure")
    skip_reason = await _check_azure_acr_ready(azure_connector_id)
    if skip_reason:
        pytest.skip(f"ACR_IMPORT skipped — Azure ACR not ready: {skip_reason}")

    detail = await _create_and_execute_transfer_cr(
        token=token,
        source_connector_type="aws",
        dest_connector_type="azure",
        dest_repo=_DEST_REPO,
    )
    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')}"
    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    assert summary.get("digest_matched") is True, (
        f"Digest mismatch after ACR import: {summary}"
    )
    assert summary.get("transfer_method") == "acr_import", (
        f"Wrong transfer method: {summary}"
    )
    assert summary.get("overwrote_existing") is False, (
        f"Should not have overwritten existing tag: {summary}"
    )

    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=detail["id"])
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("note") == "deleted_net_new_tag", (
        f"Expected 'deleted_net_new_tag' rollback note, got: {rb}"
    )
