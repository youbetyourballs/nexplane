# FILO Smoke Self-Contained Provisioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `test_smoke_filo_rollback.py` provision its own fresh EC2 instance, deploy the Nexplane agent via the CR lifecycle, run all 5 FILO phases against it, and terminate it only on full pass.

**Architecture:** Add `PHASE_0_provision` to `test_smoke_filo_rollback.py`. It resolves the backend's private VPC IP from EC2 instance metadata, provisions an EC2 instance via the Nexplane CR lifecycle, deploys the agent, polls until it registers, then runs the existing 5 phases. A pytest finalizer terminates the instance on pass, leaves it running on failure. `ASSET_ID` env var bypasses provisioning entirely. Provisioning helpers are duplicated inline — no shared module.

**Tech Stack:** Python/asyncio, httpx, boto3 (for teardown only), pytest-asyncio, Nexplane CR lifecycle (`ec2_launch`, `deploy_nexplane_agent`).

## Global Constraints

- NEVER use `from __future__ import annotations`.
- Maximum two hosts — PHASE_0 provisions exactly one EC2 instance.
- All provisioning uses the Nexplane CR lifecycle (create → approve → execute via MCP tools), not direct boto3/SSM calls.
- Teardown uses boto3 `ec2.terminate_instances()` directly — no rollback CR.
- Instance type: `t3.small`, OS param: `amazon_linux`, IAM profile: `NexplaneEC2TestProfile`.
- No Tailscale — VPC-internal connectivity only; agent phones home via backend's private IP.
- Provisioning helpers duplicated inline — no imports from `test_agent_live.py`.

---

## File Structure

**Modify only:** `backend/tests/smoke/test_smoke_filo_rollback.py`

Changes:
- Add `import asyncio` (already present), `import boto3` for teardown
- Add `_get_backend_private_ip()` helper
- Add `_create_and_execute_cr()` generic helper (wraps create → approve → execute → poll)
- Add `pytest_runtest_logreport` hook (sets `_STATE["failed"]` on any phase failure)
- Add `_teardown()`, `_terminate_instance()`, `_delete_smoke_asset()` helpers
- Add `test_PHASE_0_provision()` test function before PHASE_1
- Replace `_env("ASSET_ID")` calls in PHASE_1, PHASE_2, PHASE_4, PHASE_5 with `_STATE["asset_id"]`
- Update module docstring to remove "Prerequisites: ASSET_ID env var"

---

## Task 1: Add provisioning to FILO smoke test

**Files:**
- Modify: `backend/tests/smoke/test_smoke_filo_rollback.py`

**Interfaces:**
- Consumes: existing `_get_jwt()`, `_create_and_execute_sysctl_cr()`, `_env()`, `_STATE`, `_BASE_URL`
- Produces: `_STATE["asset_id"]` (used by PHASE_1–5), `_STATE["instance_id"]` (used by teardown)

- [ ] **Step 1: Add `boto3` import and `_get_backend_private_ip` helper**

Open `backend/tests/smoke/test_smoke_filo_rollback.py`. After the existing imports block (after line 26 `import pytest`), add:

```python
import boto3
```

After the `_BASE_URL = "http://localhost:8000/api/v1"` line, add:

```python
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
```

- [ ] **Step 2: Add `_create_and_execute_cr` generic helper**

After `_create_and_execute_sysctl_cr`, add:

```python
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
```

- [ ] **Step 3: Add failure-flag hook and teardown helpers**

Before the `# PHASE_1` comment block, add:

```python
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
```

- [ ] **Step 4: Add `test_PHASE_0_provision`**

Immediately before `test_PHASE_1_execute_cr_a`, add:

```python
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
```

- [ ] **Step 5: Replace `_env("ASSET_ID")` in PHASE_1, PHASE_2, PHASE_4, PHASE_5**

Find and replace every occurrence of `_env("ASSET_ID")` in the existing phase tests with `_STATE["asset_id"]`. There are 5 occurrences:

- Line 112 (PHASE_1): `asset_id = _env("ASSET_ID")` → `asset_id = _STATE["asset_id"]`
- Line 144 (PHASE_2): `asset_id = _env("ASSET_ID")` → `asset_id = _STATE["asset_id"]`
- Line 217 (PHASE_4): `asset_id = _env("ASSET_ID")` → `asset_id = _STATE["asset_id"]`
- Line 263 (PHASE_5): `asset_id = _env("ASSET_ID")` → `asset_id = _STATE["asset_id"]`
- Line 279 (PHASE_5): `asset_id = _env("ASSET_ID")` → `asset_id = _STATE["asset_id"]`

Also add a `_STATE` guard to PHASE_1 (it now depends on PHASE_0):
After `token = _env("API_TOKEN")` in `test_PHASE_1_execute_cr_a`, add:
```python
if "asset_id" not in _STATE:
    pytest.skip("PHASE_0 did not run")
```

- [ ] **Step 6: Update module docstring**

Replace the docstring at the top of the file (lines 3–20):

```python
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
```

- [ ] **Step 7: Syntax check**

```bash
cd backend
python -m py_compile tests/smoke/test_smoke_filo_rollback.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add backend/tests/smoke/test_smoke_filo_rollback.py
git commit -m "feat(smoke): self-contained provisioning in FILO rollback smoke test"
```

---

## Test Execution

Run on EC2 (full provisioning flow):
```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s"
```

All 6 phases (PHASE_0 through PHASE_5) must pass. Total runtime ~15 minutes (180s SSM wait + CR execution + 5 FILO phases).

On failure, the EC2 instance remains running. Get its private IP from the AWS console by searching for instance name `nexplane-smoke-filo`, then SSH or SSM into it to inspect. Terminate manually once done.
