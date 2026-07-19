# Onboarding Wizard FILO Rollback + §2 Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `provision_instance` to health-check the EC2 before marking customers active, then prove the full onboarding wizard (create_customer → provision_instance → generate_setup_token) works end-to-end with FILO rollback via a live smoke test.

**Architecture:** Add a TCP-connect health check loop inside `provision_instance/execute.py` that fires only for managed (non-dry-run) provisioning; stash the instance_id in the registry before the loop so rollback can find it if the health check times out and raises. The smoke test submits a `catalog_workflow` CR with all three wizard steps via the platform API, verifies forward execution, FILO rollback, and the §2 regression guard (health check timeout → STATE_FAILED).

**Tech Stack:** Python 3.12, asyncio, pytest + pytest-asyncio, httpx (smoke ops_client), boto3 (AWS EC2 verification), nexplane platform REST API.

## Global Constraints

- No `from __future__ import annotations` in any Python file.
- All live smoke phases run from the EC2 runner on Tailscale; never from laptop/local Docker.
- Smoke tests must stand up their own infra; they must not depend on pre-existing state.
- All functionality must run against live infrastructure — mocks are not acceptable for smoke phases.
- `delivery_model == "managed"` health check only; self-hosted path is unchanged.
- Health check probe: TCP connect to port 443 on `result["private_ip"]`. Not HTTPS — plain TCP connect (TLS negotiation not required to pass).
- Configurable timeout: `parameters.get("health_check_timeout_secs")` takes priority over `os.environ.get("PROVISION_HEALTH_TIMEOUT_SECS", "300")`.
- The `instance_id` must be written to the registry (via `reg.update_customer_section`) BEFORE the health check loop begins, so rollback can find it even if `execute()` raises.
- Rollback: if `execution_result` has no `instance_id`, fall back to `customer["instance"]["instance_id"]` from registry.
- Smoke: `NEXPLANE_SMOKE=1` env var gates the smoke; skip if not set.
- Smoke file location: `nexplane-deploy/smoke/test_onboarding_wizard_smoke.py`.
- Unit test file to extend: `nexplane-deploy/tests/test_provision_instance.py`.

---

### Task 1: §2 Fix — Health Check in provision_instance Executor

**Files:**
- Modify: `nexplane-deploy/executors/provision_instance/execute.py`
- Modify: `nexplane-deploy/tests/test_provision_instance.py`

**Interfaces:**
- Produces: `ProvisioningHealthCheckTimeout` exception class (module-level in execute.py)
- Produces: `_wait_for_instance_health(host, port=443, timeout_secs=300, interval_secs=10)` async function
- Produces: modified `execute()` that raises `ProvisioningHealthCheckTimeout` on timeout
- Produces: modified `rollback()` that falls back to registry when `execution_result["instance_id"]` is absent

- [ ] **Step 1: Write failing tests**

Add to `nexplane-deploy/tests/test_provision_instance.py` — append after the last existing test:

```python
# ---------------------------------------------------------------------------
# §2 health check tests
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from executors.provision_instance.execute import (
    ProvisioningHealthCheckTimeout,
    _wait_for_instance_health,
)


@pytest.mark.asyncio
async def test_health_check_succeeds_on_tcp_connect():
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    with patch("asyncio.open_connection", new=AsyncMock(return_value=(MagicMock(), mock_writer))):
        await _wait_for_instance_health("10.0.1.5", timeout_secs=5)
    # No exception == pass


@pytest.mark.asyncio
async def test_health_check_times_out():
    async def _always_fail(*a, **kw):
        raise ConnectionRefusedError("refused")

    with patch("asyncio.open_connection", new=_always_fail):
        with pytest.raises(ProvisioningHealthCheckTimeout):
            await _wait_for_instance_health("10.0.1.5", timeout_secs=1, interval_secs=0)


@pytest.mark.asyncio
async def test_execute_managed_with_creds_health_check_succeeds(mock_connector, registry_dir):
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    mock_connector.credentials = {"aws_access_key_id": "k", "aws_secret_access_key": "s"}
    with patch("asyncio.open_connection", new=AsyncMock(return_value=(MagicMock(), mock_writer))):
        result = await execute(
            {"client_id": "hc-test", "mode": "managed", "dry_run": False},
            [],
            mock_connector,
        )
    assert result["success"] is True
    customer = reg.load_customer("hc-test", registry_dir)
    assert customer["status"]["state"] == "active"


@pytest.mark.asyncio
async def test_execute_managed_health_check_failure_marks_failed(mock_connector, registry_dir):
    async def _always_fail(*a, **kw):
        raise ConnectionRefusedError("refused")

    mock_connector.credentials = {"aws_access_key_id": "k", "aws_secret_access_key": "s"}
    with patch("asyncio.open_connection", new=_always_fail):
        with pytest.raises(ProvisioningHealthCheckTimeout):
            await execute(
                {
                    "client_id": "hc-fail",
                    "mode": "managed",
                    "dry_run": False,
                    "health_check_timeout_secs": 1,
                },
                [],
                mock_connector,
            )
    customer = reg.load_customer("hc-fail", registry_dir)
    assert customer["status"]["state"] == "failed"
    # instance_id must be stashed in registry even though execute() raised
    assert customer["instance"]["instance_id"]


@pytest.mark.asyncio
async def test_rollback_falls_back_to_registry_for_instance_id(mock_connector, registry_dir):
    # Simulate a health-check-timeout scenario: instance_id IS in registry but
    # NOT in execution_result (execute() raised before returning).
    reg.create_customer(
        client_slug="rb-test",
        display_name="rb-test",
        contact_email="rb@test.local",
        delivery_model=reg.DELIVERY_MANAGED_SINGLE_EC2,
        plan="trial",
        region="us-east-1",
        registry_dir=registry_dir,
    )
    reg.update_customer_section(
        "rb-test", "instance", {"instance_id": "i-fallback001"}, registry_dir=registry_dir
    )
    # execution_result has no instance_id (it would if execute() had raised)
    result = await rollback(
        {"client_id": "rb-test", "mode": "managed"},
        {"client_id": "rb-test"},  # no instance_id here
        mock_connector,
    )
    assert result["success"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd f:/Nexplane/nexplane-deploy
python -m pytest tests/test_provision_instance.py::test_health_check_succeeds_on_tcp_connect tests/test_provision_instance.py::test_health_check_times_out tests/test_provision_instance.py::test_execute_managed_with_creds_health_check_succeeds tests/test_provision_instance.py::test_execute_managed_health_check_failure_marks_failed tests/test_provision_instance.py::test_rollback_falls_back_to_registry_for_instance_id -v
```

Expected: FAIL with `ImportError: cannot import name 'ProvisioningHealthCheckTimeout'`

- [ ] **Step 3: Add `ProvisioningHealthCheckTimeout` and `_wait_for_instance_health` to `executors/provision_instance/execute.py`**

The file already has `import os as _os` near the top (inside the `_COMMERCIAL_ROOT` sys.path block). Add `import asyncio as _asyncio` on the line immediately after `import os as _os, sys as _sys` (the existing line). Do NOT add a second `import os` — the existing `_os` is reused. In Step 4, use `_os` (not `_os_hc`) everywhere — `_os_hc` is not needed.

Then add this class and function after the existing `from executors.common import ...` block, before the `# ---------------------------------------------------------------------------\n# Helpers` comment line:

```python
class ProvisioningHealthCheckTimeout(Exception):
    """Raised when a managed EC2 instance does not serve port 443 within the timeout."""


async def _wait_for_instance_health(
    host: str,
    port: int = 443,
    timeout_secs: int = 300,
    interval_secs: int = 10,
) -> None:
    """Poll host:port via TCP connect until it accepts a connection or timeout elapses."""
    loop = _asyncio.get_event_loop()
    deadline = loop.time() + timeout_secs
    while loop.time() < deadline:
        try:
            _, writer = await _asyncio.wait_for(
                _asyncio.open_connection(host, port),
                timeout=5.0,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        except Exception:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await _asyncio.sleep(min(interval_secs, remaining))
    raise ProvisioningHealthCheckTimeout(
        f"Instance at {host}:{port} did not respond within {timeout_secs}s"
    )
```

- [ ] **Step 4: Modify `execute()` to stash instance_id and run health check**

In the `execute()` function, find the block immediately after `result = await provisioner.provision(...)` (the line that ends with `params=parameters)`). It currently proceeds directly to connectivity and `_record_provisioned`. Insert the health check block between `result = await provisioner.provision(...)` and `# Establish connectivity`:

```python
    # Stash instance_id in registry BEFORE health check so rollback can find
    # it even if the health check raises and execute() never returns a result.
    _instance_id_early = result.get("instance_id")
    if _instance_id_early:
        reg.update_customer_section(
            client_id, "instance", {"instance_id": _instance_id_early},
            registry_dir=registry_dir,
        )

    # Health check: only for live managed provisioning.
    if delivery_model == reg.DELIVERY_MANAGED_SINGLE_EC2 and not dry_run:
        _probe_host = result.get("private_ip") or result.get("public_ip")
        if not _probe_host:
            reg.update_customer_status(
                client_id, reg.STATE_FAILED, registry_dir=registry_dir,
                event_detail="provision_instance: no IP in result; cannot health check",
            )
            raise ProvisioningHealthCheckTimeout(
                "No IP address returned by provisioner; cannot verify instance health"
            )
        _hc_timeout = int(
            parameters.get("health_check_timeout_secs")
            or _os.environ.get("PROVISION_HEALTH_TIMEOUT_SECS", "300")
        )
        try:
            await _wait_for_instance_health(_probe_host, timeout_secs=_hc_timeout)
        except ProvisioningHealthCheckTimeout:
            reg.update_customer_status(
                client_id, reg.STATE_FAILED, registry_dir=registry_dir,
                event_detail=f"health check timeout after {_hc_timeout}s on {_probe_host}:443",
            )
            raise
```

Note: the `import os as _os` already exists at the top of the file. Add `import asyncio as _asyncio` alongside it. Use `_os` (not a new alias) when referencing `os.environ` in the health check block.

- [ ] **Step 5: Modify `rollback()` to fall back to registry for instance_id**

In the `rollback()` function, find the block:

```python
    # Managed: delegate to terminate_instance
    instance_id = execution_result.get("instance_id")
    if not instance_id:
        return {"success": False, "error": "No instance_id in execution_result; cannot terminate."}
```

Replace it with:

```python
    # Managed: delegate to terminate_instance
    instance_id = execution_result.get("instance_id")
    if not instance_id:
        # Health check may have raised before execute() returned. Fall back to
        # instance_id stashed in registry before the health check loop began.
        if reg.customer_exists(client_id, registry_dir):
            _customer = reg.load_customer(client_id, registry_dir)
            instance_id = _customer.get("instance", {}).get("instance_id")
    if not instance_id:
        return {"success": False, "error": "No instance_id in execution_result or registry; cannot terminate."}
```

- [ ] **Step 6: Run all provision_instance tests to verify they pass**

```
cd f:/Nexplane/nexplane-deploy
python -m pytest tests/test_provision_instance.py -v
```

Expected: All tests PASS (including the 5 new §2 tests and all pre-existing tests).

**Important:** The existing `test_execute_managed_mock` and `test_provision_creates_registry_entry_and_marks_active` tests use `mock_connector` (empty credentials), which means `dry_run=True`. The health check only fires when `not dry_run`, so these tests must still pass without modification.

- [ ] **Step 7: Commit**

```bash
cd f:/Nexplane/nexplane-deploy
git add executors/provision_instance/execute.py tests/test_provision_instance.py
git commit -m "fix: health check in provision_instance before marking STATE_ACTIVE (§2)"
```

---

### Task 2: Onboarding Wizard Smoke Test (Phases 1, 2, and 3)

**Files:**
- Create: `nexplane-deploy/smoke/test_onboarding_wizard_smoke.py`

**Interfaces:**
- Consumes: `ops_client` fixture from `smoke/conftest.py` (httpx.Client with Bearer auth to `NEXPLANE_OPS_URL`)
- Consumes: `aws_creds` fixture from `smoke/conftest.py`
- Consumes: platform API endpoints: `POST /api/v1/change-requests`, `POST /api/v1/change-requests/{id}/approve`, `GET /api/v1/change-requests/{id}`, `POST /api/v1/change-requests/{id}/rollback`
- Consumes: `ProvisioningHealthCheckTimeout` (Task 1) — needed conceptually; smoke verifies STATE_FAILED not the exception

- [ ] **Step 1: Understand the exact CR payload shape**

Before writing the test, verify what `Customers.tsx` sends by checking the frontend source. Run on the EC2 runner or locally:

```bash
grep -n "catalog_workflow\|desired_outcome\|steps\|action_id" \
  f:/Nexplane/nexplane/frontend/src/pages/Customers.tsx | head -40
```

Confirm the payload structure matches what the plan assumes below. If `connector_type` is named differently or `steps` nests differently, adjust the payload in Steps 2–4 accordingly.

Also confirm the CR status field name by checking a real CR via:
```bash
# On EC2 runner (replace TOKEN and URL):
curl -s -H "Authorization: Bearer $TOKEN" $NEXPLANE_OPS_URL/api/v1/change-requests | python3 -m json.tool | head -60
```

The plan assumes `cr["status"]` holds values like `"STATE_COMPLETE"`, `"STATE_FAILED"`. If the actual field is named `state` or uses different values, adjust accordingly.

- [ ] **Step 2: Create the smoke file scaffold and Phase 1 (happy path)**

Create `nexplane-deploy/smoke/test_onboarding_wizard_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Onboarding Wizard FILO Rollback Smoke Test

Phases:
  1. HAPPY PATH — full wizard forward execution; assert STATE_ACTIVE + EC2 running
  2. FILO ROLLBACK — provision then rollback; assert reverse teardown
  3. §2 REGRESSION GUARD — health check timeout; assert STATE_FAILED + EC2 terminated

Run from EC2 runner:
    NEXPLANE_SMOKE=1 \\
    NEXPLANE_OPS_URL=http://localhost:8000 \\
    NEXPLANE_OPS_TOKEN=<token> \\
    python -m pytest smoke/test_onboarding_wizard_smoke.py -v -s
"""
import os
import time
import uuid

import boto3
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"\n[wizard-smoke] {msg}", flush=True)


def _slug() -> str:
    return f"wiz-smoke-{uuid.uuid4().hex[:8]}"


def _wizard_steps(slug: str, *, health_check_timeout_secs: int | None = None) -> list:
    provision_params = {
        "client_id": slug,
        "mode": "managed",
        "artifact_version": "stable",
        "aws_region": AWS_REGION,
    }
    if health_check_timeout_secs is not None:
        provision_params["health_check_timeout_secs"] = health_check_timeout_secs
    return [
        {
            "connector_type": "commercial",
            "action_id": "create_customer",
            "params": {
                "client_id": slug,
                "display_name": f"Smoke {slug}",
                "contact_email": f"{slug}@smoke.nexplane.test",
                "plan": "trial",
                "delivery_model": "managed_single_ec2",
                "region": AWS_REGION,
            },
        },
        {
            "connector_type": "commercial",
            "action_id": "provision_instance",
            "params": provision_params,
        },
        {
            "connector_type": "commercial",
            "action_id": "generate_setup_token",
            "params": {
                "client_id": slug,
                "instance_url": f"https://{slug}.nexplane.io",
            },
        },
    ]


def _submit_wizard_cr(ops_client, slug: str, **kwargs) -> str:
    """Submit a catalog_workflow CR; return the CR id."""
    resp = ops_client.post(
        "/api/v1/change-requests",
        json={
            "change_type": "catalog_workflow",
            "title": f"Wizard smoke: {slug}",
            "desired_outcome": {"steps": _wizard_steps(slug, **kwargs)},
        },
    )
    assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
    cr_id = resp.json()["id"]
    log(f"Created CR {cr_id} for slug {slug}")
    return cr_id


def _approve(ops_client, cr_id: str) -> None:
    resp = ops_client.post(f"/api/v1/change-requests/{cr_id}/approve")
    assert resp.status_code in (200, 204), f"Approve failed {resp.status_code}: {resp.text}"
    log(f"Approved CR {cr_id}")


def _poll_cr(ops_client, cr_id: str, timeout: int = 720) -> dict:
    """Poll until CR reaches a terminal state. Returns the CR dict."""
    terminal = {"STATE_COMPLETE", "STATE_FAILED", "STATE_CANCELLED", "failed", "complete", "cancelled"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = ops_client.get(f"/api/v1/change-requests/{cr_id}").json()
        status = cr.get("status") or cr.get("state", "")
        log(f"  CR {cr_id[:8]}: {status}")
        if status in terminal or status.upper() in {t.upper() for t in terminal}:
            return cr
        time.sleep(15)
    raise TimeoutError(f"CR {cr_id} did not reach terminal state in {timeout}s")


def _poll_until_step_complete(ops_client, cr_id: str, step_action: str, timeout: int = 720) -> None:
    """Poll until a specific step within the CR shows complete."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = ops_client.get(f"/api/v1/change-requests/{cr_id}").json()
        steps = (
            cr.get("desired_outcome", {}).get("steps")
            or cr.get("steps")
            or []
        )
        for step in steps:
            if step.get("action_id") == step_action:
                step_status = step.get("status") or step.get("state", "")
                log(f"  step {step_action}: {step_status}")
                if "complete" in step_status.lower() or step_status.upper() == "STATE_COMPLETE":
                    return
        cr_status = cr.get("status") or cr.get("state", "")
        if "fail" in cr_status.lower() or "cancel" in cr_status.lower():
            raise RuntimeError(f"CR {cr_id} failed before {step_action} completed: {cr_status}")
        time.sleep(10)
    raise TimeoutError(f"Step {step_action} in CR {cr_id} did not complete in {timeout}s")


def _ec2_instances_for_slug(aws_creds: dict, slug: str) -> list:
    ec2 = boto3.client(
        "ec2",
        region_name=AWS_REGION,
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
    )
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:ClientId", "Values": [slug]},
            {"Name": "tag:ManagedBy", "Values": ["nexplane"]},
        ]
    )
    return [
        i
        for r in resp["Reservations"]
        for i in r["Instances"]
    ]


def _get_customer(ops_client, slug: str) -> dict | None:
    resp = ops_client.get(f"/api/v1/customers/{slug}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _cleanup_slug(ops_client, aws_creds: dict, slug: str) -> None:
    """Best-effort cleanup: terminate EC2 and delete customer."""
    try:
        ec2 = boto3.client(
            "ec2",
            region_name=AWS_REGION,
            aws_access_key_id=aws_creds["access_key_id"],
            aws_secret_access_key=aws_creds["secret_access_key"],
        )
        instances = _ec2_instances_for_slug(aws_creds, slug)
        ids = [i["InstanceId"] for i in instances if i["State"]["Name"] not in ("terminated", "shutting-down")]
        if ids:
            ec2.terminate_instances(InstanceIds=ids)
            log(f"  cleanup: terminated {ids}")
    except Exception as e:
        log(f"  cleanup EC2 error (ignored): {e}")
    try:
        ops_client.delete(f"/api/v1/customers/{slug}")
    except Exception as e:
        log(f"  cleanup customer error (ignored): {e}")


# ---------------------------------------------------------------------------
# Phase 1: Happy path
# ---------------------------------------------------------------------------

def test_wizard_phase1_happy_path(ops_client, aws_creds):
    slug = _slug()
    log(f"\n=== PHASE 1: HAPPY PATH (slug={slug}) ===")
    try:
        cr_id = _submit_wizard_cr(ops_client, slug)
        _approve(ops_client, cr_id)
        cr = _poll_cr(ops_client, cr_id, timeout=720)

        status = (cr.get("status") or cr.get("state", "")).upper()
        assert "COMPLETE" in status, (
            f"Expected STATE_COMPLETE, got {status!r}. CR: {cr}"
        )

        # Customer must be STATE_ACTIVE
        customer = _get_customer(ops_client, slug)
        assert customer is not None, f"Customer {slug!r} not found after wizard CR completed"
        cust_state = (customer.get("status", {}).get("state") or customer.get("state", "")).lower()
        assert cust_state == "active", f"Customer state should be active, got {cust_state!r}"
        log(f"  customer {slug}: state={cust_state} ✓")

        # EC2 instance must be running
        instances = _ec2_instances_for_slug(aws_creds, slug)
        running = [i for i in instances if i["State"]["Name"] == "running"]
        assert running, (
            f"No running EC2 with ClientId={slug!r} after wizard completed. "
            f"Found: {[(i['InstanceId'], i['State']['Name']) for i in instances]}"
        )
        log(f"  EC2 running: {[i['InstanceId'] for i in running]} ✓")

        # EC2 must be reachable on port 443 (health check already verified this)
        import socket
        for inst in running:
            ip = inst.get("PrivateIpAddress")
            try:
                s = socket.create_connection((ip, 443), timeout=10)
                s.close()
                log(f"  port 443 reachable on {ip} ✓")
            except Exception as e:
                raise AssertionError(f"Port 443 unreachable on {ip}: {e}") from e

        log("Phase 1 PASSED ✓")
    finally:
        _cleanup_slug(ops_client, aws_creds, slug)


# ---------------------------------------------------------------------------
# Phase 2: FILO rollback
# ---------------------------------------------------------------------------

def test_wizard_phase2_filo_rollback(ops_client, aws_creds):
    slug = _slug()
    log(f"\n=== PHASE 2: FILO ROLLBACK (slug={slug}) ===")
    try:
        cr_id = _submit_wizard_cr(ops_client, slug)
        _approve(ops_client, cr_id)

        # Wait until provision_instance step completes (customer active, EC2 running)
        _poll_until_step_complete(ops_client, cr_id, "provision_instance", timeout=720)
        log(f"  provision_instance step complete; triggering rollback")

        # Trigger rollback
        resp = ops_client.post(f"/api/v1/change-requests/{cr_id}/rollback")
        assert resp.status_code in (200, 202, 204), (
            f"Rollback trigger failed {resp.status_code}: {resp.text}"
        )

        # Poll until rollback completes
        cr = _poll_cr(ops_client, cr_id, timeout=300)
        rb_status = (cr.get("status") or cr.get("state", "")).upper()
        assert "ROLLED_BACK" in rb_status or "COMPLETE" in rb_status, (
            f"Expected rollback terminal state, got {rb_status!r}"
        )
        log(f"  rollback terminal state: {rb_status} ✓")

        # Customer must NOT be active (deleted or terminated)
        customer = _get_customer(ops_client, slug)
        if customer is not None:
            cust_state = (customer.get("status", {}).get("state") or customer.get("state", "")).lower()
            assert cust_state in ("terminated", "deleted"), (
                f"Customer should be terminated/deleted after rollback, got {cust_state!r}"
            )
            log(f"  customer {slug}: state={cust_state} ✓")
        else:
            log(f"  customer {slug}: not found (deleted) ✓")

        # EC2 instance must be terminated
        instances = _ec2_instances_for_slug(aws_creds, slug)
        non_terminated = [
            i for i in instances
            if i["State"]["Name"] not in ("terminated", "shutting-down")
        ]
        assert not non_terminated, (
            f"EC2 instances not terminated after rollback: "
            f"{[(i['InstanceId'], i['State']['Name']) for i in non_terminated]}"
        )
        log(f"  EC2 instances terminated ✓")

        log("Phase 2 PASSED ✓")
    finally:
        _cleanup_slug(ops_client, aws_creds, slug)


# ---------------------------------------------------------------------------
# Phase 3: §2 regression guard
# ---------------------------------------------------------------------------

def test_wizard_phase3_s2_regression_guard(ops_client, aws_creds):
    """Prove that a managed wizard CR fails (not succeeds) when the health check
    times out. The customer must NOT be marked STATE_ACTIVE, and any EC2 instance
    created must be terminated by automatic rollback."""
    slug = _slug()
    log(f"\n=== PHASE 3: §2 REGRESSION GUARD (slug={slug}) ===")
    try:
        # health_check_timeout_secs=5: port 443 won't be up in 5s on a fresh instance
        cr_id = _submit_wizard_cr(ops_client, slug, health_check_timeout_secs=5)
        _approve(ops_client, cr_id)

        # Poll with generous timeout — CR must fail, not succeed
        cr = _poll_cr(ops_client, cr_id, timeout=300)
        status = (cr.get("status") or cr.get("state", "")).upper()
        assert "FAIL" in status, (
            f"§2 regression: CR should have reached STATE_FAILED, got {status!r}. "
            f"If it reached STATE_COMPLETE, the health check is not running. CR: {cr}"
        )
        log(f"  CR status: {status} ✓")

        # Customer must NOT be STATE_ACTIVE
        customer = _get_customer(ops_client, slug)
        if customer is not None:
            cust_state = (customer.get("status", {}).get("state") or customer.get("state", "")).lower()
            assert cust_state != "active", (
                f"§2 regression: customer should not be active after health check timeout, "
                f"got {cust_state!r}"
            )
            log(f"  customer {slug}: state={cust_state} (not active) ✓")

        # Wait for automatic rollback to terminate EC2 (give it 120s)
        deadline = time.time() + 120
        while time.time() < deadline:
            instances = _ec2_instances_for_slug(aws_creds, slug)
            non_terminated = [
                i for i in instances
                if i["State"]["Name"] not in ("terminated", "shutting-down")
            ]
            if not non_terminated:
                break
            log(f"  waiting for EC2 termination: {[(i['InstanceId'], i['State']['Name']) for i in non_terminated]}")
            time.sleep(15)
        else:
            instances = _ec2_instances_for_slug(aws_creds, slug)
            non_terminated = [
                i for i in instances
                if i["State"]["Name"] not in ("terminated", "shutting-down")
            ]
            assert not non_terminated, (
                f"§2 regression: EC2 not terminated after rollback: "
                f"{[(i['InstanceId'], i['State']['Name']) for i in non_terminated]}"
            )

        log(f"  EC2 instances terminated by rollback ✓")
        log("Phase 3 PASSED ✓")
    finally:
        _cleanup_slug(ops_client, aws_creds, slug)
```

- [ ] **Step 3: Run the smoke test against the live platform (EC2 runner)**

SSH to the EC2 runner (Tailscale IP: 100.101.186.39) and run:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39

# First, push Task 1 changes to EC2
cd ~/nexplane-deploy
git pull

# Set env vars
export NEXPLANE_SMOKE=1
export NEXPLANE_OPS_URL=http://localhost:8000
export NEXPLANE_OPS_TOKEN=$(cat /run/secrets/ops_token 2>/dev/null || echo "")

# If token is empty, get it from the platform:
# NEXPLANE_OPS_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
#   -H "Content-Type: application/json" \
#   -d '{"email":"admin@acme.example","password":"admin123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

export AWS_ACCESS_KEY_ID=$(cat ~/.nexplane_aws_key_id 2>/dev/null || echo "$AWS_ACCESS_KEY_ID")
export AWS_SECRET_ACCESS_KEY=$(cat ~/.nexplane_aws_secret 2>/dev/null || echo "$AWS_SECRET_ACCESS_KEY")
export AWS_DEFAULT_REGION=us-east-1

python -m pytest smoke/test_onboarding_wizard_smoke.py -v -s
```

Expected output:
```
PASSED smoke/test_onboarding_wizard_smoke.py::test_wizard_phase1_happy_path
PASSED smoke/test_onboarding_wizard_smoke.py::test_wizard_phase2_filo_rollback
PASSED smoke/test_onboarding_wizard_smoke.py::test_wizard_phase3_s2_regression_guard
3 passed
```

**If Phase 1 or 2 fails with connection refused on port 443:** The Nexplane instance may not expose port 443 by default (if using HTTP internally). Check the provisioner output to see what `instance_url` the managed EC2 provisioner returns. If the stack uses port 443 with self-signed TLS, the TCP connect will succeed (TLS handshake not required). If the stack uses port 80 or 8080, update the health check port in `_wait_for_instance_health` call and in the smoke's socket assertion.

**If Phase 3 passes with STATUS_COMPLETE instead of STATUS_FAILED:** The `health_check_timeout_secs` param is not being read by the executor. Double-check Task 1 Step 4 — confirm `parameters.get("health_check_timeout_secs")` is being read before the env var.

**If smoke can't find `NEXPLANE_OPS_TOKEN`:** The ops token may be stored in the platform DB. Use the login endpoint pattern from `nexplane/backend/tests/smoke/smoke_helpers.py`:
```python
resp = httpx.post(f"{NEXPLANE_OPS_URL}/api/v1/auth/login",
    json={"email": "admin@acme.example", "password": "admin123"})
token = resp.json()["access_token"]
```

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-deploy
git add smoke/test_onboarding_wizard_smoke.py
git commit -m "test(smoke): onboarding wizard FILO rollback — phases 1/2/3"
```

Then push to the backend nexplane repo from EC2:
```bash
# On EC2
cd ~/nexplane-deploy && git push
```

---

## Verification Checklist

After both tasks are complete and the smoke passes:

- [ ] `python -m pytest tests/test_provision_instance.py -v` — all pass (including 5 new §2 tests)
- [ ] `NEXPLANE_SMOKE=1 python -m pytest smoke/test_onboarding_wizard_smoke.py -v -s` — all 3 phases pass live
- [ ] Phase 3 assertion: CR status is `STATE_FAILED` (not `STATE_COMPLETE`) when `health_check_timeout_secs=5`
- [ ] Phase 2 assertion: EC2 instance confirmed terminated by AWS `describe_instances`
- [ ] Phase 1 assertion: port 443 confirmed reachable via TCP connect after wizard completes
