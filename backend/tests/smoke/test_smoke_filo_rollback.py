# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
FILO_ROLLBACK_SMOKE — live smoke test for FILO rollback ordering.

Run (on EC2 inside nexplane-backend-1 container):
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s

    # Reuse an existing asset (skips provisioning):
    ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s

    # Explicit cloud account (auto-discovered if omitted):
    CLOUD_ACCOUNT_ID=<uuid> API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s

Prerequisites:
  1. API_TOKEN holds a valid nxp_... token with admin/approver role.
  2. An AWS connector registered as a cloud_account asset (or set CLOUD_ACCOUNT_ID).
  3. NexplaneEC2TestProfile IAM instance profile exists in the AWS account.

Phases:
  PHASE_0: Provision EC2 + deploy agent (skipped if ASSET_ID set)
  PHASE_1: Execute CR-A (sysctl 7199) — verify application_sequence stamped
  PHASE_2: Execute CR-B (sysctl 7198) — verify application_sequence > CR-A
  PHASE_3: FILO guard — attempt per-CR rollback of CR-A, expect 409
  PHASE_4: Asset rollback-all — rolls back CR-B then CR-A
  PHASE_5: Project rollback with to_cr_id — partial then full

Teardown: instance terminated automatically on full pass; left running on any failure.
"""
import asyncio
import hashlib
import os

import boto3
import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_STATE: dict = {}

_BASE_URL = "http://localhost:8000/api/v1"


async def _get_backend_private_ip() -> str:
    """Return this EC2 instance's private IP. Tries metadata endpoint; falls back to boto3."""
    import urllib.request
    try:
        with urllib.request.urlopen(
            "http://169.254.169.254/latest/meta-data/local-ipv4", timeout=2
        ) as resp:
            return resp.read().decode().strip()
    except Exception:
        pass
    # Fallback: use boto3 to describe this instance
    try:
        with urllib.request.urlopen(
            "http://169.254.169.254/latest/meta-data/instance-id", timeout=2
        ) as resp:
            own_id = resp.read().decode().strip()
        ec2 = boto3.client("ec2")
        reservations = ec2.describe_instances(InstanceIds=[own_id])["Reservations"]
        return reservations[0]["Instances"][0]["PrivateIpAddress"]
    except Exception as exc:
        pytest.fail(f"Cannot resolve backend private IP: {exc}")


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping FILO smoke test")
    return val


async def _get_jwt(api_token: str) -> str:
    """Derive a short-lived JWT from an nxp_... API token for REST endpoint auth."""
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


async def _create_and_execute_sysctl_cr(
    token: str, asset_id: str, param: str, value: int, title: str
) -> str:
    """Create, approve, and execute an apply_sysctl_hardening CR. Return cr_id."""
    from app.mcp_tools.change_requests import (
        approve_change_request,
        create_change_request,
        execute_change_request,
        get_change_request,
    )

    # Create draft CR
    cr = await create_change_request(
        token=token,
        change_type="apply_sysctl_hardening",
        asset_id=asset_id,
        title=title,
        parameters={"settings": {param: value}},
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]

    # Approve
    approved = await approve_change_request(token=token, cr_id=cr_id)
    assert "error" not in approved, f"approve_change_request failed: {approved}"

    # Execute
    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request failed: {executed}"

    # Poll until completed (max 120s)
    for _ in range(24):
        await asyncio.sleep(5)
        detail = await get_change_request(token=token, cr_id=cr_id)
        status = detail.get("status")
        if status == "completed":
            return cr_id
        if status in ("failed", "rollback_failed"):
            pytest.fail(f"CR {cr_id} reached terminal failure state: {detail}")
    pytest.fail(f"CR {cr_id} timed out after 120s waiting for completed status")


async def _create_and_execute_cr(
    token: str,
    change_type: str,
    asset_id: str,
    title: str,
    parameters: dict,
    timeout: int = 600,
) -> str:
    """Create, approve, and execute any CR type. Return cr_id once completed."""
    from app.mcp_tools.change_requests import (
        approve_change_request,
        create_change_request,
        execute_change_request,
        get_change_request,
    )

    cr = await create_change_request(
        token=token,
        change_type=change_type,
        asset_id=asset_id,
        title=title,
        parameters=parameters,
    )
    assert "id" in cr, f"create_change_request({change_type}) failed: {cr}"
    cr_id = cr["id"]

    approved = await approve_change_request(token=token, cr_id=cr_id)
    assert "error" not in approved, f"approve_change_request({change_type}) failed: {approved}"

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request({change_type}) failed: {executed}"

    interval = 10
    attempts = timeout // interval
    for _ in range(attempts):
        await asyncio.sleep(interval)
        detail = await get_change_request(token=token, cr_id=cr_id)
        status = detail.get("status")
        if status == "completed":
            return cr_id
        if status in ("failed", "rollback_failed"):
            pytest.fail(f"CR {cr_id} ({change_type}) reached terminal failure: {detail}")
    pytest.fail(f"CR {cr_id} ({change_type}) timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Failure tracking hook — keeps instance alive on any phase failure
# ---------------------------------------------------------------------------

def pytest_runtest_logreport(report):
    if report.failed and report.when == "call":
        _STATE["failed"] = True


# ---------------------------------------------------------------------------
# Teardown helpers
# ---------------------------------------------------------------------------

def _terminate_instance(instance_id: str) -> None:
    """Terminate EC2 instance via boto3 (sync — called from sync finalizer)."""
    try:
        ec2 = boto3.client("ec2")
        ec2.terminate_instances(InstanceIds=[instance_id])
        print(f"\n  Terminated EC2 instance {instance_id}")
    except Exception as exc:
        print(f"\n  WARNING: failed to terminate {instance_id}: {exc}")


async def _delete_smoke_asset(asset_id: str, token: str) -> None:
    """Remove the provisioned asset from inventory."""
    try:
        jwt = await _get_jwt(token)
        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30,
        ) as client:
            await client.delete(f"/assets/{asset_id}")
        print(f"  Deleted smoke asset {asset_id} from inventory")
    except Exception as exc:
        print(f"  WARNING: failed to delete asset {asset_id}: {exc}")


# ---------------------------------------------------------------------------
# PHASE_0: Provision EC2 instance and deploy Nexplane agent
# ---------------------------------------------------------------------------

async def test_PHASE_0_provision(request):
    """
    Provision a fresh EC2 instance and deploy the Nexplane agent via CR lifecycle.
    Skipped if ASSET_ID env var is set — uses that asset directly instead.
    Registers a finalizer: terminates the instance on pass, leaves it running on failure.
    """
    token = _env("API_TOKEN")

    # Escape hatch: caller supplies a pre-existing asset
    explicit_asset_id = os.environ.get("ASSET_ID")
    if explicit_asset_id:
        _STATE["asset_id"] = explicit_asset_id
        _STATE["provisioned"] = False
        print(f"\n  PHASE_0: using existing asset {explicit_asset_id} (skipping provisioning)")
        return

    jwt = await _get_jwt(token)

    # Resolve backend private IP (agent phones home to this)
    backend_ip = await _get_backend_private_ip()
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n  Backend private URL: {nexplane_url}")

    # Discover cloud account asset
    cloud_account_id = os.environ.get("CLOUD_ACCOUNT_ID")
    if not cloud_account_id:
        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30,
        ) as client:
            r = await client.get("/assets", params={"asset_type": "cloud_account"})
            assert r.status_code == 200, f"GET /assets failed: {r.text}"
            accounts = r.json()
        assert accounts, (
            "No cloud_account assets found — set CLOUD_ACCOUNT_ID env var or register an AWS connector"
        )
        cloud_account_id = accounts[0]["id"]
    print(f"  Cloud account: {cloud_account_id}")

    # Generate agent secret (POST regenerates and returns plaintext once)
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    ) as client:
        r = await client.post("/settings/agent-secret")
        assert r.status_code == 200, f"POST /settings/agent-secret failed: {r.text}"
        agent_secret = r.json()["agent_secret_plaintext"]
    print("  Agent secret generated")

    # Register finalizer before any provisioning so it always runs
    _STATE["provisioned"] = False  # set True once instance is up

    def _finalizer():
        if not _STATE.get("provisioned"):
            return
        if _STATE.get("failed"):
            print(
                f"\n  PHASE_0 finalizer: failure detected — leaving instance {_STATE.get('instance_id')} "
                f"running for debugging. Private IP resolvable via AWS console."
            )
            return
        print("\n  PHASE_0 finalizer: all phases passed — tearing down.")
        instance_id = _STATE.get("instance_id")
        asset_id = _STATE.get("asset_id")
        if instance_id:
            _terminate_instance(instance_id)
        if asset_id:
            asyncio.get_event_loop().run_until_complete(
                _delete_smoke_asset(asset_id, token)
            )

    request.addfinalizer(_finalizer)

    # Launch EC2 instance via CR lifecycle
    print("  Launching EC2 instance via ec2_launch CR...")
    ec2_cr_id = await _create_and_execute_cr(
        token=token,
        change_type="ec2_launch",
        asset_id=cloud_account_id,
        title="FILO smoke — provision EC2 instance",
        parameters={
            "mode": "quick",
            "name": "nexplane-smoke-filo",
            "os": "amazon_linux",
            "instance_type": "t3.small",
            "iam_instance_profile": "NexplaneEC2TestProfile",
            "rollback_strategy": "terminate_instance",
        },
        timeout=300,
    )

    # Get instance_id from CR execution result
    from app.mcp_tools.change_requests import get_change_request
    ec2_cr = await get_change_request(token=token, cr_id=ec2_cr_id)
    execution_result = ec2_cr.get("execution_result") or {}
    instance_id = execution_result.get("instance_id")
    assert instance_id, f"instance_id not found in ec2_launch CR result: {ec2_cr}"
    _STATE["instance_id"] = instance_id
    _STATE["provisioned"] = True
    print(f"  EC2 instance launched: {instance_id}")

    # Find the auto-created server asset for this instance
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    ) as client:
        r = await client.get("/assets", params={"q": "nexplane-smoke-filo", "asset_type": "server"})
        ec2_assets = r.json()
    assert ec2_assets, "Server asset not found in inventory after ec2_launch"
    ec2_asset_id = ec2_assets[0]["id"]
    print(f"  EC2 server asset: {ec2_asset_id}")

    # Wait for SSM agent to register on the new instance (required before deploy)
    print("  Waiting 180s for SSM agent to register on the new instance...")
    await asyncio.sleep(180)

    # Deploy Nexplane agent via CR lifecycle
    print("  Deploying Nexplane agent via deploy_nexplane_agent CR...")
    await _create_and_execute_cr(
        token=token,
        change_type="deploy_nexplane_agent",
        asset_id=ec2_asset_id,
        title="FILO smoke — deploy Nexplane agent",
        parameters={
            "instance_id": instance_id,
            "nexplane_url": nexplane_url,
            "nexplane_secret": agent_secret,
            "hostname": "nexplane-smoke-filo",
        },
        timeout=300,
    )
    print("  deploy_nexplane_agent CR completed")

    # Poll for agent asset registration (agent phones home and registers as a server asset)
    print("  Waiting up to 120s for agent to register with platform...")
    deadline = asyncio.get_event_loop().time() + 120
    agent_asset_id = None
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    ) as client:
        while asyncio.get_event_loop().time() < deadline:
            r = await client.get(
                "/assets",
                params={"q": "nexplane-smoke-filo", "asset_type": "server"},
            )
            # After agent registration there will be an asset with the agent's hostname.
            # The agent registers under hostname="nexplane-smoke-filo" passed above.
            # Filter to the asset whose metadata indicates an active agent connection
            # (has_agent=True or agent_last_seen is set), or fall back to any match.
            candidates = r.json()
            agent_candidates = [
                a for a in candidates
                if a.get("has_agent") or a.get("agent_last_seen")
            ]
            if agent_candidates:
                agent_asset_id = agent_candidates[0]["id"]
                break
            # If no agent-flagged asset yet, wait and retry
            await asyncio.sleep(15)

    assert agent_asset_id, (
        "Nexplane agent did not register within 120s. "
        f"Check instance {instance_id} via AWS console."
    )
    _STATE["asset_id"] = agent_asset_id
    print(f"  Agent registered as asset: {agent_asset_id}")


# ---------------------------------------------------------------------------
# PHASE_1: Execute CR-A
# ---------------------------------------------------------------------------

async def test_PHASE_1_execute_cr_a():
    """Execute CR-A (sysctl tcp_keepalive_time=7199). Verify application_sequence is stamped."""
    from app.mcp_tools.change_requests import get_change_request

    token = _env("API_TOKEN")
    if "asset_id" not in _STATE:
        pytest.skip("PHASE_0 did not run")
    asset_id = _STATE["asset_id"]

    cr_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_time",
        value=7199,
        title="FILO smoke CR-A — tcp_keepalive_time=7199",
    )
    _STATE["cr_a_id"] = cr_id

    detail = await get_change_request(token=token, cr_id=cr_id)
    assert detail.get("status") == "completed", f"CR-A not completed: {detail}"
    app_seq = detail.get("application_sequence")
    assert app_seq is not None, "CR-A missing application_sequence after completion"
    assert isinstance(app_seq, int), f"application_sequence not int: {app_seq}"
    _STATE["cr_a_seq"] = app_seq
    print(f"\n  CR-A id={cr_id} application_sequence={app_seq}")


# ---------------------------------------------------------------------------
# PHASE_2: Execute CR-B
# ---------------------------------------------------------------------------

async def test_PHASE_2_execute_cr_b():
    """Execute CR-B (sysctl tcp_keepalive_time=7198). Verify seq > CR-A's."""
    from app.mcp_tools.change_requests import get_change_request

    if "cr_a_id" not in _STATE:
        pytest.skip("PHASE_1 did not run")

    token = _env("API_TOKEN")
    asset_id = _STATE["asset_id"]

    cr_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_time",
        value=7198,
        title="FILO smoke CR-B — tcp_keepalive_time=7198",
    )
    _STATE["cr_b_id"] = cr_id

    detail = await get_change_request(token=token, cr_id=cr_id)
    app_seq = detail.get("application_sequence")
    assert app_seq is not None, "CR-B missing application_sequence"
    assert app_seq > _STATE["cr_a_seq"], (
        f"CR-B application_sequence ({app_seq}) must be > CR-A ({_STATE['cr_a_seq']})"
    )
    _STATE["cr_b_seq"] = app_seq
    print(f"\n  CR-B id={cr_id} application_sequence={app_seq}")


# ---------------------------------------------------------------------------
# PHASE_3: FILO guard blocks out-of-order rollback
# ---------------------------------------------------------------------------

async def test_PHASE_3_filo_guard_blocks_cr_a_rollback():
    """Attempt per-CR rollback of CR-A while CR-B is still applied. Expect 409."""
    from app.mcp_tools.change_requests import get_change_request

    if "cr_a_id" not in _STATE or "cr_b_id" not in _STATE:
        pytest.skip("PHASE_1/2 did not run")

    token = _env("API_TOKEN")
    cr_a_id = _STATE["cr_a_id"]
    cr_b_id = _STATE["cr_b_id"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30.0,
    ) as client:
        resp = await client.post(f"/change-requests/{cr_a_id}/rollback")

    assert resp.status_code == 409, (
        f"Expected 409 (FILO guard), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("error") == "out_of_order_rollback", f"Unexpected error: {body}"
    assert cr_b_id in body.get("blocking_crs", []), (
        f"CR-B not in blocking_crs: {body.get('blocking_crs')}"
    )

    # CR-A must still be completed (rollback did not proceed)
    detail = await get_change_request(token=token, cr_id=cr_a_id)
    assert detail.get("status") == "completed", (
        f"CR-A status changed despite 409: {detail.get('status')}"
    )
    print(f"\n  FILO guard correctly blocked rollback of CR-A, blocking_crs={body['blocking_crs']}")


# ---------------------------------------------------------------------------
# PHASE_4: Asset rollback-all
# ---------------------------------------------------------------------------

async def test_PHASE_4_asset_rollback_all():
    """POST /assets/{id}/rollback-all rolls back CR-B then CR-A in FILO order."""
    from app.mcp_tools.change_requests import get_change_request

    if "cr_a_id" not in _STATE or "cr_b_id" not in _STATE:
        pytest.skip("PHASE_1/2 did not run")

    token = _env("API_TOKEN")
    asset_id = _STATE["asset_id"]
    cr_a_id = _STATE["cr_a_id"]
    cr_b_id = _STATE["cr_b_id"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=300.0,  # rollback-all can take a while
    ) as client:
        resp = await client.post(f"/assets/{asset_id}/rollback-all")

    assert resp.status_code == 200, f"rollback-all failed ({resp.status_code}): {resp.text}"
    body = resp.json()
    assert body.get("failed_at") is None, f"rollback-all stopped at: {body.get('failed_at')}: {body}"
    rolled_back = body.get("rolled_back", [])
    assert cr_b_id in rolled_back, f"CR-B not in rolled_back: {rolled_back}"
    assert cr_a_id in rolled_back, f"CR-A not in rolled_back: {rolled_back}"

    # CR-B must appear before CR-A (FILO: newest rolled back first)
    assert rolled_back.index(cr_b_id) < rolled_back.index(cr_a_id), (
        f"Expected CR-B before CR-A in rollback order. Got: {rolled_back}"
    )

    # Confirm CR statuses via MCP tool
    cr_a_detail = await get_change_request(token=token, cr_id=cr_a_id)
    cr_b_detail = await get_change_request(token=token, cr_id=cr_b_id)
    assert cr_b_detail.get("status") == "rolled_back", f"CR-B status: {cr_b_detail.get('status')}"
    assert cr_a_detail.get("status") == "rolled_back", f"CR-A status: {cr_a_detail.get('status')}"
    print(f"\n  rollback-all rolled back: {rolled_back}")


# ---------------------------------------------------------------------------
# PHASE_5: Project rollback with to_cr_id
# ---------------------------------------------------------------------------

async def test_PHASE_5_project_rollback_with_to_cr_id():
    """Create project with 2 CRs, execute both, partial rollback with to_cr_id, then full cleanup."""
    from app.mcp_tools.change_requests import get_change_request
    from app.mcp_tools.projects import (
        add_cr_to_project,
        create_project,
        rollback_project,
    )

    token = _env("API_TOKEN")
    asset_id = _STATE["asset_id"]

    # Create project
    proj = await create_project(
        token=token,
        name="FILO smoke project",
        goal="Test to_cr_id partial rollback",
        description="Automated smoke — safe to delete",
    )
    assert "id" in proj, f"create_project failed: {proj}"
    project_id = proj["id"]
    _STATE["project_id"] = project_id

    # Execute CR-C (seq_order=1 in project) — use tcp_keepalive_intvl to avoid conflict
    cr_c_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_intvl",
        value=74,
        title="FILO smoke CR-C — tcp_keepalive_intvl=74",
    )
    _STATE["cr_c_id"] = cr_c_id
    added_c = await add_cr_to_project(token=token, project_id=project_id, cr_id=cr_c_id)
    assert "error" not in added_c, f"add_cr_to_project CR-C failed: {added_c}"

    # Execute CR-D (seq_order=2 in project)
    cr_d_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_intvl",
        value=73,
        title="FILO smoke CR-D — tcp_keepalive_intvl=73",
    )
    _STATE["cr_d_id"] = cr_d_id
    added_d = await add_cr_to_project(token=token, project_id=project_id, cr_id=cr_d_id)
    assert "error" not in added_d, f"add_cr_to_project CR-D failed: {added_d}"

    # Partial rollback: to_cr_id=CR-D means roll back only CR-D (most recent)
    partial = await rollback_project(
        token=token, project_id=project_id, to_cr_id=cr_d_id
    )
    assert "error" not in partial, f"partial rollback_project failed: {partial}"
    assert partial.get("rollback_initiated") is True, f"rollback not initiated: {partial}"

    # Wait for CR-D to be rolled back (poll up to 60s)
    for _ in range(12):
        await asyncio.sleep(5)
        cr_d_detail = await get_change_request(token=token, cr_id=cr_d_id)
        if cr_d_detail.get("status") == "rolled_back":
            break
    else:
        pytest.fail(f"CR-D not rolled back after 60s: {cr_d_detail}")

    # CR-C must still be completed (not rolled back — outside to_cr_id range)
    cr_c_detail = await get_change_request(token=token, cr_id=cr_c_id)
    assert cr_c_detail.get("status") == "completed", (
        f"CR-C should still be completed but got: {cr_c_detail.get('status')}"
    )
    print(f"\n  Partial rollback: CR-D rolled back, CR-C still completed")

    # Full cleanup: rollback without to_cr_id (rolls back CR-C)
    full = await rollback_project(token=token, project_id=project_id)
    assert "error" not in full, f"full rollback_project failed: {full}"
    assert full.get("rollback_initiated") is True

    for _ in range(12):
        await asyncio.sleep(5)
        cr_c_detail = await get_change_request(token=token, cr_id=cr_c_id)
        if cr_c_detail.get("status") == "rolled_back":
            break
    else:
        pytest.fail(f"CR-C not rolled back after 60s: {cr_c_detail}")

    print(f"\n  Full cleanup: CR-C rolled back. All sysctl params restored.")
