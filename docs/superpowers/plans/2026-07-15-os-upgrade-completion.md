# OS Upgrade Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual-instructions stub in `os_upgrade.py` with a fully automated EBS volume-swap rollback, add a `snapshot_only` parameter, and deliver a live smoke test that proves the mechanism works end-to-end.

**Architecture:** Extend one existing executor file (`os_upgrade.py`) — restructure `_take_snapshot()` to return a metadata dict, add `snapshot_only` early-return path in `execute()`, implement `_restore_snapshot()` with full boto3 EBS stop/detach/create-from-snap/attach/start sequence, and wire `rollback()` to call it. One new smoke test file with two phases: dry-run preflight and snapshot-then-EBS-rollback proof.

**Tech Stack:** Python async, boto3 EC2 waiters in `run_in_executor`, nexplane agent `dispatch_agent_job`, pytest smoke harness, AWS SSM for sentinel verification.

## Global Constraints

- SPDX header `# SPDX-License-Identifier: AGPL-3.0-only` + copyright line on every modified/created file
- NEVER use `from __future__ import annotations` — line 8 of `os_upgrade.py` MUST be removed
- Use `asyncio.get_event_loop().run_in_executor(None, lambda: ...)` for all synchronous boto3 waiter calls — NEVER `asyncio.get_running_loop()`
- `ROLLBACK_CAPABILITY = "full"` already declared on line 16 — do not change
- All smoke phases follow full CR lifecycle: create → plan → submit-for-approval → approve → execute → rollback
- Smoke skips with `pytest.skip()` when required creds absent from platform DB — never fails due to missing config
- Do NOT delete the old root volume or the snapshot after rollback — tag them `nexplane-rollback-orphan: true` for operator cleanup only
- Live smoke runs on EC2: `docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_os_upgrade_smoke.py -v -s`

---

### Task 1: Extend `os_upgrade.py` — snapshot metadata, `snapshot_only` param, automated rollback

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/os_upgrade.py`
- Create: `backend/tests/unit/test_os_upgrade_rollback.py`

**Interfaces:**
- `_take_snapshot(asset_id, instance_id, connector) -> dict` — returns `{"snapshot_id", "root_volume_id", "root_device_name", "availability_zone", "region", "instance_id"}`
- `_restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector) -> dict` — new signature with `snapshot_meta` dict parameter; fully automated EBS swap
- `execute()` new parameter: `snapshot_only` (bool, default False) — takes snapshot, skips preflight and upgrade, returns immediately
- `execute()` result now always includes `snapshot_meta` key alongside `snapshot_id`
- `rollback()` returns `{"rolled_back": bool, "snapshot_id": str, "new_volume_id": str, "old_volume_id": str, "agent_recovered": bool}`

- [ ] **Step 1: Remove the forbidden import**

Open `backend/app/connectors/executors/nexplane_agent/os_upgrade.py`.
Delete line 8: `from __future__ import annotations`

The file top should now read:
```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OS major version upgrade executor.
Flow: preflight -> snapshot -> upgrade -> verify -> (auto-rollback on failure).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
```

- [ ] **Step 2: Write the failing test for `rollback()` — no snapshot**

File: `backend/tests/unit/test_os_upgrade_rollback.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


SNAP_META = {
    "snapshot_id": "snap-abc123",
    "root_volume_id": "vol-old",
    "root_device_name": "/dev/xvda",
    "availability_zone": "us-east-1a",
    "region": "us-east-1",
    "instance_id": "i-test123",
}


@pytest.mark.asyncio
async def test_rollback_no_snapshot():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback
    result = await rollback({}, {}, None)
    assert result["rolled_back"] is False
    assert result["reason"] == "no_snapshot_available"


@pytest.mark.asyncio
async def test_rollback_no_snapshot_meta_falls_back_to_manual():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback
    result = await rollback({}, {"snapshot_id": "snap-abc"}, None)
    assert result["rolled_back"] is False
    assert "manual_steps" in result


@pytest.mark.asyncio
async def test_rollback_calls_restore_snapshot():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback

    mock_restore = AsyncMock(return_value={
        "restored": True,
        "new_volume_id": "vol-new",
        "old_volume_id": "vol-old",
        "agent_recovered": True,
    })
    with patch(
        "app.connectors.executors.nexplane_agent.os_upgrade._restore_snapshot",
        mock_restore,
    ):
        result = await rollback(
            {"asset_ids": ["asset-1"]},
            {"snapshot_id": "snap-abc123", "snapshot_meta": SNAP_META, "asset_id": "asset-1"},
            MagicMock(),
        )
    assert result["rolled_back"] is True
    assert result["new_volume_id"] == "vol-new"
    assert result["agent_recovered"] is True
    mock_restore.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_restore_exception_returns_false():
    from app.connectors.executors.nexplane_agent.os_upgrade import rollback

    mock_restore = AsyncMock(side_effect=RuntimeError("EC2 API error"))
    with patch(
        "app.connectors.executors.nexplane_agent.os_upgrade._restore_snapshot",
        mock_restore,
    ):
        result = await rollback(
            {},
            {"snapshot_id": "snap-abc123", "snapshot_meta": SNAP_META, "asset_id": "asset-1"},
            MagicMock(),
        )
    assert result["rolled_back"] is False
    assert "EC2 API error" in result["reason"]


@pytest.mark.asyncio
async def test_snapshot_only_parameter():
    from app.connectors.executors.nexplane_agent.os_upgrade import execute

    mock_snap = AsyncMock(return_value=SNAP_META)
    mock_asset = MagicMock()
    mock_asset.asset_metadata = {"instance_id": "i-test123"}

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=mock_asset)

    with patch(
        "app.connectors.executors.nexplane_agent.os_upgrade._take_snapshot", mock_snap
    ), patch(
        "app.connectors.executors.nexplane_agent.os_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ):
        result = await execute(
            {"snapshot_only": True},
            ["asset-1"],
            MagicMock(credentials={"access_key_id": "k", "secret_access_key": "s"}),
        )
    assert result["status"] == "snapshot_only"
    assert result["snapshot_id"] == "snap-abc123"
    assert result["snapshot_meta"] == SNAP_META
```

- [ ] **Step 3: Run the tests to confirm they fail**

```
docker exec nexplane-backend-1 python -m pytest /app/tests/unit/test_os_upgrade_rollback.py -v
```
Expected: 5 FAILED (functions don't yet match expected signatures/behavior)

- [ ] **Step 4: Replace `_take_snapshot()` to return a metadata dict**

In `os_upgrade.py`, replace the entire `_take_snapshot` function (lines 160–196 in the original):

```python
async def _take_snapshot(asset_id: str, instance_id: str, connector) -> dict:
    """Take pre-upgrade EBS snapshot. Returns snapshot metadata dict."""
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("access_key_id"):
        raise RuntimeError("No cloud credentials available for snapshot")

    import boto3
    region = creds.get("region", "us-east-1")
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )

    reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    if not reservations:
        raise RuntimeError(f"Instance {instance_id} not found")

    instance_data = reservations[0]["Instances"][0]
    az = instance_data["Placement"]["AvailabilityZone"]
    bdm = instance_data.get("BlockDeviceMappings", [])
    root_entry = next(
        (b for b in bdm if b.get("DeviceName") in ("/dev/xvda", "/dev/sda1", "/dev/nvme0n1p1")),
        None,
    )
    if not root_entry:
        raise RuntimeError("Could not identify root volume")
    root_vol_id = root_entry["Ebs"]["VolumeId"]
    root_device_name = root_entry["DeviceName"]

    snap = ec2.create_snapshot(
        VolumeId=root_vol_id,
        Description=f"Pre-OS-upgrade snapshot for {asset_id} at {datetime.now(timezone.utc).isoformat()}",
        TagSpecifications=[{"ResourceType": "snapshot", "Tags": [
            {"Key": "nexplane-purpose", "Value": "pre-os-upgrade"},
            {"Key": "nexplane-asset-id", "Value": asset_id},
        ]}],
    )
    return {
        "snapshot_id": snap["SnapshotId"],
        "root_volume_id": root_vol_id,
        "root_device_name": root_device_name,
        "availability_zone": az,
        "region": region,
        "instance_id": instance_id,
    }
```

- [ ] **Step 5: Update `execute()` to use the dict return and add `snapshot_only` path**

In `execute()`, add `snapshot_only` extraction after the `target_version` line:

```python
    snapshot_only = bool(parameters.get("snapshot_only", False))
```

Replace the snapshot block (the `if not skip_snapshot:` block, lines 60–79 in original) with:

```python
    snapshot_meta = None
    snapshot_id = None
    if not skip_snapshot:
        logger.info(f"Taking pre-upgrade snapshot for {asset_id}")
        try:
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                cloud_instance_id = (asset.asset_metadata or {}).get("instance_id") if asset else None

            if cloud_instance_id:
                snapshot_meta = await _take_snapshot(asset_id, cloud_instance_id, connector)
                snapshot_id = snapshot_meta["snapshot_id"]
                logger.info(f"Snapshot created: {snapshot_id}")
        except Exception as e:
            logger.warning(f"Snapshot failed (proceeding without): {e}")
            cloud_meta = {}
            async with AsyncSessionLocal() as db:
                asset = await db.get(Asset, uuid.UUID(asset_id))
                if asset:
                    cloud_meta = asset.asset_metadata or {}
            if cloud_meta.get("environment") == "prod" and not skip_snapshot:
                raise RuntimeError(f"Pre-upgrade snapshot failed on prod asset: {e}. Set skip_snapshot=true to override.")

    if snapshot_only:
        return {
            "status": "snapshot_only",
            "snapshot_id": snapshot_meta["snapshot_id"] if snapshot_meta else None,
            "snapshot_meta": snapshot_meta,
            "asset_id": asset_id,
        }
```

Update the `failed_and_rolled_back` auto-rollback calls inside `execute()` (two places — after upgrade failure and after verify failure) — change:
```python
restore_status = await _restore_snapshot(asset_id, snapshot_id, connector)
```
to:
```python
restore_status = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta or {}, connector)
```

And update both `failed_and_rolled_back` return dicts to include `snapshot_meta`:
```python
    return {
        "status": "failed_and_rolled_back",
        "error": str(e),          # or "verify_result": verify_result
        "snapshot_id": snapshot_id,
        "snapshot_meta": snapshot_meta,
        "rollback_status": restore_status,
    }
```

Update the final `completed` return to include `snapshot_meta`:
```python
    return {
        "status": "completed",
        "previous_os": preflight.get("current_os"),
        "new_os": verify_result.get("new_os_version"),
        "snapshot_id": snapshot_id,
        "snapshot_meta": snapshot_meta,
        "upgrade_result": upgrade_result,
        "verify_result": verify_result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 6: Implement `_restore_snapshot()` with full EBS volume swap**

Replace the existing stub (lines 199–212 in original):

```python
async def _restore_snapshot(
    asset_id: str,
    snapshot_id: str,
    snapshot_meta: dict,
    connector,
) -> dict:
    """Automated EBS root volume swap: stop → create-from-snap → detach → attach → start → verify agent."""
    instance_id = snapshot_meta.get("instance_id")
    root_volume_id = snapshot_meta.get("root_volume_id")
    root_device_name = snapshot_meta.get("root_device_name", "/dev/xvda")
    az = snapshot_meta.get("availability_zone")
    region = snapshot_meta.get("region", "us-east-1")

    if not instance_id or not root_volume_id or not az:
        raise RuntimeError(f"Incomplete snapshot_meta for restore: {snapshot_meta}")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("access_key_id"):
        raise RuntimeError("No cloud credentials available for EBS restore")

    import boto3
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )
    loop = asyncio.get_event_loop()

    # 1. Stop instance
    logger.info(f"Stopping instance {instance_id} for EBS restore")
    ec2.stop_instances(InstanceIds=[instance_id])
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_stopped").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 15, "MaxAttempts": 20},
        ),
    )
    logger.info(f"Instance {instance_id} stopped")

    # 2. Create new volume from snapshot in same AZ
    logger.info(f"Creating volume from snapshot {snapshot_id} in {az}")
    new_vol = ec2.create_volume(
        SnapshotId=snapshot_id,
        AvailabilityZone=az,
        VolumeType="gp3",
        TagSpecifications=[{"ResourceType": "volume", "Tags": [
            {"Key": "nexplane-purpose", "Value": "os-upgrade-restore"},
            {"Key": "nexplane-asset-id", "Value": asset_id},
        ]}],
    )
    new_vol_id = new_vol["VolumeId"]
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("volume_available").wait(
            VolumeIds=[new_vol_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
    )
    logger.info(f"New volume {new_vol_id} ready")

    # 3. Detach current root volume
    logger.info(f"Detaching current root volume {root_volume_id}")
    try:
        ec2.detach_volume(VolumeId=root_volume_id, InstanceId=instance_id, Force=False)
        await loop.run_in_executor(
            None,
            lambda: ec2.get_waiter("volume_available").wait(
                VolumeIds=[root_volume_id],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            ),
        )
    except Exception as e:
        logger.warning(f"Detach of {root_volume_id} raised: {e} — proceeding")

    # Tag old volume for operator cleanup — do NOT delete
    try:
        ec2.create_tags(
            Resources=[root_volume_id],
            Tags=[
                {"Key": "nexplane-rollback-orphan", "Value": "true"},
                {"Key": "nexplane-asset-id", "Value": asset_id},
            ],
        )
    except Exception:
        pass

    # 4. Attach new volume as root
    logger.info(f"Attaching {new_vol_id} as {root_device_name} on {instance_id}")
    ec2.attach_volume(VolumeId=new_vol_id, InstanceId=instance_id, Device=root_device_name)
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("volume_in_use").wait(
            VolumeIds=[new_vol_id],
            WaiterConfig={"Delay": 5, "MaxAttempts": 24},
        ),
    )

    # 5. Start instance
    logger.info(f"Starting instance {instance_id}")
    ec2.start_instances(InstanceIds=[instance_id])
    await loop.run_in_executor(
        None,
        lambda: ec2.get_waiter("instance_running").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 10, "MaxAttempts": 30},
        ),
    )
    logger.info(f"Instance {instance_id} running — waiting for agent heartbeat")

    # 6. Poll agent heartbeat until online or 300s timeout
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    agent_recovered = False
    deadline = asyncio.get_event_loop().time() + 300
    while asyncio.get_event_loop().time() < deadline:
        try:
            await dispatch_agent_job(
                command="health_check",
                parameters={},
                asset_ids=[asset_id],
                timeout_seconds=20,
            )
            agent_recovered = True
            logger.info(f"Agent on {asset_id} recovered after EBS restore")
            break
        except Exception:
            await asyncio.sleep(30)

    # Tag snapshot as used
    try:
        ec2.create_tags(
            Resources=[snapshot_id],
            Tags=[{"Key": "nexplane-rollback-used", "Value": "true"}],
        )
    except Exception:
        pass

    return {
        "restored": agent_recovered,
        "new_volume_id": new_vol_id,
        "old_volume_id": root_volume_id,
        "instance_id": instance_id,
        "agent_recovered": agent_recovered,
    }
```

- [ ] **Step 7: Implement `rollback()`**

Replace the existing stub (lines 215–230 in original):

```python
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id")
    snapshot_meta = execution_result.get("snapshot_meta") or {}
    asset_id = execution_result.get("asset_id") or (
        str(parameters.get("asset_ids", [None])[0]) if parameters.get("asset_ids") else None
    )

    if not snapshot_id:
        return {"rolled_back": False, "reason": "no_snapshot_available"}

    if not snapshot_meta.get("instance_id"):
        return {
            "rolled_back": False,
            "reason": "no_snapshot_meta — CR predates automated rollback; restore manually",
            "snapshot_id": snapshot_id,
            "manual_steps": [
                "1. Stop the EC2 instance",
                f"2. aws ec2 create-volume --snapshot-id {snapshot_id} --availability-zone <az>",
                "3. Detach current root volume",
                "4. Attach new volume as root device",
                "5. Start instance",
            ],
        }

    try:
        result = await _restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector)
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc), "snapshot_id": snapshot_id}

    return {
        "rolled_back": result["restored"],
        "snapshot_id": snapshot_id,
        "new_volume_id": result.get("new_volume_id"),
        "old_volume_id": result.get("old_volume_id"),
        "agent_recovered": result.get("agent_recovered"),
    }
```

- [ ] **Step 8: Run unit tests**

```
docker exec nexplane-backend-1 python -m pytest /app/tests/unit/test_os_upgrade_rollback.py -v
```
Expected: 5 passed

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/os_upgrade.py \
        backend/tests/unit/test_os_upgrade_rollback.py
git commit -m "feat(os-upgrade): automated EBS rollback (stop/swap/start/verify), snapshot_only param, remove future import"
```

---

### Task 2: Smoke test — `test_os_upgrade_smoke.py`

**Files:**
- Create: `backend/tests/smoke/test_os_upgrade_smoke.py`

**Interfaces:**
- Consumes: `NexplaneClient`, `log`, `get_connector_creds_from_db` from `smoke_helpers`
- AWS creds fields required: `smoke_instance_id` (EC2 instance with nexplane agent running), `smoke_asset_id` (asset UUID in platform DB), `access_key_id`, `secret_access_key`, `region`
- SSM `ssm:SendCommand` permission required on the test instance role to write/verify the sentinel file

- [ ] **Step 1: Write the smoke test**

File: `backend/tests/smoke/test_os_upgrade_smoke.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import sys
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

PHASE = "OS_UPGRADE"
TIMEOUT = 600  # snapshot + volume swap can take several minutes


def _run_cr(client, label, action_id, params, asset_ids=None, timeout=TIMEOUT):
    base = client.base
    body = {
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": "nexplane_agent",
            "action_id": action_id,
            "params": params,
        },
    }
    if asset_ids:
        body["asset_ids"] = asset_ids
    resp = client.client.post(f"{base}/change-requests", json=body)
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]
    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")
    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /approve failed {r.status_code}: {r.text}")
    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /execute failed {r.status_code}: {r.text}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "completed":
            log(label)
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(
                f"[{label}] CR {cr_id} status={status!r}: {str(cr.get('execution_runs', ''))[:400]}"
            )
        time.sleep(15)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client, cr_id, label, timeout=TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /rollback failed {r.status_code}: {r.text}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"rolled back: {label}")
            return cr
        if status in ("rollback_failed", "failed"):
            raise AssertionError(f"[{label}] rollback status={status!r}")
        time.sleep(15)
    raise TimeoutError(f"[{label}] rollback timeout after {timeout}s")


def _step_result(cr, rollback=False):
    for run in cr.get("execution_runs", []):
        is_rb = "rollback" in run.get("workflow_id", "")
        if is_rb != rollback:
            continue
        result = run.get("result") or {}
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
            if "rolled_back" in result:
                return result
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _ssm_run(instance_id, command, creds, timeout=60):
    """Run a shell command on the instance via SSM and return stdout."""
    import boto3
    ssm = boto3.client(
        "ssm",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        result = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        if result["Status"] in ("Success", "Failed", "Cancelled"):
            return result.get("StandardOutputContent", "").strip()
    raise TimeoutError(f"SSM command timed out after {timeout}s")


class TestOsUpgradeSmoke:

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("aws")
        if self.creds is None:
            pytest.skip("No AWS credentials in platform DB")
        self.instance_id = self.creds.get("smoke_instance_id")
        self.asset_id = self.creds.get("smoke_asset_id")

    def test_os_upgrade_dry_run(self):
        """Phase OS_UPGRADE_DRY — preflight only, no snapshot, no state change."""
        if not self.asset_id:
            pytest.skip("No smoke_asset_id in AWS creds")
        cr = _run_cr(
            self.client,
            "[smoke] os_upgrade dry_run",
            "execute_os_upgrade",
            {"dry_run": True, "target_version": ""},
            asset_ids=[self.asset_id],
        )
        result = _step_result(cr)
        assert result.get("status") == "dry_run", f"Expected dry_run status, got: {result}"
        assert "current_os" in result or "target_os" in result, (
            f"Expected OS info in dry_run result, got: {result}"
        )
        log(f"{PHASE}: DRY_RUN passed — current_os={result.get('current_os')}")

    def test_os_upgrade_snapshot_and_rollback(self):
        """Phase OS_UPGRADE_ROLLBACK — snapshot_only CR, write sentinel, rollback, verify sentinel gone."""
        if not self.instance_id or not self.asset_id:
            pytest.skip("No smoke_instance_id or smoke_asset_id in AWS creds — cannot run rollback smoke")

        # Step 1: Execute with snapshot_only=True — takes snapshot, returns immediately
        cr = _run_cr(
            self.client,
            "[smoke] os_upgrade snapshot_only",
            "execute_os_upgrade",
            {"snapshot_only": True},
            asset_ids=[self.asset_id],
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("status") == "snapshot_only", f"Expected snapshot_only status, got: {result}"
        assert result.get("snapshot_id"), f"Expected snapshot_id in result, got: {result}"
        assert result.get("snapshot_meta", {}).get("instance_id"), (
            f"Expected snapshot_meta.instance_id in result, got: {result}"
        )
        log(f"{PHASE}: snapshot taken — snap={result['snapshot_id']}")

        # Step 2: Write sentinel file via SSM (post-snapshot — should disappear after rollback)
        sentinel_value = f"nexplane-smoke-sentinel-{cr_id[:8]}"
        _ssm_run(
            self.instance_id,
            f"echo {sentinel_value} > /tmp/nexplane-smoke-sentinel",
            self.creds,
        )
        verify = _ssm_run(self.instance_id, "cat /tmp/nexplane-smoke-sentinel", self.creds)
        assert sentinel_value in verify, f"Sentinel write failed: {verify}"
        log(f"{PHASE}: sentinel written post-snapshot")

        # Step 3: Rollback — triggers full EBS volume swap, instance restarts
        cr = _rollback_cr(self.client, cr_id, "rollback os_upgrade snapshot", timeout=TIMEOUT)
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        assert rb_result.get("agent_recovered") is True, (
            f"Expected agent_recovered=True after EBS restore, got: {rb_result}"
        )
        log(f"{PHASE}: rollback completed — new_vol={rb_result.get('new_volume_id')}")

        # Step 4: Verify sentinel gone — volume was restored from pre-sentinel snapshot
        sentinel_after = _ssm_run(
            self.instance_id,
            "cat /tmp/nexplane-smoke-sentinel 2>/dev/null || echo MISSING",
            self.creds,
        )
        assert sentinel_value not in sentinel_after, (
            f"Sentinel still present after rollback — EBS restore did not work: {sentinel_after!r}"
        )
        log(f"{PHASE}: SNAPSHOT_AND_ROLLBACK passed — sentinel gone after EBS restore")
```

- [ ] **Step 2: Run the smoke test (dry_run phase only — no live infra needed)**

```
docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_os_upgrade_smoke.py::TestOsUpgradeSmoke::test_os_upgrade_dry_run -v -s
```
Expected: 1 passed (or 1 skipped if no AWS creds configured)

- [ ] **Step 3: Run the full smoke suite**

```
docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_os_upgrade_smoke.py -v -s
```
Expected: 2 passed, or 1 passed + 1 skipped if `smoke_instance_id` not configured

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_os_upgrade_smoke.py
git commit -m "smoke(OS_UPGRADE): dry_run preflight + snapshot_only/rollback EBS restore verification"
```
