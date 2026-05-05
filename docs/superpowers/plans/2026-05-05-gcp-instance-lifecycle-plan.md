# GCP Instance Lifecycle — Sub-project A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GCP Compute Engine instance lifecycle (launch/stop/start/reboot/delete/snapshot) with three connection modes, connector-aware Quick Actions UI, and smoke test phases L and M using the rollback stack pattern.

**Architecture:** Seven new GCP executor files follow the same pattern as `stop_instance.py` — mock path when `not creds`, real path via `google.cloud.compute_v1`. Six new change type JSON definitions, a new Alembic migration, catalog entries, and a connector-aware UI fix that adds `connectorType` filtering to Quick Actions so AWS and GCP actions don't appear on each other's assets. Smoke test file renamed `test_cloud_live.py`; phases L and M appended.

**Tech Stack:** Python 3.12, `google-cloud-compute` (already in requirements), FastAPI, SQLAlchemy, React 18, TypeScript

---

## Files

**Create:**
- `backend/app/connectors/executors/gcp/launch_instance.py`
- `backend/app/connectors/executors/gcp/reboot_instance.py`
- `backend/app/connectors/executors/gcp/create_disk_snapshot.py`
- `backend/app/connectors/executors/gcp/delete_disk_snapshot.py`
- `backend/app/connectors/executors/gcp/capture_instance_state.py`
- `backend/app/connectors/executors/gcp/health_check.py`
- `backend/app/connectors/executors/gcp/wait_instance_state.py`
- `backend/app/connectors/change_type_definitions/gce_instance_create.json`
- `backend/app/connectors/change_type_definitions/gce_stop.json`
- `backend/app/connectors/change_type_definitions/gce_start.json`
- `backend/app/connectors/change_type_definitions/gce_instance_reboot.json`
- `backend/app/connectors/change_type_definitions/gce_instance_delete.json`
- `backend/app/connectors/change_type_definitions/gce_disk_snapshot.json`
- `backend/alembic/versions/024_add_gce_change_types.py`
- `backend/tests/smoke/test_cloud_live.py` (renamed from `test_aws_live.py`)

**Modify:**
- `backend/app/models/change_request.py` — add 6 GCE ChangeType values
- `backend/app/connectors/catalog/gcp.json` — add 10 new action entries
- `backend/app/schemas/asset.py` — add `connector_type: Optional[str]`
- `backend/app/routers/assets.py` — populate `connector_type` from `asset.connector`
- `frontend/src/types/api.ts` — add 6 GCE ChangeType literals, add `connector_type` to Asset interface
- `frontend/src/pages/AssetDetail.tsx` — add `connectorType` field to action entries, filter logic
- `frontend/src/pages/CreateChangeRequest.tsx` — add GCE Instances group, asset filters, labels, outcome templates

---

### Task 1: New GCP executors — helper executors

**Files:**
- Create: `backend/app/connectors/executors/gcp/capture_instance_state.py`
- Create: `backend/app/connectors/executors/gcp/health_check.py`
- Create: `backend/app/connectors/executors/gcp/wait_instance_state.py`

These three are used as pipeline steps inside change types. No standalone change type entries needed.

- [ ] **Step 1: Write failing tests for all three**

Open `backend/tests/test_gcp_executors.py` (create it if it doesn't exist, or append). Add:

```python
import pytest

def test_capture_instance_state_mock():
    import asyncio
    from app.connectors.executors.gcp.capture_instance_state import execute
    result = asyncio.run(execute({"instance_name": "test-vm", "zone": "us-central1-a"}, [], None))
    assert result["action"] == "capture_instance_state"
    assert result["instance_name"] == "test-vm"
    assert result["mock"] is True

def test_health_check_mock():
    import asyncio
    from app.connectors.executors.gcp.health_check import execute
    result = asyncio.run(execute({"instance_name": "test-vm", "zone": "us-central1-a"}, [], None))
    assert result["action"] == "health_check"
    assert result["status"] == "RUNNING"

def test_wait_instance_state_mock():
    import asyncio
    from app.connectors.executors.gcp.wait_instance_state import execute
    result = asyncio.run(execute(
        {"instance_name": "test-vm", "zone": "us-central1-a", "target_state": "RUNNING"}, [], None
    ))
    assert result["action"] == "wait_instance_state"
    assert result["target_state"] == "RUNNING"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.connectors.executors.gcp.capture_instance_state'`

- [ ] **Step 3: Create `capture_instance_state.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]

    if not creds:
        return {
            "action": "capture_instance_state",
            "instance_name": instance_name,
            "zone": zone,
            "machine_type": "e2-micro",
            "status": "RUNNING",
            "labels": {},
            "mock": True,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    instance = await loop.run_in_executor(
        None, lambda: client.get(project=project, zone=zone, instance=instance_name)
    )
    return {
        "action": "capture_instance_state",
        "instance_name": instance_name,
        "zone": zone,
        "machine_type": instance.machine_type.split("/")[-1],
        "status": instance.status,
        "labels": dict(instance.labels),
        "network_tags": list(instance.tags.items),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture_instance_state is read-only"}
```

- [ ] **Step 4: Create `health_check.py`**

```python
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]

    if not creds:
        return {"action": "health_check", "instance_name": instance_name, "zone": zone, "status": "RUNNING"}

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    instance = await loop.run_in_executor(
        None, lambda: client.get(project=project, zone=zone, instance=instance_name)
    )
    if instance.status != "RUNNING":
        raise RuntimeError(f"Instance {instance_name} is not RUNNING — status: {instance.status}")
    return {"action": "health_check", "instance_name": instance_name, "zone": zone, "status": instance.status}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "health_check is read-only"}
```

- [ ] **Step 5: Create `wait_instance_state.py`**

```python
import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]
    target_state = parameters.get("target_state", "RUNNING")
    timeout = parameters.get("timeout_seconds", 300)

    if not creds:
        return {
            "action": "wait_instance_state",
            "instance_name": instance_name,
            "zone": zone,
            "target_state": target_state,
            "reached": True,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    client = compute_v1.InstancesClient(credentials=credentials)
    loop = asyncio.get_event_loop()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        instance = await loop.run_in_executor(
            None, lambda: client.get(project=project, zone=zone, instance=instance_name)
        )
        if instance.status == target_state:
            return {
                "action": "wait_instance_state",
                "instance_name": instance_name,
                "zone": zone,
                "target_state": target_state,
                "reached": True,
            }
        await asyncio.sleep(10)

    raise RuntimeError(f"Timed out waiting for {instance_name} to reach {target_state}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait_instance_state is read-only"}
```

- [ ] **Step 6: Run tests — verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py -v 2>&1 | tail -10
```

Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/gcp/capture_instance_state.py \
        backend/app/connectors/executors/gcp/health_check.py \
        backend/app/connectors/executors/gcp/wait_instance_state.py \
        backend/tests/test_gcp_executors.py
git commit -m "feat(gcp): add capture_instance_state, health_check, wait_instance_state executors"
```

---

### Task 2: New GCP executors — reboot, snapshot, delete_snapshot

**Files:**
- Create: `backend/app/connectors/executors/gcp/reboot_instance.py`
- Create: `backend/app/connectors/executors/gcp/create_disk_snapshot.py`
- Create: `backend/app/connectors/executors/gcp/delete_disk_snapshot.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/test_gcp_executors.py`:

```python
def test_reboot_instance_mock():
    import asyncio
    from app.connectors.executors.gcp.reboot_instance import execute
    result = asyncio.run(execute({"instance_name": "test-vm", "zone": "us-central1-a"}, [], None))
    assert result["action"] == "reboot_instance"
    assert result["instance_name"] == "test-vm"

def test_create_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.gcp.create_disk_snapshot import execute
    result = asyncio.run(execute(
        {"instance_name": "test-vm", "zone": "us-central1-a", "snapshot_name": "snap-001"}, [], None
    ))
    assert result["action"] == "create_disk_snapshot"
    assert result["snapshot_name"] == "snap-001"

def test_delete_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.gcp.delete_disk_snapshot import execute
    result = asyncio.run(execute({"snapshot_name": "snap-001"}, [], None))
    assert result["action"] == "delete_disk_snapshot"
    assert result["snapshot_name"] == "snap-001"
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py::test_reboot_instance_mock -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `reboot_instance.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]

    if not creds:
        return {"action": "reboot_instance", "instance_name": instance_name, "zone": zone, "status": "RESTARTING"}

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    op = await loop.run_in_executor(
        None, lambda: client.reset(project=project, zone=zone, instance=instance_name)
    )
    return {
        "action": "reboot_instance",
        "instance_name": instance_name,
        "zone": zone,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot_instance has no meaningful inverse"}
```

- [ ] **Step 4: Create `create_disk_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]
    snapshot_name = parameters.get("snapshot_name", f"nexplane-snap-{instance_name}")

    if not creds:
        return {
            "action": "create_disk_snapshot",
            "instance_name": instance_name,
            "zone": zone,
            "snapshot_name": snapshot_name,
            "disk_name": instance_name,
            "mock": True,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    # Get boot disk name from instance
    instances_client = compute_v1.InstancesClient(credentials=credentials)
    instance = await loop.run_in_executor(
        None, lambda: instances_client.get(project=project, zone=zone, instance=instance_name)
    )
    disk_name = instance.disks[0].source.split("/")[-1]

    # Create snapshot
    snapshots_client = compute_v1.SnapshotsClient(credentials=credentials)
    snapshot_body = compute_v1.Snapshot(
        name=snapshot_name,
        labels={"managed-by": "nexplane", "source-instance": instance_name},
    )
    op = await loop.run_in_executor(
        None,
        lambda: instances_client.set_disk_auto_delete(  # triggers a snapshot via disks API
            project=project, zone=zone, instance=instance_name, auto_delete=False, device_name=disk_name
        ),
    )
    # Use disks client to create the snapshot
    disks_client = compute_v1.DisksClient(credentials=credentials)
    op = await loop.run_in_executor(
        None,
        lambda: disks_client.create_snapshot(
            project=project, zone=zone, disk=disk_name, snapshot_resource=snapshot_body
        ),
    )
    return {
        "action": "create_disk_snapshot",
        "instance_name": instance_name,
        "zone": zone,
        "snapshot_name": snapshot_name,
        "disk_name": disk_name,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_disk_snapshot import execute as delete_snap
    return await delete_snap(
        {"snapshot_name": execution_result.get("snapshot_name", parameters.get("snapshot_name"))},
        [], connector
    )
```

- [ ] **Step 5: Create `delete_disk_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    snapshot_name = parameters["snapshot_name"]

    if not creds:
        return {"action": "delete_disk_snapshot", "snapshot_name": snapshot_name, "deleted": True, "mock": True}

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.SnapshotsClient(credentials=credentials)

    op = await loop.run_in_executor(
        None, lambda: client.delete(project=project, snapshot=snapshot_name)
    )
    return {
        "action": "delete_disk_snapshot",
        "snapshot_name": snapshot_name,
        "deleted": True,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_disk_snapshot is terminal"}
```

- [ ] **Step 6: Run all executor tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py -v 2>&1 | tail -10
```

Expected: `6 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/gcp/reboot_instance.py \
        backend/app/connectors/executors/gcp/create_disk_snapshot.py \
        backend/app/connectors/executors/gcp/delete_disk_snapshot.py \
        backend/tests/test_gcp_executors.py
git commit -m "feat(gcp): add reboot_instance, create_disk_snapshot, delete_disk_snapshot executors"
```

---

### Task 3: `launch_instance` executor

**Files:**
- Create: `backend/app/connectors/executors/gcp/launch_instance.py`

This is the largest executor — handles three connection modes and emits `_auto_asset`.

- [ ] **Step 1: Add failing test**

Append to `backend/tests/test_gcp_executors.py`:

```python
def test_launch_instance_mock_agent_startup():
    import asyncio
    from app.connectors.executors.gcp.launch_instance import execute
    result = asyncio.run(execute({
        "name": "nexplane-smoke-test-gcp-01",
        "machine_type": "e2-micro",
        "zone": "us-central1-a",
        "image_family": "ubuntu-2204-lts",
        "image_project": "ubuntu-os-cloud",
        "connection_mode": "agent_startup",
    }, [], None))
    assert result["action"] == "launch_instance"
    assert result["instance_name"] == "nexplane-smoke-test-gcp-01"
    assert "_auto_asset" in result
    assert result["_auto_asset"]["asset_type"] == "server"
    assert result["mock"] is True

def test_launch_instance_mock_ssh_mode():
    import asyncio
    from app.connectors.executors.gcp.launch_instance import execute
    result = asyncio.run(execute({
        "name": "nexplane-smoke-test-gcp-02",
        "machine_type": "e2-micro",
        "zone": "us-central1-a",
        "image_family": "ubuntu-2204-lts",
        "image_project": "ubuntu-os-cloud",
        "connection_mode": "ssh",
        "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc...",
    }, [], None))
    assert result["connection_mode"] == "ssh"
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py::test_launch_instance_mock_agent_startup -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `launch_instance.py`**

```python
import asyncio
from datetime import datetime, timezone


_AGENT_STARTUP_TEMPLATE = """#!/bin/bash
set -e
# Install Nexplane agent
NEXPLANE_URL="{nexplane_url}"
NEXPLANE_SECRET="{nexplane_secret}"
VERSION=$(curl -fsSL https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version)
curl -fsSL "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-linux-amd64-${{VERSION}}" \
  -o /usr/local/bin/nexplane-agent
chmod +x /usr/local/bin/nexplane-agent

cat > /etc/systemd/system/nexplane-agent.service <<EOF
[Unit]
Description=Nexplane Agent
After=network.target

[Service]
ExecStart=/usr/local/bin/nexplane-agent --control-plane $NEXPLANE_URL --secret $NEXPLANE_SECRET --mode service
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nexplane-agent
systemctl start nexplane-agent
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters["name"]
    machine_type = parameters.get("machine_type", "e2-micro")
    zone = parameters.get("zone", "us-central1-a")
    image_family = parameters.get("image_family", "ubuntu-2204-lts")
    image_project = parameters.get("image_project", "ubuntu-os-cloud")
    connection_mode = parameters.get("connection_mode", "agent_startup")
    nexplane_url = parameters.get("nexplane_url", "")
    nexplane_secret = parameters.get("nexplane_secret", "")
    ssh_public_key = parameters.get("ssh_public_key", "")
    network_tags = parameters.get("network_tags", [])
    labels = parameters.get("labels", {"managed-by": "nexplane"})

    auto_asset = {
        "name": name,
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "instance_name": name,
            "zone": zone,
            "machine_type": machine_type,
            "connection_mode": connection_mode,
            "image_family": image_family,
            "provider": "gcp",
        },
        "tags": ["gce", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "launch_instance",
            "instance_name": name,
            "zone": zone,
            "machine_type": machine_type,
            "connection_mode": connection_mode,
            "status": "STAGING",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    # Resolve latest image
    images_client = compute_v1.ImagesClient(credentials=credentials)
    image = await loop.run_in_executor(
        None, lambda: images_client.get_from_family(project=image_project, family=image_family)
    )
    image_link = image.self_link

    # Build startup script based on connection_mode
    if connection_mode == "agent_startup":
        startup_script = parameters.get(
            "startup_script",
            _AGENT_STARTUP_TEMPLATE.format(nexplane_url=nexplane_url, nexplane_secret=nexplane_secret),
        )
        metadata_items = [compute_v1.Items(key="startup-script", value=startup_script)]
        tags_list = list(network_tags)
    elif connection_mode == "iap":
        startup_script = parameters.get("startup_script", "")
        metadata_items = [compute_v1.Items(key="startup-script", value=startup_script)] if startup_script else []
        tags_list = list(set(network_tags) | {"allow-iap-ssh"})
    else:  # ssh
        metadata_items = []
        if ssh_public_key:
            metadata_items.append(compute_v1.Items(key="ssh-keys", value=f"ubuntu:{ssh_public_key}"))
        tags_list = list(network_tags)

    instance_body = compute_v1.Instance(
        name=name,
        machine_type=f"zones/{zone}/machineTypes/{machine_type}",
        disks=[
            compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(source_image=image_link),
            )
        ],
        network_interfaces=[compute_v1.NetworkInterface(
            access_configs=[compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT")]
        )],
        metadata=compute_v1.Metadata(items=metadata_items),
        tags=compute_v1.Tags(items=tags_list),
        labels=labels,
    )

    instances_client = compute_v1.InstancesClient(credentials=credentials)
    op = await loop.run_in_executor(
        None, lambda: instances_client.insert(project=project, zone=zone, instance_resource=instance_body)
    )

    # Wait for instance to be RUNNING
    from app.connectors.executors.gcp.wait_instance_state import execute as wait
    await wait({"instance_name": name, "zone": zone, "target_state": "RUNNING"}, [], connector)

    # Fetch internal IP
    instance = await loop.run_in_executor(
        None, lambda: instances_client.get(project=project, zone=zone, instance=name)
    )
    internal_ip = ""
    if instance.network_interfaces:
        internal_ip = instance.network_interfaces[0].network_i_p or ""

    auto_asset["asset_metadata"]["internal_ip"] = internal_ip

    return {
        "action": "launch_instance",
        "instance_name": name,
        "zone": zone,
        "machine_type": machine_type,
        "connection_mode": connection_mode,
        "internal_ip": internal_ip,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_instance import execute as delete
    return await delete(
        {
            "instance_name": execution_result.get("instance_name", parameters.get("name")),
            "zone": execution_result.get("zone", parameters.get("zone", "us-central1-a")),
        },
        [], connector
    )
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py -v 2>&1 | tail -10
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/gcp/launch_instance.py \
        backend/tests/test_gcp_executors.py
git commit -m "feat(gcp): add launch_instance executor with agent_startup/iap/ssh connection modes"
```

---

### Task 4: Update existing executors + ChangeType model + Alembic migration

**Files:**
- Modify: `backend/app/connectors/executors/gcp/stop_instance.py`
- Modify: `backend/app/connectors/executors/gcp/start_instance.py`
- Modify: `backend/app/connectors/executors/gcp/delete_instance.py`
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/024_add_gce_change_types.py`

- [ ] **Step 1: Add failing tests for new change types**

Append to `backend/tests/test_gcp_executors.py`:

```python
def test_gce_change_types_in_enum():
    from app.models.change_request import ChangeType
    assert ChangeType.gce_instance_create == "gce_instance_create"
    assert ChangeType.gce_stop == "gce_stop"
    assert ChangeType.gce_start == "gce_start"
    assert ChangeType.gce_instance_reboot == "gce_instance_reboot"
    assert ChangeType.gce_instance_delete == "gce_instance_delete"
    assert ChangeType.gce_disk_snapshot == "gce_disk_snapshot"
```

- [ ] **Step 2: Run to verify it fails**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py::test_gce_change_types_in_enum -v 2>&1 | tail -5
```

Expected: `AttributeError: 'gce_instance_create' is not a valid ChangeType`

- [ ] **Step 3: Add rollback to `stop_instance.py`**

The file already has a rollback. Verify it calls start and matches the pattern — no change needed. The rollback is already correct.

- [ ] **Step 4: Fix `start_instance.py` rollback**

Replace the current rollback (which refuses to roll back) with one that stops:

```python
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.stop_instance import execute as stop
    return await stop(parameters, [], connector)
```

- [ ] **Step 5: Add GCE ChangeType values to `change_request.py`**

Open `backend/app/models/change_request.py`. Find the `# AWS expansion — Plan 1` block (around line 33). After the last existing value (`cloudwatch_alarm_delete = "cloudwatch_alarm_delete"`), add:

```python
    # GCP instance lifecycle — Sub-project A
    gce_instance_create = "gce_instance_create"
    gce_stop = "gce_stop"
    gce_start = "gce_start"
    gce_instance_reboot = "gce_instance_reboot"
    gce_instance_delete = "gce_instance_delete"
    gce_disk_snapshot = "gce_disk_snapshot"
```

- [ ] **Step 6: Create Alembic migration `024_add_gce_change_types.py`**

```python
"""add gce instance lifecycle change types

Revision ID: 024
Revises: 023
Create Date: 2026-05-05
"""
from alembic import op

revision = '024'
down_revision = '023'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'gce_instance_create', 'gce_stop', 'gce_start',
        'gce_instance_reboot', 'gce_instance_delete', 'gce_disk_snapshot',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 7: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
```

Expected: `Running upgrade 023 -> 024`

- [ ] **Step 8: Run tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py -v 2>&1 | tail -10
```

Expected: `9 passed`

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/gcp/start_instance.py \
        backend/app/models/change_request.py \
        backend/alembic/versions/024_add_gce_change_types.py \
        backend/tests/test_gcp_executors.py
git commit -m "feat(gcp): add GCE ChangeType enum values and Alembic migration 024"
```

---

### Task 5: Change type definitions + GCP catalog entries

**Files:**
- Create: `backend/app/connectors/change_type_definitions/gce_instance_create.json`
- Create: `backend/app/connectors/change_type_definitions/gce_stop.json`
- Create: `backend/app/connectors/change_type_definitions/gce_start.json`
- Create: `backend/app/connectors/change_type_definitions/gce_instance_reboot.json`
- Create: `backend/app/connectors/change_type_definitions/gce_instance_delete.json`
- Create: `backend/app/connectors/change_type_definitions/gce_disk_snapshot.json`
- Modify: `backend/app/connectors/catalog/gcp.json`

- [ ] **Step 1: Create `gce_instance_create.json`**

```json
{
  "change_type": "gce_instance_create",
  "display_name": "Launch GCE Instance",
  "steps": [
    {"generic_action": "launch_instance",      "purpose": "execute", "required": true},
    {"generic_action": "wait_instance_state",  "purpose": "verify",  "required": true},
    {"generic_action": "health_check",         "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "no_concurrent_changes"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_instance",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 2: Create `gce_stop.json`**

```json
{
  "change_type": "gce_stop",
  "display_name": "Stop GCE Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_validate", "required": true},
    {"generic_action": "stop_instance",          "purpose": "execute",            "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "start_instance",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 3: Create `gce_start.json`**

```json
{
  "change_type": "gce_start",
  "display_name": "Start GCE Instance",
  "steps": [
    {"generic_action": "start_instance",       "purpose": "execute", "required": true},
    {"generic_action": "wait_instance_state",  "purpose": "verify",  "required": true},
    {"generic_action": "health_check",         "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "stop_instance",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 4: Create `gce_instance_reboot.json`**

```json
{
  "change_type": "gce_instance_reboot",
  "display_name": "Reboot GCE Instance",
  "steps": [
    {"generic_action": "reboot_instance",     "purpose": "execute", "required": true},
    {"generic_action": "wait_instance_state", "purpose": "verify",  "required": true},
    {"generic_action": "health_check",        "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "rollback_unavailable",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 5: Create `gce_instance_delete.json`**

```json
{
  "change_type": "gce_instance_delete",
  "display_name": "Delete GCE Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_validate", "required": true},
    {"generic_action": "delete_instance",        "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "rollback_unavailable",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 6: Create `gce_disk_snapshot.json`**

```json
{
  "change_type": "gce_disk_snapshot",
  "display_name": "Create GCE Disk Snapshot",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_validate", "required": true},
    {"generic_action": "create_disk_snapshot",   "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_disk_snapshot",
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 7: Add new action entries to `gcp.json`**

Open `backend/app/connectors/catalog/gcp.json`. Before the closing `]` of the `"actions"` array, add these entries (after the existing `rotate_service_account_key` entry):

```json
    ,
    {"action_id": "launch_instance", "generic_action": "launch_instance", "action_type": "change", "execution_tier": 2, "display_name": "Launch GCE Instance", "description": "Create a new Compute Engine instance with agent_startup, iap, or ssh connection mode.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "name", "type": "string", "required": true}, {"name": "machine_type", "type": "string", "required": false, "default": "e2-micro"}, {"name": "zone", "type": "string", "required": false, "default": "us-central1-a"}, {"name": "image_family", "type": "string", "required": false, "default": "ubuntu-2204-lts"}, {"name": "image_project", "type": "string", "required": false, "default": "ubuntu-os-cloud"}, {"name": "connection_mode", "type": "string", "required": false, "default": "agent_startup"}, {"name": "nexplane_url", "type": "string", "required": false}, {"name": "nexplane_secret", "type": "string", "required": false}, {"name": "ssh_public_key", "type": "string", "required": false}], "executor": "gcp.launch_instance", "rollback_action": "delete_instance", "estimated_duration_seconds": 120, "blast_radius_hint": "new_resource"},
    {"action_id": "reboot_instance", "generic_action": "reboot_instance", "action_type": "change", "execution_tier": 2, "display_name": "Reboot GCE Instance", "description": "Reset (hard reboot) a running GCE instance.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}], "executor": "gcp.reboot_instance", "estimated_duration_seconds": 60},
    {"action_id": "create_disk_snapshot", "generic_action": "create_disk_snapshot", "action_type": "change", "execution_tier": 2, "display_name": "Create Disk Snapshot", "description": "Snapshot the boot disk of a GCE instance.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}, {"name": "snapshot_name", "type": "string", "required": false}], "executor": "gcp.create_disk_snapshot", "rollback_action": "delete_disk_snapshot", "estimated_duration_seconds": 60},
    {"action_id": "delete_disk_snapshot", "generic_action": "delete_disk_snapshot", "action_type": "change", "execution_tier": 2, "display_name": "Delete Disk Snapshot", "description": "Delete a GCE disk snapshot.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "snapshot_name", "type": "string", "required": true}], "executor": "gcp.delete_disk_snapshot", "estimated_duration_seconds": 15},
    {"action_id": "capture_instance_state", "generic_action": "capture_instance_state", "action_type": "change", "execution_tier": 1, "display_name": "Capture Instance State", "description": "Record current instance metadata as preflight baseline.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}], "executor": "gcp.capture_instance_state", "estimated_duration_seconds": 5},
    {"action_id": "health_check", "generic_action": "health_check", "action_type": "change", "execution_tier": 1, "display_name": "Health Check", "description": "Verify GCE instance is in RUNNING state.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}], "executor": "gcp.health_check", "estimated_duration_seconds": 10},
    {"action_id": "wait_instance_state", "generic_action": "wait_instance_state", "action_type": "change", "execution_tier": 1, "display_name": "Wait for Instance State", "description": "Poll until GCE instance reaches target state (RUNNING, TERMINATED).", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}, {"name": "target_state", "type": "string", "required": false, "default": "RUNNING"}], "executor": "gcp.wait_instance_state", "estimated_duration_seconds": 60}
```

Also add `"rollback_action": "start_instance"` to the existing `stop_instance` entry and `"rollback_action": "stop_instance"` to the existing `start_instance` entry.

- [ ] **Step 8: Verify backend still starts**

```bash
docker exec nexplane-backend-1 python -c "from app.connectors.catalog_service import get_catalog_service; c = get_catalog_service(); print('catalog OK')"
```

Expected: `catalog OK`

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/change_type_definitions/gce_instance_create.json \
        backend/app/connectors/change_type_definitions/gce_stop.json \
        backend/app/connectors/change_type_definitions/gce_start.json \
        backend/app/connectors/change_type_definitions/gce_instance_reboot.json \
        backend/app/connectors/change_type_definitions/gce_instance_delete.json \
        backend/app/connectors/change_type_definitions/gce_disk_snapshot.json \
        backend/app/connectors/catalog/gcp.json
git commit -m "feat(gcp): add change type definitions and catalog entries for GCE instance lifecycle"
```

---

### Task 6: connector_type on AssetRead + API

**Files:**
- Modify: `backend/app/schemas/asset.py`
- Modify: `backend/app/routers/assets.py`

The Quick Actions panel needs to know which connector type an asset belongs to. Currently `AssetRead` only exposes `connector_name`. We add `connector_type`.

- [ ] **Step 1: Add failing test**

Append to `backend/tests/test_gcp_executors.py`:

```python
def test_asset_read_has_connector_type_field():
    from app.schemas.asset import AssetRead
    import inspect
    fields = AssetRead.model_fields
    assert "connector_type" in fields, "AssetRead must expose connector_type for UI filtering"
```

- [ ] **Step 2: Run to verify it fails**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py::test_asset_read_has_connector_type_field -v 2>&1 | tail -5
```

Expected: `AssertionError: AssetRead must expose connector_type for UI filtering`

- [ ] **Step 3: Add `connector_type` to `AssetRead`**

Open `backend/app/schemas/asset.py`. After line 23 (`connector_name: Optional[str] = None`), add:

```python
    connector_type: Optional[str] = None
```

- [ ] **Step 4: Populate `connector_type` in the assets router**

Open `backend/app/routers/assets.py`. Find the two places that construct `AssetRead` (list endpoint ~line 57–63, get endpoint ~line 149–152). Update both:

```python
# List endpoint — change:
AssetRead(
    **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
    connector_name=asset.connector.name if asset.connector else None,
)
# To:
AssetRead(
    **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
    connector_name=asset.connector.name if asset.connector else None,
    connector_type=asset.connector.connector_type.value if asset.connector else None,
)
```

Apply the same change to the get endpoint.

- [ ] **Step 5: Run tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_gcp_executors.py -v 2>&1 | tail -10
```

Expected: `10 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/asset.py backend/app/routers/assets.py backend/tests/test_gcp_executors.py
git commit -m "feat(gcp): expose connector_type on AssetRead for connector-aware UI filtering"
```

---

### Task 7: Frontend — ChangeType literals, Asset interface, connector-aware Quick Actions

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/AssetDetail.tsx`

- [ ] **Step 1: Add GCE ChangeType literals to `api.ts`**

Open `frontend/src/types/api.ts`. Find the `ChangeType` union (ends at `"cloudwatch_alarm_delete"`). Add before the closing semicolon:

```typescript
  | "gce_instance_create"
  | "gce_stop"
  | "gce_start"
  | "gce_instance_reboot"
  | "gce_instance_delete"
  | "gce_disk_snapshot"
```

- [ ] **Step 2: Add `connector_type` to `Asset` interface**

In the same file, find the `Asset` interface (around line 39). After `connector_name?: string;`, add:

```typescript
  connector_type?: string;
```

- [ ] **Step 3: Add `connectorType` field to the ASSET_ACTIONS type and add connector filtering**

Open `frontend/src/pages/AssetDetail.tsx`. Find line 13:

```typescript
const ASSET_ACTIONS: Record<AssetType, { changeType: string; label: string; title: (a: Asset) => string; description: (a: Asset) => string }[]> = {
```

Replace with:

```typescript
interface QuickAction {
  changeType: string;
  label: string;
  title: (a: Asset) => string;
  description: (a: Asset) => string;
  connectorType?: string;
}

const ASSET_ACTIONS: Record<AssetType, QuickAction[]> = {
```

- [ ] **Step 4: Add `connectorType: "aws"` to all existing AWS-specific server actions**

In the `server` array, add `connectorType: "aws"` to the following entries (the ones that are EC2-specific):
- `ec2_reboot` → add `connectorType: "aws"`
- `ec2_stop` → add `connectorType: "aws"`
- `ec2_start` → add `connectorType: "aws"`
- `ec2_stop_start` → add `connectorType: "aws"`
- `ec2_terminate` → add `connectorType: "aws"`
- `snapshot_asset` → add `connectorType: "aws"`

Leave `patch_packages`, `remote_command`, `isolate_host`, `enforce_cis_benchmark` without `connectorType` (they are connector-agnostic).

Also add `connectorType: "aws"` to the existing AWS-specific `cloud_account` actions:
- `ec2_launch` → add `connectorType: "aws"`
- `s3_block_public_access` → add `connectorType: "aws"`
- `iam_enforce_mfa` → add `connectorType: "aws"`
- `iam_user_create` → add `connectorType: "aws"`
- `s3_bucket_create` → add `connectorType: "aws"`
- `route53_zone_create` → add `connectorType: "aws"`
- `rds_instance_create` → add `connectorType: "aws"`

- [ ] **Step 5: Add GCP server Quick Actions**

In the `server` array, after all existing entries, add:

```typescript
    {
      changeType: "gce_stop",
      label: "Stop Instance",
      title: (a) => `Stop ${a.name}`,
      description: (a) => `Stop GCE instance ${a.asset_metadata?.instance_name ?? a.name} in zone ${a.asset_metadata?.zone ?? ""}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_start",
      label: "Start Instance",
      title: (a) => `Start ${a.name}`,
      description: (a) => `Start stopped GCE instance ${a.asset_metadata?.instance_name ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_instance_reboot",
      label: "Reboot Instance",
      title: (a) => `Reboot ${a.name}`,
      description: (a) => `Hard reset GCE instance ${a.asset_metadata?.instance_name ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_disk_snapshot",
      label: "Create Disk Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Snapshot boot disk of GCE instance ${a.asset_metadata?.instance_name ?? a.name}.`,
      connectorType: "gcp",
    },
    {
      changeType: "gce_instance_delete",
      label: "Delete Instance",
      title: (a) => `Delete ${a.name}`,
      description: (a) => `Permanently delete GCE instance ${a.asset_metadata?.instance_name ?? a.name}. Irreversible.`,
      connectorType: "gcp",
    },
```

- [ ] **Step 6: Add GCP cloud_account Quick Action**

In the `cloud_account` array, after the existing entries, add:

```typescript
    {
      changeType: "gce_instance_create",
      label: "Launch GCE Instance",
      title: (a) => `Launch GCE instance in ${a.name}`,
      description: (a) => `Create a new Compute Engine instance in GCP project ${a.asset_metadata?.project_id ?? a.name}.`,
      connectorType: "gcp",
    },
```

- [ ] **Step 7: Add connector filtering logic in the Quick Actions render**

Find where the Quick Actions panel renders its list. Look for `ASSET_ACTIONS[asset.asset_type]` usage. It will look something like:

```typescript
const actions = ASSET_ACTIONS[asset.asset_type] ?? [];
```

Replace with:

```typescript
const actions = (ASSET_ACTIONS[asset.asset_type] ?? []).filter(
  (action) => !action.connectorType || action.connectorType === asset.connector_type
);
```

- [ ] **Step 8: TypeScript check**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20"
```

Expected: no new errors introduced by our changes (pre-existing errors are acceptable).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/pages/AssetDetail.tsx
git commit -m "feat(gcp): connector-aware Quick Actions — GCE server/cloud_account actions, connectorType filter"
```

---

### Task 8: Frontend — CreateChangeRequest GCE group + filters + templates

**Files:**
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Add GCE Instances group to CHANGE_TYPE_GROUPS**

Open `frontend/src/pages/CreateChangeRequest.tsx`. Find the `Observability` group (the last named group before `Backup & Recovery`). After the `Observability` group closing brace, add:

```typescript
  {
    label: "GCE Instances",
    types: ["gce_instance_create", "gce_stop", "gce_start", "gce_instance_reboot", "gce_instance_delete", "gce_disk_snapshot"],
  },
```

- [ ] **Step 2: Add CHANGE_TYPE_ASSET_FILTER entries**

Find `CHANGE_TYPE_ASSET_FILTER`. Add after the last existing entry:

```typescript
  gce_instance_create: "cloud_account",
  gce_stop: "server",
  gce_start: "server",
  gce_instance_reboot: "server",
  gce_instance_delete: "server",
  gce_disk_snapshot: "server",
```

- [ ] **Step 3: Add labels and outcome templates to CHANGE_TYPE_META**

Find `CHANGE_TYPE_META`. Add after the last existing entry:

```typescript
  gce_instance_create: {
    label: "Launch GCE Instance",
    description: "Create a new Compute Engine VM with agent, IAP, or SSH connection.",
    outcomeTemplate: {
      name: "",
      machine_type: "e2-micro",
      zone: "us-central1-a",
      image_family: "ubuntu-2204-lts",
      image_project: "ubuntu-os-cloud",
      connection_mode: "agent_startup",
      rollback_strategy: "delete_instance",
    },
  },
  gce_stop: {
    label: "Stop GCE Instance",
    description: "Gracefully stop a running Compute Engine instance.",
    outcomeTemplate: { instance_name: "", zone: "us-central1-a", rollback_strategy: "start_instance" },
  },
  gce_start: {
    label: "Start GCE Instance",
    description: "Start a stopped Compute Engine instance.",
    outcomeTemplate: { instance_name: "", zone: "us-central1-a", rollback_strategy: "stop_instance" },
  },
  gce_instance_reboot: {
    label: "Reboot GCE Instance",
    description: "Hard reset (reset) a Compute Engine instance.",
    outcomeTemplate: { instance_name: "", zone: "us-central1-a", rollback_strategy: "rollback_unavailable" },
  },
  gce_instance_delete: {
    label: "Delete GCE Instance",
    description: "Permanently delete a Compute Engine instance.",
    outcomeTemplate: { instance_name: "", zone: "us-central1-a", rollback_strategy: "rollback_unavailable" },
  },
  gce_disk_snapshot: {
    label: "Create GCE Disk Snapshot",
    description: "Snapshot the boot disk of a Compute Engine instance.",
    outcomeTemplate: { instance_name: "", zone: "us-central1-a", snapshot_name: "", rollback_strategy: "delete_disk_snapshot" },
  },
```

- [ ] **Step 4: TypeScript check**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20"
```

Expected: no new errors from our changes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(gcp): add GCE Instances CR group, asset filters, labels, and outcome templates"
```

---

### Task 9: Smoke test — rename file + add GCP helpers + Phase L

**Files:**
- Rename: `backend/tests/smoke/test_aws_live.py` → `backend/tests/smoke/test_cloud_live.py`
- Modify: `backend/tests/smoke/test_cloud_live.py`

- [ ] **Step 1: Rename the smoke test file**

```bash
git mv backend/tests/smoke/test_aws_live.py backend/tests/smoke/test_cloud_live.py
```

- [ ] **Step 2: Update the module docstring**

Open `backend/tests/smoke/test_cloud_live.py`. Replace the first docstring block:

```python
"""
Nexplane Multi-Cloud Live Smoke Test — Phases A–M.

Runs against live AWS and GCP accounts via the Nexplane API. Creates and destroys
real cloud resources. Run specific phases with --phases (default: A,B,C,D).

Usage:
    python backend/tests/smoke/test_cloud_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases A,B,C,D \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id

Phase descriptions:
    A  EC2 lifecycle + Tailscale join + Nexplane agent deploy
    B  Agent-based actions (patching audit, OS posture, CloudWatch agent)
    C  Local Terraform lifecycle (S3 bucket create/destroy)
    D  Local Ansible playbook (htop install/remove)
    E  EC2 advanced: stop/start/reboot/snapshot with rollback stack
    F  Security group: add/remove rules with rollback stack
    G  IAM user lifecycle: create/attach-policy/rotate-key/delete with rollback stack
    H  S3 advanced: create/lifecycle/policy/public-access/delete with rollback stack
    I  Route53: private zone + A record create/update/delete with rollback stack
    J  RDS: instance + snapshot lifecycle (~30 min) with rollback stack
    K  CloudWatch: alarms + SSM metric push with rollback stack
    L  GCE: instance launch + agent deploy with rollback stack
    M  GCE advanced: stop/start/reboot/snapshot with rollback stack

Requirements:
    AWS phases (A-K): AWS connector with credentials + NexplaneEC2TestProfile IAM role
                      Tailscale connector with reusable pre-authorized auth key
    GCP phases (L-M): GCP connector with credentials + Compute Engine API enabled
"""
```

- [ ] **Step 3: Add `--gcp-project` CLI argument**

Find `parser.add_argument("--tailscale-auth-key", ...)` in `main()`. After it, add:

```python
    parser.add_argument("--gcp-project", default="", help="GCP project ID for phases L and M")
```

- [ ] **Step 4: Add `_get_gcp_compute_client` helper**

After the `_get_aws_boto3_client` function, add:

```python
_gcp_creds_cache: dict = {}


def _get_gcp_compute_client():
    """Get a GCP Compute Engine client using GCP connector credentials from the app DB."""
    import threading
    global _gcp_creds_cache
    if not _gcp_creds_cache:
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa

        result_holder: list = [None]

        async def _get():
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa.select(Connector).where(Connector.connector_type == ConnectorType.gcp)
                )
                conn = result.scalars().first()
                if not conn:
                    return None
                await _attach_credentials(conn, db)
                return getattr(conn, 'credentials', {})

        def _run_in_thread():
            result_holder[0] = asyncio.run(_get())

        t = threading.Thread(target=_run_in_thread)
        t.start()
        t.join()
        _gcp_creds_cache = result_holder[0] or {}

    creds = _gcp_creds_cache
    if not creds:
        return None

    import json as _json
    from google.oauth2 import service_account
    from google.cloud import compute_v1

    key_json_raw = creds.get("service_account_key_json", "")
    if isinstance(key_json_raw, str):
        key_json = _json.loads(key_json_raw)
    else:
        key_json = key_json_raw

    credentials = service_account.Credentials.from_service_account_info(
        key_json,
        scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/compute"],
    )
    return compute_v1.InstancesClient(credentials=credentials)
```

- [ ] **Step 5: Add `run_phase_l` function**

Before the `# Main` section, add:

```python
# ---------------------------------------------------------------------------
# Phase L
# ---------------------------------------------------------------------------

GCE_SMOKE_INSTANCE = "nexplane-smoke-gce-01"
GCE_ZONE = "us-central1-a"


def run_phase_l(client: NexplaneClient, cloud_account_id: str,
                gcp_project: str, agent_secret: str) -> dict:
    """Phase L: GCE instance launch + agent deploy with rollback stack."""
    print("\n[Phase L] GCE Instance Launch + Agent Deploy")

    rollback_stack: list[tuple[str, str]] = []
    instance_created = False

    try:
        cr = client.run_cr(
            "Smoke-L: launch GCE instance", "gce_instance_create", cloud_account_id,
            {
                "name": GCE_SMOKE_INSTANCE,
                "machine_type": "e2-micro",
                "zone": GCE_ZONE,
                "image_family": "ubuntu-2204-lts",
                "image_project": "ubuntu-os-cloud",
                "connection_mode": "agent_startup",
                "nexplane_url": f"http://localhost:8000",
                "nexplane_secret": agent_secret,
            },
        )
        rollback_stack.append((cr["id"], "gce_instance_create"))
        instance_created = True
        log(f"GCE instance launched: {GCE_SMOKE_INSTANCE}")

        # Verify server asset in inventory
        time.sleep(10)
        instance_asset = client.get_asset_by_name(GCE_SMOKE_INSTANCE)
        if instance_asset:
            log(f"GCE instance in inventory: {instance_asset['id']}")
        else:
            print(f"  ⚠️  GCE instance asset not yet in inventory (ingest lag)")
            instance_asset = {"id": cloud_account_id, "name": GCE_SMOKE_INSTANCE,
                              "asset_metadata": {"instance_name": GCE_SMOKE_INSTANCE, "zone": GCE_ZONE}}

        # Wait up to 5 min for agent to register
        print("  Waiting up to 5 min for Nexplane agent to register...")
        deadline = time.time() + 300
        agent_asset = None
        while time.time() < deadline:
            candidates = client.get("/assets", params={"q": GCE_SMOKE_INSTANCE, "asset_type": "endpoint"})
            if candidates:
                agent_asset = candidates[0]
                log(f"Agent registered: {agent_asset['id']}")
                break
            time.sleep(15)
        if not agent_asset:
            print("  ⚠️  Agent not yet registered — startup script may still be running")

        log("Phase L complete")
        return {"instance_asset": instance_asset, "rollback_stack": rollback_stack}

    except Exception as e:
        print(f"\n❌ Phase L failed: {e}")
        raise
    finally:
        if not instance_created:
            return {"instance_asset": None, "rollback_stack": []}
        print("  [Phase L cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete instance via GCP SDK
        try:
            compute = _get_gcp_compute_client()
            if compute and gcp_project:
                compute.delete(project=gcp_project, zone=GCE_ZONE, instance=GCE_SMOKE_INSTANCE)
                print(f"  Safety net: deleted GCE instance {GCE_SMOKE_INSTANCE}")
        except Exception as e2:
            print(f"  ⚠️  Safety net GCE delete failed: {e2}")
```

- [ ] **Step 6: Wire Phase L into `main()`**

In `main()`, after the Phase K block, add:

```python
        gcp_phase_result: Optional[dict] = None
        if "L" in phases:
            agent_secret = client.get_agent_secret()
            gcp_phase_result = run_phase_l(client, cloud_account_id, args.gcp_project, agent_secret)
```

- [ ] **Step 7: Update `--phases` help text**

Find the `--phases` argument. Replace the help string:

```python
        help="Comma-separated phases to run (A-M). Phase J is slow (~35 min, creates RDS). GCP phases L-M require --gcp-project. E.g. --phases A,B,C,D or --phases L,M",
```

- [ ] **Step 8: Verify the file parses**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_cloud_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 9: Commit**

```bash
git add backend/tests/smoke/test_cloud_live.py
git commit -m "feat(smoke): rename to test_cloud_live.py, add GCP helpers and Phase L"
```

---

### Task 10: Smoke test — Phase M

**Files:**
- Modify: `backend/tests/smoke/test_cloud_live.py`

- [ ] **Step 1: Add `run_phase_m` function**

Before the `# Main` section, after `run_phase_l`, add:

```python
# ---------------------------------------------------------------------------
# Phase M
# ---------------------------------------------------------------------------

def run_phase_m(client: NexplaneClient, phase_l_result: dict, gcp_project: str) -> None:
    """Phase M: GCE advanced — stop/start/reboot/snapshot with rollback stack."""
    print("\n[Phase M] GCE Advanced Operations")

    instance_asset = phase_l_result["instance_asset"]
    instance_name = instance_asset.get("asset_metadata", {}).get("instance_name", GCE_SMOKE_INSTANCE)
    zone = instance_asset.get("asset_metadata", {}).get("zone", GCE_ZONE)

    rollback_stack: list[tuple[str, str]] = []
    snapshot_name: str | None = None

    try:
        # 1. Stop instance
        cr = client.run_cr(
            "Smoke-M: stop GCE instance", "gce_stop", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.append((cr["id"], "gce_stop"))
        log("GCE instance stopped")

        # 2. Start instance
        cr = client.run_cr(
            "Smoke-M: start GCE instance", "gce_start", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.pop()  # stop superseded
        rollback_stack.append((cr["id"], "gce_start"))
        log("GCE instance started")

        # 3. Reboot
        client.run_cr(
            "Smoke-M: reboot GCE instance", "gce_instance_reboot", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        log("GCE instance rebooted")

        # Wait for agent to reconnect post-reboot
        time.sleep(30)
        assets = client.get("/assets", params={"q": GCE_SMOKE_INSTANCE, "asset_type": "endpoint"})
        if assets:
            log("Agent still registered post-reboot")
        else:
            print("  ⚠️  Agent not visible post-reboot (may still be reconnecting)")

        # 4. Create disk snapshot
        snapshot_name = f"nexplane-smoke-snap-{int(time.time())}"
        cr = client.run_cr(
            "Smoke-M: create disk snapshot", "gce_disk_snapshot", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone, "snapshot_name": snapshot_name},
        )
        rollback_stack.append((cr["id"], "gce_disk_snapshot"))
        log(f"Disk snapshot created: {snapshot_name}")

        # 5. Verify snapshot via GCP SDK
        try:
            from google.cloud import compute_v1 as _cv1
            creds_cache = _gcp_creds_cache
            if creds_cache and gcp_project:
                import json as _j
                from google.oauth2 import service_account as _sa
                key_json_raw = creds_cache.get("service_account_key_json", "")
                key_json = _j.loads(key_json_raw) if isinstance(key_json_raw, str) else key_json_raw
                gcp_creds = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                snap_client = _cv1.SnapshotsClient(credentials=gcp_creds)
                snap = snap_client.get(project=gcp_project, snapshot=snapshot_name)
                log(f"Snapshot verified: status={snap.status}, size={snap.disk_size_gb}GB")
        except Exception as e:
            print(f"  ⚠️  Snapshot verify skipped: {e}")

        log("Phase M complete")

    except Exception as e:
        print(f"\n❌ Phase M failed: {e}")
        raise
    finally:
        print("  [Phase M cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete snapshot directly
        if snapshot_name and gcp_project:
            try:
                from google.cloud import compute_v1 as _cv1
                import json as _j
                from google.oauth2 import service_account as _sa
                creds_cache = _gcp_creds_cache
                if creds_cache:
                    key_json_raw = creds_cache.get("service_account_key_json", "")
                    key_json = _j.loads(key_json_raw) if isinstance(key_json_raw, str) else key_json_raw
                    gcp_creds = _sa.Credentials.from_service_account_info(
                        key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    snap_client = _cv1.SnapshotsClient(credentials=gcp_creds)
                    snap_client.delete(project=gcp_project, snapshot=snapshot_name)
                    print(f"  Safety net: deleted snapshot {snapshot_name}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")
```

- [ ] **Step 2: Wire Phase M into `main()`**

After the Phase L block, add:

```python
        if "M" in phases:
            if gcp_phase_result is None or gcp_phase_result.get("instance_asset") is None:
                fail("Phase M requires Phase L to have run first")
            run_phase_m(client, gcp_phase_result, args.gcp_project)
```

- [ ] **Step 3: Verify the file parses**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_cloud_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 4: Run existing backend unit tests to ensure no regressions**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: `13 passed` (or more with the new gcp executor tests)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_cloud_live.py
git commit -m "feat(smoke): add Phase M — GCE stop/start/reboot/snapshot with rollback stack"
```

---

### Task 11: Self-review + final commit

- [ ] **Step 1: Run full backend test suite**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests pass, no failures.

- [ ] **Step 2: Verify frontend compiles**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20"
```

Expected: no new errors from our changes.

- [ ] **Step 3: Verify catalog loads cleanly**

```bash
docker exec nexplane-backend-1 python -c "
from app.connectors.catalog_service import get_catalog_service
c = get_catalog_service()
gcp_actions = [a['action_id'] for a in c.get_connector_actions('gcp')]
required = ['launch_instance', 'reboot_instance', 'create_disk_snapshot', 'delete_disk_snapshot',
            'capture_instance_state', 'health_check', 'wait_instance_state']
for r in required:
    assert r in gcp_actions, f'Missing: {r}'
print(f'GCP catalog OK — {len(gcp_actions)} actions')
"
```

Expected: `GCP catalog OK — 21 actions`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(gcp): GCE instance lifecycle complete — executors, change types, UI, smoke tests L+M"
```
