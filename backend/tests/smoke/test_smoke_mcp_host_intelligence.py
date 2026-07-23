# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
MCP_HOST_INTELLIGENCE_SMOKE — live smoke test for host intelligence MCP tools.

Run:
    ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest backend/tests/smoke/test_smoke_mcp_host_intelligence.py -v

Prerequisites:
  1. Migration intel001 applied to the live DB.
  2. At least one asset has a live Nexplane agent registered.
  3. API_TOKEN env var holds a valid nxp_... token.
  4. ASSET_ID env var holds the UUID of that asset.
"""
from __future__ import annotations

import os
import time as _time
import uuid

import pytest
from smoke_helpers import NexplaneClient, get_connector_creds_from_db, smoke_run_cr, smoke_rollback_cr

# All tests in this module share one event loop so the asyncpg connection pool
# (bound to the first loop) stays valid across test functions.
pytestmark = pytest.mark.asyncio(loop_scope="session")

# ---------------------------------------------------------------------------
# Known infra gap: no live Nexplane agent is registered in the smoke env.
# All tests that dispatch agent jobs (PHASE_1, PHASE_2, PHASE_4) are skipped
# when ASSET_ID has no AgentRegistration row in the database.
# PHASE_3 (get_host_full_context) is NOT skipped — it catches per-tool errors
# internally and returns a partial bundle, so the structural check still runs.
# ---------------------------------------------------------------------------

_AGENT_AVAILABLE: bool | None = None  # lazily evaluated once per session

_SMOKE_CLIENT: NexplaneClient | None = None
_SMOKE_LAUNCH_CR_ID: str | None = None
_SMOKE_API_TOKEN_ID: str | None = None
_SMOKE_BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
_SMOKE_EMAIL = os.environ.get("SMOKE_EMAIL", "admin@acme.example")
_SMOKE_PASSWORD = os.environ.get("SMOKE_PASSWORD", "admin123")


def setup_module(module):
    """Provision an ephemeral EC2 instance and deploy a Nexplane agent so tests run live."""
    global _AGENT_AVAILABLE, _SMOKE_CLIENT, _SMOKE_LAUNCH_CR_ID, _SMOKE_API_TOKEN_ID

    # Fast path: caller already provided ASSET_ID — skip provisioning
    if os.environ.get("ASSET_ID"):
        print("  [setup] ASSET_ID already set — skipping ephemeral provisioning")
        _AGENT_AVAILABLE = True
        return

    client = NexplaneClient(_SMOKE_BASE_URL, _SMOKE_EMAIL, _SMOKE_PASSWORD)
    _SMOKE_CLIENT = client

    # 1. Resolve AWS cloud account asset
    cloud_account_id = client.get_cloud_account_asset_id()

    # 2. Launch ephemeral EC2 instance
    print("  [setup] Launching nexplane-smoke-host-intel EC2 instance...")
    launch_cr = smoke_run_cr(
        client,
        "ec2_launch",
        {
            "mode": "quick",
            "name": "nexplane-smoke-host-intel",
            "os": "amazon_linux",
            "instance_type": "t3.small",
            "iam_instance_profile": "NexplaneEC2TestProfile",
            "rollback_strategy": "terminate_instance",
        },
        asset_ids=[cloud_account_id],
        timeout=300,
    )
    _SMOKE_LAUNCH_CR_ID = launch_cr["id"]

    # 3. Extract instance_id and ec2_asset_id from execution steps
    instance_id = None
    ec2_asset_id = None
    for run in launch_cr.get("execution_runs", []):
        if "rollback" in run.get("workflow_id", ""):
            continue
        steps = (run.get("result") or {}).get("execution", {}).get("steps", [])
        for s in steps:
            result = s.get("result") or {}
            if not instance_id and result.get("instance_id"):
                instance_id = result["instance_id"]
            if not ec2_asset_id and result.get("_auto_asset_id"):
                ec2_asset_id = result["_auto_asset_id"]
    if not instance_id:
        raise AssertionError(f"ec2_launch CR completed but no instance_id found in steps: {launch_cr}")

    print(f"  [setup] Launched {instance_id} (asset={ec2_asset_id})")

    # 4. Fetch instance hostname and private IP via boto3
    aws_creds = get_connector_creds_from_db("aws")
    import boto3
    ec2_boto = boto3.client(
        "ec2",
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
        region_name=aws_creds.get("region", "us-east-1"),
    )
    desc = ec2_boto.describe_instances(InstanceIds=[instance_id])
    inst_data = desc["Reservations"][0]["Instances"][0]
    private_ip = inst_data.get("PrivateIpAddress", "")
    hostname = inst_data.get("PrivateDnsName", "").split(".")[0]
    print(f"  [setup] Instance hostname={hostname} private_ip={private_ip}")

    # 5. Wait for SSM agent readiness
    print("  [setup] Waiting for SSM agent readiness...")
    ssm = boto3.client(
        "ssm",
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
        region_name=aws_creds.get("region", "us-east-1"),
    )
    ssm_deadline = _time.time() + 300
    while _time.time() < ssm_deadline:
        resp = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        if resp.get("InstanceInformationList"):
            print("  [setup] SSM agent ready")
            break
        _time.sleep(15)
    else:
        raise TimeoutError(f"SSM not ready for {instance_id} after 300s")

    # 6. Deploy Nexplane agent via CR
    print("  [setup] Deploying Nexplane agent...")
    agent_secret = client.get_agent_secret()
    smoke_run_cr(
        client,
        "deploy_nexplane_agent",
        {
            "instance_id": instance_id,
            "nexplane_url": "http://172.31.1.233:8000",
            "nexplane_secret": agent_secret,
        },
        asset_ids=[ec2_asset_id] if ec2_asset_id else None,
        timeout=300,
    )

    # 7. Wait for agent registration in the platform
    print("  [setup] Waiting for agent to register...")
    agent_asset_id = None
    reg_deadline = _time.time() + 300
    while _time.time() < reg_deadline:
        # Try searching by hostname first
        candidates = []
        if hostname:
            try:
                candidates = client.get(f"/assets", params={"asset_type": "server", "q": hostname})
            except Exception:
                pass
        # Also scan recent server assets for agent_version
        if not candidates:
            try:
                all_servers = client.get("/assets", params={"asset_type": "server", "limit": 200})
                candidates = [
                    a for a in all_servers
                    if (a.get("asset_metadata") or {}).get("agent_version")
                    or (private_ip and a.get("name", "") == hostname)
                ]
            except Exception:
                pass
        for asset in candidates:
            meta = asset.get("asset_metadata") or {}
            if meta.get("agent_version") or (private_ip and asset.get("name", "") == hostname):
                agent_asset_id = asset["id"]
                break
        if agent_asset_id:
            print(f"  [setup] Agent registered as asset {agent_asset_id}")
            break
        _time.sleep(15)
    else:
        raise TimeoutError("Nexplane agent did not register within 300s")

    # 8. Give agent one full collection cycle before tests begin
    print("  [setup] Sleeping 60s for initial collection cycle...")
    _time.sleep(60)

    os.environ["ASSET_ID"] = agent_asset_id

    # 9. Create a short-lived API token for the MCP tool calls
    token_resp = client.post("/tokens", json={"name": "smoke-host-intel"})
    raw_token = token_resp["raw_token"]
    _SMOKE_API_TOKEN_ID = token_resp["id"]
    os.environ["API_TOKEN"] = raw_token
    print("  [setup] API token created for MCP tool calls")

    _AGENT_AVAILABLE = True
    print("  [setup] Setup complete — 17 tests will run against live agent")


def teardown_module(module):
    """Roll back the ephemeral EC2 launch CR to terminate the smoke instance."""
    global _SMOKE_CLIENT, _SMOKE_LAUNCH_CR_ID, _SMOKE_API_TOKEN_ID
    if _SMOKE_API_TOKEN_ID and _SMOKE_CLIENT:
        try:
            _SMOKE_CLIENT.delete(f"/tokens/{_SMOKE_API_TOKEN_ID}")
        except Exception:
            pass
    if _SMOKE_LAUNCH_CR_ID and _SMOKE_CLIENT:
        print(f"  [teardown] Rolling back EC2 launch CR {_SMOKE_LAUNCH_CR_ID}...")
        smoke_rollback_cr(_SMOKE_CLIENT, _SMOKE_LAUNCH_CR_ID)


async def _agent_registered(asset_id: str) -> bool:
    """Return True if there is at least one AgentRegistration for *asset_id*."""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration

    asset_uuid = uuid.UUID(asset_id)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentRegistration).where(
                AgentRegistration.asset_id == asset_uuid
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def _require_agent(asset_id: str) -> None:
    """Skip the calling test if no agent is registered for *asset_id*."""
    global _AGENT_AVAILABLE
    if _AGENT_AVAILABLE is None:
        _AGENT_AVAILABLE = await _agent_registered(asset_id)
    if not _AGENT_AVAILABLE:
        pytest.skip(
            "No live Nexplane agent registered for asset — "
            "deploy the nexplane-agent binary and re-run to exercise these tools. "
            "Known infra gap: smoke env has 0 agent registrations."
        )


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping live smoke test")
    return val


async def _invoke(tool_fn, token: str, asset_id: str):
    result = await tool_fn(token, asset_id)
    if isinstance(result, dict) and "error" in result:
        raise AssertionError(f"{tool_fn.__name__} returned error: {result['error']}")
    if isinstance(result, list) and result and isinstance(result[0], dict) and "error" in result[0]:
        raise AssertionError(f"{tool_fn.__name__} returned error list: {result[0]['error']}")
    return result


async def test_PHASE_1_get_kernel_info():
    from app.mcp_tools.host_intelligence import get_kernel_info
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_kernel_info, token, asset_id)
    assert "version" in result, f"Missing 'version' key: {result}"
    assert "arch" in result, f"Missing 'arch' key: {result}"


async def test_PHASE_1_get_running_processes():
    from app.mcp_tools.host_intelligence import get_running_processes
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_running_processes, token, asset_id)
    assert isinstance(result, list), "Expected list"
    assert len(result) > 0, "No processes returned"
    assert "pid" in result[0]
    assert "name" in result[0]


async def test_PHASE_1_get_cron_jobs():
    from app.mcp_tools.host_intelligence import get_cron_jobs
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_cron_jobs, token, asset_id)
    assert isinstance(result, list)
    if result:
        assert "schedule" in result[0]


async def test_PHASE_1_get_local_users():
    from app.mcp_tools.host_intelligence import get_local_users
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_local_users, token, asset_id)
    assert isinstance(result, list)
    assert len(result) > 0, "No users returned"
    assert "username" in result[0]


async def test_PHASE_1_get_installed_packages():
    from app.mcp_tools.host_intelligence import get_installed_packages
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_installed_packages, token, asset_id)
    assert isinstance(result, list)
    assert len(result) > 0, "No packages returned"
    assert "name" in result[0]


async def test_PHASE_1_get_running_services():
    from app.mcp_tools.host_intelligence import get_running_services
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_running_services, token, asset_id)
    assert isinstance(result, list)
    if result:
        assert "name" in result[0]


async def test_PHASE_1_get_open_ports():
    from app.mcp_tools.host_intelligence import get_open_ports
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_open_ports, token, asset_id)
    assert isinstance(result, list)
    if result:
        assert "port" in result[0]


async def test_PHASE_1_get_security_posture():
    from app.mcp_tools.host_intelligence import get_security_posture
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_security_posture, token, asset_id)
    assert isinstance(result, dict)
    assert "selinux_mode" in result


async def test_PHASE_1_get_seccomp_policy():
    from app.mcp_tools.host_intelligence import get_seccomp_policy
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_seccomp_policy, token, asset_id)
    assert isinstance(result, dict)
    assert "active_profiles" in result


async def test_PHASE_1_get_apparmor_profiles():
    from app.mcp_tools.host_intelligence import get_apparmor_profiles
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_apparmor_profiles, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_selinux_policy():
    from app.mcp_tools.host_intelligence import get_selinux_policy
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_selinux_policy, token, asset_id)
    assert isinstance(result, dict)
    assert "mode" in result


async def test_PHASE_1_get_sudoers():
    from app.mcp_tools.host_intelligence import get_sudoers
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_sudoers, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_authorized_keys():
    from app.mcp_tools.host_intelligence import get_authorized_keys
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_authorized_keys, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_ssl_certs():
    from app.mcp_tools.host_intelligence import get_ssl_certs
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_ssl_certs, token, asset_id)
    assert isinstance(result, list)


async def test_PHASE_1_get_patch_status():
    from app.mcp_tools.host_intelligence import get_patch_status
    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)
    result = await _invoke(get_patch_status, token, asset_id)
    assert isinstance(result, dict)
    assert "pending_patches_count" in result
    assert "critical_pending" in result


async def test_PHASE_2_cache_hit_kernel():
    """Second call within 300 s must return without dispatching a new agent job."""
    from app.mcp_tools.host_intelligence import get_kernel_info
    from app.database import AsyncSessionLocal
    from app.models.mcp_intelligence_cache import McpIntelligenceCache
    from sqlalchemy import select, func

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    await _require_agent(asset_id)

    # First call — populates cache
    await _invoke(get_kernel_info, token, asset_id)

    # Count cache entries before second call
    async with AsyncSessionLocal() as db:
        before = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.tool_name == "get_kernel_info"
            )
        )
        count_before = before.scalar()

    # Second call — should hit cache
    result2 = await _invoke(get_kernel_info, token, asset_id)

    async with AsyncSessionLocal() as db:
        after = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.tool_name == "get_kernel_info"
            )
        )
        count_after = after.scalar()

    assert count_after == count_before, (
        f"Cache grew from {count_before} to {count_after} — second call re-dispatched"
    )
    assert "version" in result2


async def test_PHASE_3_get_host_full_context():
    from app.mcp_tools.host_intelligence import get_host_full_context

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")

    result = await get_host_full_context(token, asset_id)

    assert "error" not in result, f"full_context returned error: {result.get('error')}"

    expected_keys = {
        "asset_id", "kernel", "processes", "cron_jobs", "local_users",
        "installed_packages", "running_services", "open_ports", "security_posture",
        "seccomp_policy", "apparmor_profiles", "selinux_policy", "sudoers",
        "authorized_keys", "ssl_certs", "patch_status",
    }
    missing = expected_keys - set(result.keys())
    assert not missing, f"Missing keys in full_context: {missing}"


async def test_PHASE_4_cache_invalidate_forces_redispatch():
    from app.mcp_tools.host_intelligence import get_kernel_info
    from app.services.host_intelligence_service import invalidate_cache
    from app.database import AsyncSessionLocal
    from app.models.mcp_intelligence_cache import McpIntelligenceCache
    from app.models.asset import Asset
    from sqlalchemy import select, func
    import uuid as _uuid

    token = _env("API_TOKEN")
    asset_id_str = _env("ASSET_ID")
    await _require_agent(asset_id_str)
    asset_uuid = _uuid.UUID(asset_id_str)

    # Ensure at least one cache entry exists
    await _invoke(get_kernel_info, token, asset_id_str)

    # Find the org_id for the asset
    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, asset_uuid)
        assert asset, "ASSET_ID not found in DB"
        org_id = asset.organization_id

    # Invalidate
    async with AsyncSessionLocal() as db:
        await invalidate_cache(db, org_id, asset_id_str)

    # Verify cache is empty for this asset
    async with AsyncSessionLocal() as db:
        count_result = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.asset_id == asset_uuid,
            )
        )
        count = count_result.scalar()
    assert count == 0, f"Expected 0 cache entries after invalidate, got {count}"

    # Next call must re-populate cache
    result = await _invoke(get_kernel_info, token, asset_id_str)
    assert "version" in result

    async with AsyncSessionLocal() as db:
        count_result = await db.execute(
            select(func.count()).select_from(McpIntelligenceCache).where(
                McpIntelligenceCache.asset_id == asset_uuid,
                McpIntelligenceCache.tool_name == "get_kernel_info",
            )
        )
        count_after = count_result.scalar()
    assert count_after == 1, f"Expected 1 cache entry after re-dispatch, got {count_after}"
