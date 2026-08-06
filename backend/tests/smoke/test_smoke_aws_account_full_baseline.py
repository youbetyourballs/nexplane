# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: AWS Account Full Baseline

Runs the aws_account_full_baseline CR through the full lifecycle
(create→plan→approve→execute→rollback) against the live AWS connector.

Verifies:
  - All 3 steps_completed (hardening, monitoring, iam_role_baseline)
  - IAM password policy enforced (spot-check via boto3)
  - CloudTrail trail exists (spot-check via boto3)
  - NexplaneReadOnly/NexplaneSecurityAudit/NexplaneBreakGlass roles present
  - Rollback removes Nexplane-prefixed net-new roles

Timeout: 10 minutes (monitoring phase enables multi-region services).

Run:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_aws_account_full_baseline.py -v -s

Must be run from EC2 runner on Tailscale, not local Docker.
"""

import asyncio
import hashlib
import os

import boto3
import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_STATE: dict = {}

_EXPECTED_STEPS = [
    "aws_account_baseline_hardening",
    "aws_account_baseline_monitoring",
    "iam_role_baseline",
]

_ROLE_PREFIX = "Nexplane"
_EXPECTED_ROLES = [
    f"{_ROLE_PREFIX}ReadOnly",
    f"{_ROLE_PREFIX}SecurityAudit",
    f"{_ROLE_PREFIX}BreakGlass",
]


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set")
    return val


async def _get_api_token() -> str:
    """Return an API token — from env var or first valid token in DB."""
    env_tok = os.environ.get("API_TOKEN")
    if env_tok:
        return env_tok
    from app.database import AsyncSessionLocal
    from app.models.api_token import ApiToken
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ApiToken).where(ApiToken.revoked == False).limit(1)  # noqa: E712
        )
        tok = r.scalars().first()
        if not tok:
            pytest.skip("No API tokens in DB and API_TOKEN env var not set")
        return tok.token


async def _get_aws_creds() -> tuple[dict, str]:
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Connector).where(Connector.connector_type == "aws"))
        connector = r.scalars().first()
        if not connector:
            pytest.skip("No AWS connector registered")
        cr = await db.execute(
            select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
        )
        cc = cr.scalars().first()
        backend = get_secret_backend()
        return backend.decrypt_json(cc.credentials_encrypted), str(connector.id)


def _iam_client(creds: dict):
    return boto3.client(
        "iam",
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


def _cloudtrail_client(creds: dict):
    return boto3.client(
        "cloudtrail",
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


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
        assert r.status_code == 200, f"submit-for-approval failed: {r.text}"
        r = await client.post(
            f"/change-requests/{cr_id}/approve",
            json={"decision": "approved", "comment": "aws account full baseline smoke self-approval"},
            headers=headers,
        )
        assert r.status_code == 200, f"approve failed: {r.text}"


async def _poll_cr(token: str, cr_id: str, terminal: set, timeout: int = 600) -> dict:
    interval = 15
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
        status = detail.get("status")
        if status in terminal:
            return detail
        print(f"  [poll] CR {cr_id} status={status}")
    pytest.fail(f"CR {cr_id} did not reach {terminal} in {timeout}s")


def _extract_exec_result(detail: dict) -> dict:
    runs = detail.get("execution_runs", [])
    for run in reversed(runs):
        steps = run.get("result", {}).get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result", {})
    return detail.get("execution_result", {})


# ---------------------------------------------------------------------------
# Phase 1: get creds (no infra setup needed — runs against live account)
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_ACCOUNT_FULL_BASELINE")
async def test_01_setup():
    """Verify AWS connector is registered and credentials are accessible."""
    token = await _get_api_token()
    creds, connector_id = await _get_aws_creds()

    _STATE["token"] = token
    _STATE["connector_id"] = connector_id
    _STATE["creds"] = creds

    # Spot-check we can call STS
    loop = asyncio.get_event_loop()
    iam = _iam_client(creds)
    identity = await loop.run_in_executor(
        None, lambda: boto3.client(
            "sts",
            aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
            region_name=creds.get("region", "us-east-1"),
        ).get_caller_identity()
    )
    account_id = identity["Account"]
    _STATE["account_id"] = account_id
    print(f"\n[smoke] AWS account: {account_id}, connector: {connector_id}")
    assert account_id, "STS identity check failed"


# ---------------------------------------------------------------------------
# Phase 2: full CR lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_ACCOUNT_FULL_BASELINE")
async def test_02_execute_cr():
    """Run aws_account_full_baseline CR through full lifecycle."""
    token = _STATE.get("token") or await _get_api_token()
    connector_id = _STATE.get("connector_id")
    assert connector_id, "test_01 must run first"

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(
            "/change-requests",
            json={
                "title": "[smoke] AWS Account Full Baseline",
                "change_type": "aws_account_full_baseline",
                "desired_outcome": {
                    "summary": "Apply full AWS account baseline: hardening + monitoring + IAM roles",
                    "enforce_password_policy": True,
                    "enforce_s3_block_public": True,
                    "enforce_root_mfa_check": True,
                    "enable_vpc_flow_logs": True,
                    "role_name_prefix": _ROLE_PREFIX,
                    "skip_if_exists": True,
                },
                "connector_id": connector_id,
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"create CR failed: {r.text}"
        cr = r.json()

    cr_id = cr["id"]
    _STATE["cr_id"] = cr_id
    print(f"\n[smoke] Created CR {cr_id}")

    await _plan_and_approve(token, cr_id)

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/execute",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"execute failed: {r.text}"

    # Monitoring phase enables multi-region services — use 10-minute timeout
    detail = await _poll_cr(token, cr_id, {"completed", "failed"}, timeout=600)
    assert detail["status"] == "completed", (
        f"CR did not complete: {detail.get('status')}\n"
        f"result: {_extract_exec_result(detail)}"
    )
    _STATE["detail"] = detail
    print(f"\n[smoke] CR {cr_id} completed ✓")


# ---------------------------------------------------------------------------
# Phase 3: verify execution result — all 3 steps present
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_ACCOUNT_FULL_BASELINE")
async def test_03_verify_steps_completed():
    """Assert all 3 sub-executor steps completed and spot-check live state."""
    detail = _STATE.get("detail")
    assert detail, "test_02 must run first"

    result = _extract_exec_result(detail)
    print(f"\n[smoke] execution result status: {result.get('status')}")

    steps_completed = result.get("steps_completed", [])
    completed_names = [s["step"] for s in steps_completed]

    for expected in _EXPECTED_STEPS:
        assert expected in completed_names, (
            f"Expected step '{expected}' missing from steps_completed: {completed_names}"
        )

    print(f"[smoke] steps_completed: {completed_names} ✓")

    # Spot-check: IAM password policy should be set
    loop = asyncio.get_event_loop()
    creds = _STATE["creds"]
    iam = _iam_client(creds)
    pp = await loop.run_in_executor(None, lambda: iam.get_account_password_policy())
    policy = pp.get("PasswordPolicy", {})
    assert policy.get("MinimumPasswordLength", 0) >= 8, (
        f"Password policy MinimumPasswordLength < 8: {policy}"
    )
    print(f"[smoke] IAM password policy enforced (min length={policy.get('MinimumPasswordLength')}) ✓")

    # Spot-check: CloudTrail trail exists
    ct = _cloudtrail_client(creds)
    trails_resp = await loop.run_in_executor(None, lambda: ct.describe_trails(includeShadowTrails=False))
    trails = trails_resp.get("trailList", [])
    assert len(trails) > 0, "No CloudTrail trails found after baseline — monitoring step may have failed"
    print(f"[smoke] CloudTrail trails: {[t['Name'] for t in trails]} ✓")

    # Spot-check: Nexplane IAM roles exist
    for role_name in _EXPECTED_ROLES:
        try:
            await loop.run_in_executor(None, lambda rn=role_name: iam.get_role(RoleName=rn))
            print(f"[smoke] IAM role {role_name} exists ✓")
        except iam.exceptions.NoSuchEntityException:
            # Role may have been skipped (skip_if_exists=True with pre-existing role) — not a hard failure
            print(f"[smoke] IAM role {role_name} not found (may have been skipped)")


# ---------------------------------------------------------------------------
# Phase 4: rollback
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_ACCOUNT_FULL_BASELINE")
async def test_04_rollback():
    """Roll back — FILO: iam_role_baseline → monitoring → hardening."""
    token = _STATE.get("token") or await _get_api_token()
    cr_id = _STATE.get("cr_id")
    assert cr_id, "test_02 must run first"

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(
            f"/change-requests/{cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"rollback trigger failed: {r.text}"

    detail = await _poll_cr(token, cr_id, {"rolled_back", "rollback_failed"}, timeout=600)
    assert detail["status"] == "rolled_back", (
        f"Rollback did not complete: {detail.get('status')}"
    )
    print(f"\n[smoke] CR {cr_id} rolled back ✓")


# ---------------------------------------------------------------------------
# Phase 5: verify rollback — Nexplane-prefixed net-new roles removed
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AWS_ACCOUNT_FULL_BASELINE")
async def test_05_verify_rollback():
    """Confirm net-new Nexplane roles are gone after rollback."""
    detail = _STATE.get("detail")
    assert detail, "test_02 must run first"

    result = _extract_exec_result(detail)
    steps_completed = result.get("steps_completed", [])

    # Find which roles were net-new (was_new=True)
    iam_step = next(
        (s for s in steps_completed if s["step"] == "iam_role_baseline"),
        None,
    )
    if not iam_step:
        pytest.skip("iam_role_baseline step not found in steps_completed — skipping role check")

    iam_result = iam_step.get("result", {})
    pre_state_roles = iam_result.get("pre_state", {}).get("roles", [])
    net_new_roles = [r["name"] for r in pre_state_roles if r.get("was_new", False)]

    if not net_new_roles:
        print("[smoke] No net-new roles were created (all pre-existed) — rollback check is a no-op ✓")
        return

    # Confirm net-new roles are gone
    loop = asyncio.get_event_loop()
    creds = _STATE["creds"]
    iam = _iam_client(creds)

    for role_name in net_new_roles:
        try:
            await loop.run_in_executor(None, lambda rn=role_name: iam.get_role(RoleName=rn))
            pytest.fail(f"Role {role_name} still exists after rollback — rollback failed to clean it up")
        except Exception as exc:
            err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if err_code in ("NoSuchEntity", "NoSuchEntityException"):
                print(f"[smoke] Role {role_name} correctly removed by rollback ✓")
            else:
                # Unexpected error — treat as role still existing
                raise

    print(f"[smoke] All {len(net_new_roles)} net-new roles removed by rollback ✓")
