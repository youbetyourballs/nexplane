# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: AWS IAM Role Baseline

Phases:
  IAM_BASELINE  — create NexplaneReadOnly/SecurityAudit/BreakGlass roles, verify with boto3
  IAM_ROLLBACK  — rollback deletes net-new roles, verify with boto3

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_aws_iam_role_baseline.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AWS connector registered in the platform with iam:CreateRole, iam:AttachRolePolicy,
     iam:DeleteRole, iam:DetachRolePolicy, iam:GetRole permissions.
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_EXPECTED_ROLES = ["NexplaneReadOnly", "NexplaneSecurityAudit", "NexplaneBreakGlass"]

# Track created CR id for rollback phase
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


async def _find_connector_id(jwt: str, connector_type: str) -> str:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.get(
            "/connectors",
            params={"connector_type": connector_type},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200
        items = r.json()
    matching = [c for c in items if c.get("connector_type") == connector_type]
    if not matching:
        pytest.skip("No AWS connector found — skipping IAM baseline smoke")
    return str(matching[0]["id"])


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


def _boto_iam(connector_id: str):
    """Return a boto3 IAM client using credentials from the platform connector."""
    import boto3
    from app.database import AsyncSessionLocal
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    import asyncio
    from sqlalchemy import select
    import uuid

    async def _get_creds():
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                select(ConnectorCredential).where(
                    ConnectorCredential.connector_id == uuid.UUID(connector_id)
                ).limit(1)
            )
            cred = r.scalar_one_or_none()
            if not cred:
                return None
            backend = get_secret_backend()
            return backend.decrypt_json(cred.credentials_encrypted)

    creds = asyncio.get_event_loop().run_until_complete(_get_creds())
    if not creds:
        return None

    return boto3.client(
        "iam",
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


# ---------------------------------------------------------------------------
# Phase: IAM_BASELINE
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("IAM_BASELINE")
async def test_iam_role_baseline_create():
    """Create NexplaneReadOnly/SecurityAudit/BreakGlass roles; verify they exist via boto3."""
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)
    connector_id = await _find_connector_id(jwt, "aws")

    cr = await create_change_request(
        token=token,
        change_type="catalog_action",
        asset_id=None,
        title="[smoke] AWS IAM role baseline",
        parameters={
            "connector_type": "aws",
            "action_id": "aws_iam_role_baseline",
            "connector_id": connector_id,
            "params": {
                "role_name_prefix": "Nexplane",
                "skip_if_exists": True,
            },
        },
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    _state["connector_id"] = connector_id

    await _plan_and_approve_cr(jwt=jwt, cr_id=cr_id, comment="iam-baseline smoke self-approval")

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute failed: {executed}"

    detail = await _poll_cr(jwt=jwt, cr_id=cr_id, token=token, timeout=120)
    result = detail.get("execution_result", {})

    # Verify result structure
    assert "created_roles" in result or "skipped_roles" in result, (
        f"Expected created_roles or skipped_roles in result: {result}"
    )
    assert "account_id" in result, f"Missing account_id in result: {result}"

    print(
        f"\nIAM baseline: created={result.get('created_roles', [])}, "
        f"skipped={result.get('skipped_roles', [])}"
    )

    # Verify roles exist via boto3
    iam = _boto_iam(connector_id)
    if iam:
        from botocore.exceptions import ClientError
        for role_name in _EXPECTED_ROLES:
            try:
                resp = iam.get_role(RoleName=role_name)
                assert resp["Role"]["RoleName"] == role_name
                tags = {t["Key"]: t["Value"] for t in resp["Role"].get("Tags", [])}
                assert tags.get("ManagedBy") == "nexplane", (
                    f"Role {role_name} missing ManagedBy=nexplane tag: {tags}"
                )
                print(f"  ✓ {role_name} exists with correct tags")
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchEntity":
                    pytest.fail(f"Role {role_name} not found after execution")
                raise


# ---------------------------------------------------------------------------
# Phase: IAM_ROLLBACK
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("IAM_ROLLBACK")
async def test_iam_role_baseline_rollback():
    """Rollback deletes net-new roles; verify they no longer exist via boto3."""
    if "cr_id" not in _state:
        pytest.skip("IAM_BASELINE phase did not run — skipping rollback phase")

    token = _env("API_TOKEN")
    jwt = await _get_jwt(token)
    cr_id = _state["cr_id"]
    connector_id = _state["connector_id"]

    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"Rollback request failed: {r.text}"

    # Poll for rolled_back status
    interval = 5
    for _ in range(120 // interval):
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

    # Verify via boto3 that net-new roles were deleted
    iam = _boto_iam(connector_id)
    if iam:
        from botocore.exceptions import ClientError

        runs = detail.get("execution_runs", [])
        rollback_run = next(
            (run for run in reversed(runs) if "rollback" in (run.get("workflow_id") or "")),
            runs[-1] if runs else None,
        )
        raw = rollback_run.get("result", {}) if rollback_run else {}
        inner = raw.get("execution", raw)
        # deleted_roles may be at top level or nested in rollback_steps
        rollback_steps = inner.get("rollback_steps", [])
        if rollback_steps:
            inner = rollback_steps[0].get("result", {})
        deleted_roles = inner.get("deleted_roles", [])

        for role_name in deleted_roles:
            try:
                iam.get_role(RoleName=role_name)
                pytest.fail(f"Role {role_name} still exists after rollback")
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchEntity":
                    print(f"  ✓ {role_name} deleted by rollback")
                else:
                    raise

        print(f"\nRollback complete: {len(deleted_roles)} roles deleted")
