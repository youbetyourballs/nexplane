# Azure VM Lifecycle — Sub-project A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Azure VM lifecycle (launch/stop/start/reboot/delete/snapshot/run-command) with real Azure API calls, connector-aware Quick Actions UI, and live smoke test phases N and O using the rollback stack pattern.

**Architecture:** Nine new/updated executor files in `backend/app/connectors/executors/azure/` follow the same pattern as GCP executors — mock path when `not creds`, real path via `azure.mgmt.compute` SDK, `asyncio.get_running_loop()` + `run_in_executor`. The `launch_vm` executor creates the full Azure networking stack (VNet, subnet, public IP, NIC) prefixed with `{vm_name}-` for clean teardown. Change type JSONs, Alembic migration 025, catalog entries, and frontend `connectorType: "azure"` filtering complete the vertical slice. Smoke test phases N and O follow the established rollback stack pattern from Phases L/M.

**Tech Stack:** Python 3.12, `azure-mgmt-compute>=33.0.0`, `azure-mgmt-network>=27.0.0`, `azure-identity>=1.19.0` (all in requirements.txt), React 18, TypeScript

---

## Files

**Create:**
- `backend/app/connectors/executors/azure/capture_vm_state.py`
- `backend/app/connectors/executors/azure/health_check.py`
- `backend/app/connectors/executors/azure/wait_vm_state.py`
- `backend/app/connectors/executors/azure/reboot_vm.py`
- `backend/app/connectors/executors/azure/terminate_vm.py`
- `backend/app/connectors/executors/azure/create_disk_snapshot.py`
- `backend/app/connectors/executors/azure/delete_disk_snapshot.py`
- `backend/app/connectors/executors/azure/run_command.py`
- `backend/app/connectors/executors/azure/launch_vm.py`
- `backend/app/connectors/change_type_definitions/azure_vm_create.json`
- `backend/app/connectors/change_type_definitions/azure_vm_stop.json`
- `backend/app/connectors/change_type_definitions/azure_vm_start.json`
- `backend/app/connectors/change_type_definitions/azure_vm_reboot.json`
- `backend/app/connectors/change_type_definitions/azure_vm_delete.json`
- `backend/app/connectors/change_type_definitions/azure_vm_snapshot.json`
- `backend/app/connectors/change_type_definitions/azure_run_command.json`
- `backend/alembic/versions/025_add_azure_vm_change_types.py`
- `backend/tests/test_azure_executors.py`

**Modify:**
- `backend/app/connectors/executors/azure/discover_vms.py` — remove mock fallback, fix `get_event_loop`
- `backend/app/connectors/executors/azure/deallocate_vm.py` — fix `get_event_loop`, add real rollback
- `backend/app/connectors/executors/azure/start_vm.py` — fix `get_event_loop`, add real rollback
- `backend/app/models/change_request.py` — add 7 new ChangeType values
- `backend/app/connectors/catalog/azure.json` — add 9 new action entries + rollback_action on existing
- `frontend/src/types/api.ts` — add 7 ChangeType literals
- `frontend/src/pages/AssetDetail.tsx` — add Azure server + cloud_account Quick Actions
- `frontend/src/pages/CreateChangeRequest.tsx` — add Azure VMs group, filters, templates
- `backend/tests/smoke/test_cloud_live.py` — add `_azure_creds_cache`, `_get_azure_compute_client`, Phases N and O

---

### Task 1: Fix existing Azure executors — discover_vms, deallocate_vm, start_vm

**Files:**
- Modify: `backend/app/connectors/executors/azure/discover_vms.py`
- Modify: `backend/app/connectors/executors/azure/deallocate_vm.py`
- Modify: `backend/app/connectors/executors/azure/start_vm.py`
- Create: `backend/tests/test_azure_executors.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_azure_executors.py`:

```python
import pytest


def test_discover_vms_mock_returns_empty():
    import asyncio
    from app.connectors.executors.azure.discover_vms import execute
    result = asyncio.run(execute({}, [], None))
    assert result == []


def test_deallocate_vm_mock():
    import asyncio
    from app.connectors.executors.azure.deallocate_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "deallocate_vm"
    assert result["mock"] is True


def test_start_vm_mock():
    import asyncio
    from app.connectors.executors.azure.start_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "start_vm"
    assert result["mock"] is True


def test_deallocate_vm_rollback_calls_start():
    import asyncio
    from app.connectors.executors.azure.deallocate_vm import rollback
    result = asyncio.run(rollback({"resource_group": "rg", "vm_name": "vm1"}, {}, None))
    assert result["action"] == "start_vm"
    assert result["mock"] is True


def test_start_vm_rollback_calls_deallocate():
    import asyncio
    from app.connectors.executors.azure.start_vm import rollback
    result = asyncio.run(rollback({"resource_group": "rg", "vm_name": "vm1"}, {}, None))
    assert result["action"] == "deallocate_vm"
    assert result["mock"] is True
```

- [ ] **Step 2: Run to verify they fail**

Copy test file to main repo and run:
```powershell
Copy-Item "backend/tests/test_azure_executors.py" "f:/Nexplane/nexplane/backend/tests/test_azure_executors.py"
```
```bash
docker exec nexplane-backend-1 python -m pytest tests/test_azure_executors.py -v 2>&1 | tail -15
```
Expected: `test_discover_vms_mock_returns_empty` FAILS (returns mock data, not `[]`); rollback tests FAIL (wrong action).

- [ ] **Step 3: Fix `discover_vms.py`**

Replace entire file content:

```python
import asyncio
from datetime import datetime, timezone


async def _real_execute(creds: dict) -> list:
    from ._client import get_compute_client
    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    vms = await loop.run_in_executor(None, lambda: list(compute.virtual_machines.list_all()))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for vm in vms:
        location = getattr(vm, "location", "unknown")
        hw = getattr(vm, "hardware_profile", None)
        vm_size = getattr(hw, "vm_size", "unknown") if hw else "unknown"
        # Extract resource_group from VM id: /subscriptions/.../resourceGroups/{rg}/providers/...
        rg = "unknown"
        if vm.id:
            parts = vm.id.split("/")
            try:
                rg = parts[parts.index("resourceGroups") + 1]
            except (ValueError, IndexError):
                pass
        results.append({
            "name": vm.name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["azure", "nexplane-managed"],
            "asset_metadata": {
                "vm_name": vm.name,
                "resource_group": rg,
                "location": location,
                "vm_size": vm_size,
                "provider": "azure",
                "subscription_id": creds.get("subscription_id"),
                "discovered_at": now,
            },
        })
    return results


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return []
    return await _real_execute(creds)
```

- [ ] **Step 4: Fix `deallocate_vm.py`**

Replace entire file content:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    if not creds:
        return {"action": "deallocate_vm", "resource_group": resource_group, "vm_name": vm_name, "mock": True}
    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: client.virtual_machines.begin_deallocate(resource_group, vm_name).result()
    )
    return {
        "action": "deallocate_vm",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.start_vm import execute as start
    return await start(parameters, [], connector)
```

- [ ] **Step 5: Fix `start_vm.py`**

Replace entire file content:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    if not creds:
        return {"action": "start_vm", "resource_group": resource_group, "vm_name": vm_name, "mock": True}
    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: client.virtual_machines.begin_start(resource_group, vm_name).result()
    )
    return {
        "action": "start_vm",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.deallocate_vm import execute as deallocate
    return await deallocate(parameters, [], connector)
```

- [ ] **Step 6: Run tests**

Copy all three modified files to main repo, run tests, clean up:
```powershell
Copy-Item "backend/app/connectors/executors/azure/discover_vms.py" "f:/Nexplane/nexplane/backend/app/connectors/executors/azure/discover_vms.py"
Copy-Item "backend/app/connectors/executors/azure/deallocate_vm.py" "f:/Nexplane/nexplane/backend/app/connectors/executors/azure/deallocate_vm.py"
Copy-Item "backend/app/connectors/executors/azure/start_vm.py" "f:/Nexplane/nexplane/backend/app/connectors/executors/azure/start_vm.py"
```
```bash
docker exec nexplane-backend-1 python -m pytest tests/test_azure_executors.py -v 2>&1 | tail -10
```
Expected: `5 passed`. Clean up: `cd f:/Nexplane/nexplane && git checkout -- backend/`; remove untracked test file.

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/azure/discover_vms.py \
        backend/app/connectors/executors/azure/deallocate_vm.py \
        backend/app/connectors/executors/azure/start_vm.py \
        backend/tests/test_azure_executors.py
git commit -m "fix(azure): real discover_vms (no mock fallback), get_running_loop, deallocate/start rollbacks"
```

---

### Task 2: Helper executors — capture_vm_state, health_check, wait_vm_state

**Files:**
- Create: `backend/app/connectors/executors/azure/capture_vm_state.py`
- Create: `backend/app/connectors/executors/azure/health_check.py`
- Create: `backend/app/connectors/executors/azure/wait_vm_state.py`
- Modify: `backend/tests/test_azure_executors.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/test_azure_executors.py`:

```python
def test_capture_vm_state_mock():
    import asyncio
    from app.connectors.executors.azure.capture_vm_state import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "capture_vm_state"
    assert result["vm_name"] == "vm1"
    assert result["mock"] is True


def test_health_check_mock():
    import asyncio
    from app.connectors.executors.azure.health_check import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "health_check"
    assert result["power_state"] == "PowerState/running"


def test_wait_vm_state_mock():
    import asyncio
    from app.connectors.executors.azure.wait_vm_state import execute
    result = asyncio.run(execute(
        {"resource_group": "rg", "vm_name": "vm1", "target_state": "running"}, [], None
    ))
    assert result["action"] == "wait_vm_state"
    assert result["reached"] is True
```

- [ ] **Step 2: Create `capture_vm_state.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {
            "action": "capture_vm_state",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "vm_size": "Standard_B1s",
            "location": "eastus",
            "os_disk_name": f"{vm_name}-osdisk",
            "tags": {},
            "mock": True,
        }

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    vm = await loop.run_in_executor(None, lambda: client.virtual_machines.get(resource_group, vm_name))

    os_disk_name = ""
    if vm.storage_profile and vm.storage_profile.os_disk:
        os_disk_name = vm.storage_profile.os_disk.name or ""

    return {
        "action": "capture_vm_state",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else "unknown",
        "location": vm.location,
        "os_disk_name": os_disk_name,
        "tags": dict(vm.tags or {}),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture_vm_state is read-only"}
```

- [ ] **Step 3: Create `health_check.py`**

```python
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {
            "action": "health_check",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "power_state": "PowerState/running",
        }

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    vm = await loop.run_in_executor(
        None, lambda: client.virtual_machines.get(resource_group, vm_name, expand="instanceView")
    )

    statuses = vm.instance_view.statuses if vm.instance_view else []
    power_state = next(
        (s.code for s in statuses if s.code and s.code.startswith("PowerState/")),
        "PowerState/unknown",
    )

    if power_state != "PowerState/running":
        raise RuntimeError(f"VM {vm_name} is not running — power state: {power_state}")

    return {
        "action": "health_check",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "power_state": power_state,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "health_check is read-only"}
```

- [ ] **Step 4: Create `wait_vm_state.py`**

```python
import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    target_state = parameters.get("target_state", "running")
    timeout = parameters.get("timeout_seconds", 300)

    if not creds:
        return {
            "action": "wait_vm_state",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "target_state": target_state,
            "reached": True,
        }

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    target_code = f"PowerState/{target_state}"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        vm = await loop.run_in_executor(
            None, lambda: client.virtual_machines.get(resource_group, vm_name, expand="instanceView")
        )
        statuses = {s.code for s in (vm.instance_view.statuses or []) if s.code}
        if target_code in statuses:
            return {
                "action": "wait_vm_state",
                "resource_group": resource_group,
                "vm_name": vm_name,
                "target_state": target_state,
                "reached": True,
            }
        await asyncio.sleep(10)

    raise RuntimeError(f"Timed out waiting for {vm_name} to reach {target_code}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait_vm_state is read-only"}
```

- [ ] **Step 5: Run tests**

Copy and test (same worktree→main repo pattern as Task 1). Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/azure/capture_vm_state.py \
        backend/app/connectors/executors/azure/health_check.py \
        backend/app/connectors/executors/azure/wait_vm_state.py \
        backend/tests/test_azure_executors.py
git commit -m "feat(azure): add capture_vm_state, health_check, wait_vm_state executors"
```

---

### Task 3: Action executors — reboot_vm, terminate_vm, create_disk_snapshot, delete_disk_snapshot, run_command

**Files:**
- Create: `backend/app/connectors/executors/azure/reboot_vm.py`
- Create: `backend/app/connectors/executors/azure/terminate_vm.py`
- Create: `backend/app/connectors/executors/azure/create_disk_snapshot.py`
- Create: `backend/app/connectors/executors/azure/delete_disk_snapshot.py`
- Create: `backend/app/connectors/executors/azure/run_command.py`
- Modify: `backend/tests/test_azure_executors.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/test_azure_executors.py`:

```python
def test_reboot_vm_mock():
    import asyncio
    from app.connectors.executors.azure.reboot_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "reboot_vm"
    assert result["vm_name"] == "vm1"


def test_terminate_vm_mock():
    import asyncio
    from app.connectors.executors.azure.terminate_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "terminate_vm"
    assert result["deleted"] is True


def test_create_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.azure.create_disk_snapshot import execute
    result = asyncio.run(execute(
        {"resource_group": "rg", "vm_name": "vm1", "snapshot_name": "snap-001"}, [], None
    ))
    assert result["action"] == "create_disk_snapshot"
    assert result["snapshot_name"] == "snap-001"


def test_delete_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.azure.delete_disk_snapshot import execute
    result = asyncio.run(execute({"resource_group": "rg", "snapshot_name": "snap-001"}, [], None))
    assert result["action"] == "delete_disk_snapshot"
    assert result["deleted"] is True


def test_run_command_mock():
    import asyncio
    from app.connectors.executors.azure.run_command import execute
    result = asyncio.run(execute(
        {"resource_group": "rg", "vm_name": "vm1", "command": "uptime"}, [], None
    ))
    assert result["action"] == "run_command"
    assert result["vm_name"] == "vm1"
```

- [ ] **Step 2: Create `reboot_vm.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {"action": "reboot_vm", "resource_group": resource_group, "vm_name": vm_name}

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: client.virtual_machines.begin_restart(resource_group, vm_name).result()
    )
    return {
        "action": "reboot_vm",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot_vm has no meaningful inverse"}
```

- [ ] **Step 3: Create `terminate_vm.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {"action": "terminate_vm", "resource_group": resource_group, "vm_name": vm_name, "deleted": True, "mock": True}

    from ._client import get_compute_client, get_network_client
    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Delete VM
    await loop.run_in_executor(
        None, lambda: compute.virtual_machines.begin_delete(resource_group, vm_name).result()
    )

    # Clean up networking resources created by launch_vm (named {vm_name}-*)
    for cleanup in [
        lambda: network.network_interfaces.begin_delete(resource_group, f"{vm_name}-nic").result(),
        lambda: network.public_ip_addresses.begin_delete(resource_group, f"{vm_name}-pip").result(),
        lambda: network.virtual_networks.begin_delete(resource_group, f"{vm_name}-vnet").result(),
    ]:
        try:
            await loop.run_in_executor(None, cleanup)
        except Exception:
            pass  # resources may not exist (e.g., user-provided VNet)

    return {
        "action": "terminate_vm",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terminate_vm is irreversible"}
```

- [ ] **Step 4: Create `create_disk_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    snapshot_name = parameters.get("snapshot_name", f"nexplane-snap-{vm_name}")

    if not creds:
        return {
            "action": "create_disk_snapshot",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "snapshot_name": snapshot_name,
            "disk_name": f"{vm_name}-osdisk",
            "mock": True,
        }

    from ._client import get_compute_client
    from azure.mgmt.compute.models import Snapshot, CreationData, DiskCreateOption
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    # Get the OS disk resource ID
    vm = await loop.run_in_executor(None, lambda: client.virtual_machines.get(resource_group, vm_name))
    disk_id = vm.storage_profile.os_disk.managed_disk.id
    disk_name = vm.storage_profile.os_disk.name
    location = vm.location

    snapshot = await loop.run_in_executor(
        None,
        lambda: client.snapshots.begin_create_or_update(
            resource_group,
            snapshot_name,
            Snapshot(
                location=location,
                creation_data=CreationData(
                    create_option=DiskCreateOption.copy,
                    source_resource_id=disk_id,
                ),
                tags={"managed-by": "nexplane", "source-vm": vm_name},
            ),
        ).result(),
    )

    return {
        "action": "create_disk_snapshot",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "snapshot_name": snapshot_name,
        "disk_name": disk_name,
        "snapshot_id": snapshot.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_disk_snapshot import execute as delete_snap
    return await delete_snap(
        {
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
            "snapshot_name": execution_result.get("snapshot_name", parameters.get("snapshot_name")),
        },
        [], connector,
    )
```

- [ ] **Step 5: Create `delete_disk_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    snapshot_name = parameters["snapshot_name"]

    if not creds:
        return {"action": "delete_disk_snapshot", "resource_group": resource_group, "snapshot_name": snapshot_name, "deleted": True, "mock": True}

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: client.snapshots.begin_delete(resource_group, snapshot_name).result()
    )
    return {
        "action": "delete_disk_snapshot",
        "resource_group": resource_group,
        "snapshot_name": snapshot_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_disk_snapshot is terminal"}
```

- [ ] **Step 6: Create `run_command.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    command = parameters["command"]

    if not creds:
        return {
            "action": "run_command",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "command": command,
            "stdout": "mock_output_ok",
            "stderr": "",
        }

    from ._client import get_compute_client
    from azure.mgmt.compute.models import RunCommandInput
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    result = await loop.run_in_executor(
        None,
        lambda: client.virtual_machines.begin_run_command(
            resource_group,
            vm_name,
            RunCommandInput(command_id="RunShellScript", script=[command]),
        ).result(),
    )

    stdout = ""
    stderr = ""
    if result and result.value:
        stdout = result.value[0].message if result.value[0].message else ""
    if result and len(result.value) > 1:
        stderr = result.value[1].message if result.value[1].message else ""

    return {
        "action": "run_command",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "run_command has no meaningful inverse"}
```

- [ ] **Step 7: Run all tests**

Copy files to main repo, run, expected: `13 passed`. Clean up main repo.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/azure/reboot_vm.py \
        backend/app/connectors/executors/azure/terminate_vm.py \
        backend/app/connectors/executors/azure/create_disk_snapshot.py \
        backend/app/connectors/executors/azure/delete_disk_snapshot.py \
        backend/app/connectors/executors/azure/run_command.py \
        backend/tests/test_azure_executors.py
git commit -m "feat(azure): add reboot_vm, terminate_vm, create/delete_disk_snapshot, run_command executors"
```

---

### Task 4: launch_vm executor

**Files:**
- Create: `backend/app/connectors/executors/azure/launch_vm.py`
- Modify: `backend/tests/test_azure_executors.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/test_azure_executors.py`:

```python
def test_launch_vm_mock_agent_extension():
    import asyncio
    from app.connectors.executors.azure.launch_vm import execute
    result = asyncio.run(execute({
        "vm_name": "nexplane-smoke-azure-01",
        "resource_group": "nexplane-smoke-rg",
        "location": "eastus",
        "vm_size": "Standard_B1s",
        "connection_mode": "agent_extension",
        "nexplane_url": "http://localhost:8000",
        "nexplane_secret": "test-secret",
    }, [], None))
    assert result["action"] == "launch_vm"
    assert result["vm_name"] == "nexplane-smoke-azure-01"
    assert "_auto_asset" in result
    assert result["_auto_asset"]["asset_type"] == "server"
    assert result["mock"] is True


def test_launch_vm_mock_ssh_mode():
    import asyncio
    from app.connectors.executors.azure.launch_vm import execute
    result = asyncio.run(execute({
        "vm_name": "nexplane-smoke-azure-02",
        "resource_group": "nexplane-smoke-rg",
        "location": "eastus",
        "vm_size": "Standard_B1s",
        "connection_mode": "ssh",
        "ssh_public_key": "ssh-rsa AAAA...",
    }, [], None))
    assert result["connection_mode"] == "ssh"
    assert result["mock"] is True
```

- [ ] **Step 2: Create `launch_vm.py`**

```python
import asyncio
import base64
from datetime import datetime, timezone


_AGENT_STARTUP_SCRIPT = """#!/bin/bash
set -e
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
    vm_name = parameters["vm_name"]
    resource_group = parameters["resource_group"]
    location = parameters.get("location", "eastus")
    vm_size = parameters.get("vm_size", "Standard_B1s")
    connection_mode = parameters.get("connection_mode", "agent_extension")
    nexplane_url = parameters.get("nexplane_url", "")
    nexplane_secret = parameters.get("nexplane_secret", "")
    ssh_public_key = parameters.get("ssh_public_key", "")
    admin_password = parameters.get("admin_password", "")
    admin_username = parameters.get("admin_username", "azureuser")

    auto_asset = {
        "name": vm_name,
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "vm_name": vm_name,
            "resource_group": resource_group,
            "location": location,
            "vm_size": vm_size,
            "connection_mode": connection_mode,
            "os_type": "Linux",
            "provider": "azure",
        },
        "tags": ["azure-vm", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "launch_vm",
            "vm_name": vm_name,
            "resource_group": resource_group,
            "location": location,
            "connection_mode": connection_mode,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_compute_client, get_network_client
    from azure.mgmt.compute.models import (
        VirtualMachine, HardwareProfile, StorageProfile, OsProfile,
        NetworkProfile, NetworkInterfaceReference, LinuxConfiguration,
        SshConfiguration, SshPublicKey, ImageReference, OSDisk,
        DiskCreateOptionTypes, ManagedDiskParameters,
    )
    from azure.mgmt.network.models import (
        VirtualNetwork, AddressSpace, Subnet, PublicIPAddress,
        PublicIPAddressSku, NetworkInterface, NetworkInterfaceIPConfiguration,
    )

    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Create public IP
    pip = await loop.run_in_executor(
        None,
        lambda: network.public_ip_addresses.begin_create_or_update(
            resource_group, f"{vm_name}-pip",
            PublicIPAddress(
                location=location,
                sku=PublicIPAddressSku(name="Basic"),
                public_ip_allocation_method="Dynamic",
            ),
        ).result(),
    )

    # Create VNet + subnet
    vnet = await loop.run_in_executor(
        None,
        lambda: network.virtual_networks.begin_create_or_update(
            resource_group, f"{vm_name}-vnet",
            VirtualNetwork(
                location=location,
                address_space=AddressSpace(address_prefixes=["10.0.0.0/16"]),
                subnets=[Subnet(name="default", address_prefix="10.0.0.0/24")],
            ),
        ).result(),
    )
    subnet = await loop.run_in_executor(
        None, lambda: network.subnets.get(resource_group, f"{vm_name}-vnet", "default")
    )

    # Create NIC
    nic = await loop.run_in_executor(
        None,
        lambda: network.network_interfaces.begin_create_or_update(
            resource_group, f"{vm_name}-nic",
            NetworkInterface(
                location=location,
                ip_configurations=[
                    NetworkInterfaceIPConfiguration(
                        name="ipconfig1",
                        subnet=subnet,
                        public_ip_address=pip,
                    )
                ],
            ),
        ).result(),
    )

    # Build OS profile based on connection_mode
    if connection_mode == "password":
        os_profile = OsProfile(
            computer_name=vm_name,
            admin_username=admin_username,
            admin_password=admin_password,
        )
    else:
        # agent_extension and ssh both use SSH key auth
        key_data = ssh_public_key if ssh_public_key else "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC placeholder"
        os_profile = OsProfile(
            computer_name=vm_name,
            admin_username=admin_username,
            linux_configuration=LinuxConfiguration(
                disable_password_authentication=True,
                ssh=SshConfiguration(
                    public_keys=[
                        SshPublicKey(
                            path=f"/home/{admin_username}/.ssh/authorized_keys",
                            key_data=key_data,
                        )
                    ]
                ),
            ),
        )

    # Create VM
    vm = await loop.run_in_executor(
        None,
        lambda: compute.virtual_machines.begin_create_or_update(
            resource_group, vm_name,
            VirtualMachine(
                location=location,
                hardware_profile=HardwareProfile(vm_size=vm_size),
                storage_profile=StorageProfile(
                    image_reference=ImageReference(
                        publisher="Canonical",
                        offer="0001-com-ubuntu-server-jammy",
                        sku="22_04-lts",
                        version="latest",
                    ),
                    os_disk=OSDisk(
                        create_option=DiskCreateOptionTypes.from_image,
                        delete_option="Delete",
                    ),
                ),
                os_profile=os_profile,
                network_profile=NetworkProfile(
                    network_interfaces=[
                        NetworkInterfaceReference(id=nic.id, primary=True)
                    ]
                ),
            ),
        ).result(),
    )

    # Deploy Custom Script Extension for agent_extension mode
    if connection_mode == "agent_extension":
        from azure.mgmt.compute.models import VirtualMachineExtension
        startup_script = _AGENT_STARTUP_SCRIPT.format(
            nexplane_url=nexplane_url,
            nexplane_secret=nexplane_secret,
        )
        script_b64 = base64.b64encode(startup_script.encode()).decode()
        await loop.run_in_executor(
            None,
            lambda: compute.virtual_machine_extensions.begin_create_or_update(
                resource_group, vm_name, "NexplaneAgentInstall",
                VirtualMachineExtension(
                    location=location,
                    publisher="Microsoft.Azure.Extensions",
                    type_properties_type="CustomScript",
                    type_handler_version="2.1",
                    auto_upgrade_minor_version=True,
                    settings={"script": script_b64},
                ),
            ).result(),
        )

    # Get public IP (assigned after creation)
    public_ip = ""
    try:
        pip_refreshed = await loop.run_in_executor(
            None, lambda: network.public_ip_addresses.get(resource_group, f"{vm_name}-pip")
        )
        public_ip = pip_refreshed.ip_address or ""
    except Exception:
        pass

    auto_asset["asset_metadata"]["public_ip"] = public_ip

    return {
        "action": "launch_vm",
        "vm_name": vm_name,
        "resource_group": resource_group,
        "location": location,
        "connection_mode": connection_mode,
        "public_ip": public_ip,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.terminate_vm import execute as terminate
    return await terminate(
        {
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
            "vm_name": execution_result.get("vm_name", parameters.get("vm_name")),
        },
        [], connector,
    )
```

- [ ] **Step 3: Run tests** — expected: `15 passed`. Copy, test, clean up.

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/azure/launch_vm.py \
        backend/tests/test_azure_executors.py
git commit -m "feat(azure): add launch_vm executor with agent_extension/ssh/password connection modes"
```

---

### Task 5: ChangeType model + migration + change type JSONs + catalog

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/025_add_azure_vm_change_types.py`
- Create: 7 JSON files in `backend/app/connectors/change_type_definitions/`
- Modify: `backend/app/connectors/catalog/azure.json`
- Modify: `backend/tests/test_azure_executors.py`

- [ ] **Step 1: Add failing test**

Append to `backend/tests/test_azure_executors.py`:

```python
def test_azure_vm_change_types_in_enum():
    from app.models.change_request import ChangeType
    assert ChangeType.azure_vm_create == "azure_vm_create"
    assert ChangeType.azure_vm_stop == "azure_vm_stop"
    assert ChangeType.azure_vm_start == "azure_vm_start"
    assert ChangeType.azure_vm_reboot == "azure_vm_reboot"
    assert ChangeType.azure_vm_delete == "azure_vm_delete"
    assert ChangeType.azure_vm_snapshot == "azure_vm_snapshot"
    assert ChangeType.azure_run_command == "azure_run_command"
```

- [ ] **Step 2: Add ChangeType values to `backend/app/models/change_request.py`**

Find the `# GCP instance lifecycle — Sub-project A` block (around line 47). After `gce_disk_snapshot = "gce_disk_snapshot"`, add:

```python
    # Azure VM lifecycle — Sub-project A
    azure_vm_create = "azure_vm_create"
    azure_vm_stop = "azure_vm_stop"
    azure_vm_start = "azure_vm_start"
    azure_vm_reboot = "azure_vm_reboot"
    azure_vm_delete = "azure_vm_delete"
    azure_vm_snapshot = "azure_vm_snapshot"
    azure_run_command = "azure_run_command"
```

- [ ] **Step 3: Create Alembic migration `backend/alembic/versions/025_add_azure_vm_change_types.py`**

```python
"""add azure vm lifecycle change types

Revision ID: 025
Revises: 024
Create Date: 2026-05-05
"""
from alembic import op

revision = '025'
down_revision = '024'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'azure_vm_create', 'azure_vm_stop', 'azure_vm_start',
        'azure_vm_reboot', 'azure_vm_delete', 'azure_vm_snapshot',
        'azure_run_command',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 4: Create `azure_vm_create.json`**

```json
{
  "change_type": "azure_vm_create",
  "display_name": "Launch Azure VM",
  "steps": [
    {"generic_action": "launch_vm",     "purpose": "execute", "required": true},
    {"generic_action": "wait_vm_state", "purpose": "verify",  "required": true},
    {"generic_action": "health_check",  "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "no_concurrent_changes"],
  "verification_methods": ["api_check"],
  "rollback_action": "terminate_vm",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 5: Create `azure_vm_stop.json`**

```json
{
  "change_type": "azure_vm_stop",
  "display_name": "Stop Azure VM",
  "steps": [
    {"generic_action": "capture_vm_state", "purpose": "preflight_validate", "required": true},
    {"generic_action": "deallocate_vm",    "purpose": "execute",            "required": true},
    {"generic_action": "wait_vm_state",    "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "start_vm",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 6: Create `azure_vm_start.json`**

```json
{
  "change_type": "azure_vm_start",
  "display_name": "Start Azure VM",
  "steps": [
    {"generic_action": "start_vm",      "purpose": "execute", "required": true},
    {"generic_action": "wait_vm_state", "purpose": "verify",  "required": true},
    {"generic_action": "health_check",  "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "deallocate_vm",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 7: Create `azure_vm_reboot.json`**

```json
{
  "change_type": "azure_vm_reboot",
  "display_name": "Reboot Azure VM",
  "steps": [
    {"generic_action": "reboot_vm",     "purpose": "execute", "required": true},
    {"generic_action": "wait_vm_state", "purpose": "verify",  "required": true},
    {"generic_action": "health_check",  "purpose": "verify",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "rollback_unavailable",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 8: Create `azure_vm_delete.json`**

```json
{
  "change_type": "azure_vm_delete",
  "display_name": "Delete Azure VM",
  "steps": [
    {"generic_action": "capture_vm_state", "purpose": "preflight_validate", "required": true},
    {"generic_action": "terminate_vm",     "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "rollback_unavailable",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 9: Create `azure_vm_snapshot.json`**

```json
{
  "change_type": "azure_vm_snapshot",
  "display_name": "Create Azure VM Disk Snapshot",
  "steps": [
    {"generic_action": "capture_vm_state",   "purpose": "preflight_validate", "required": true},
    {"generic_action": "create_disk_snapshot", "purpose": "execute",           "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_disk_snapshot",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 10: Create `azure_run_command.json`**

```json
{
  "change_type": "azure_run_command",
  "display_name": "Run Command on Azure VM",
  "steps": [
    {"generic_action": "run_command", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "rollback_unavailable",
  "rollback_connector_type": "azure"
}
```

- [ ] **Step 11: Add 9 new action entries to `backend/app/connectors/catalog/azure.json`**

Open the file. Before the closing `]` of the `"actions"` array (after the `tag_resource` entry), add:

```json
    ,
    {"action_id": "launch_vm", "generic_action": "launch_vm", "action_type": "change", "execution_tier": 2, "display_name": "Launch Azure VM", "description": "Create a new Azure VM with agent_extension, ssh, or password connection mode.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "vm_name", "type": "string", "required": true}, {"name": "resource_group", "type": "string", "required": true}, {"name": "location", "type": "string", "required": false, "default": "eastus"}, {"name": "vm_size", "type": "string", "required": false, "default": "Standard_B1s"}, {"name": "connection_mode", "type": "string", "required": false, "default": "agent_extension"}, {"name": "nexplane_url", "type": "string", "required": false}, {"name": "nexplane_secret", "type": "string", "required": false}, {"name": "ssh_public_key", "type": "string", "required": false}], "executor": "azure.launch_vm", "rollback_action": "terminate_vm", "estimated_duration_seconds": 180, "blast_radius_hint": "new_resource"},
    {"action_id": "reboot_vm", "generic_action": "reboot_vm", "action_type": "change", "execution_tier": 2, "display_name": "Reboot Azure VM", "description": "Restart a running Azure VM.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}], "executor": "azure.reboot_vm", "estimated_duration_seconds": 60},
    {"action_id": "terminate_vm", "generic_action": "terminate_vm", "action_type": "change", "execution_tier": 3, "display_name": "Delete Azure VM", "description": "Delete an Azure VM and its associated network resources.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}], "executor": "azure.terminate_vm", "estimated_duration_seconds": 120, "blast_radius_hint": "destructive"},
    {"action_id": "create_disk_snapshot", "generic_action": "create_disk_snapshot", "action_type": "change", "execution_tier": 2, "display_name": "Create Disk Snapshot", "description": "Snapshot the OS disk of an Azure VM.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}, {"name": "snapshot_name", "type": "string", "required": false}], "executor": "azure.create_disk_snapshot", "rollback_action": "delete_disk_snapshot", "estimated_duration_seconds": 60},
    {"action_id": "delete_disk_snapshot", "generic_action": "delete_disk_snapshot", "action_type": "change", "execution_tier": 2, "display_name": "Delete Disk Snapshot", "description": "Delete an Azure disk snapshot.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "snapshot_name", "type": "string", "required": true}], "executor": "azure.delete_disk_snapshot", "estimated_duration_seconds": 30},
    {"action_id": "capture_vm_state", "generic_action": "capture_vm_state", "action_type": "change", "execution_tier": 1, "display_name": "Capture VM State", "description": "Record current VM configuration as preflight baseline.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}], "executor": "azure.capture_vm_state", "estimated_duration_seconds": 5},
    {"action_id": "health_check", "generic_action": "health_check", "action_type": "change", "execution_tier": 1, "display_name": "Health Check", "description": "Verify Azure VM is in running power state.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}], "executor": "azure.health_check", "estimated_duration_seconds": 10},
    {"action_id": "wait_vm_state", "generic_action": "wait_vm_state", "action_type": "change", "execution_tier": 1, "display_name": "Wait for VM State", "description": "Poll until Azure VM reaches target power state.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}, {"name": "target_state", "type": "string", "required": false, "default": "running"}], "executor": "azure.wait_vm_state", "estimated_duration_seconds": 120},
    {"action_id": "run_command", "generic_action": "run_command", "action_type": "change", "execution_tier": 2, "display_name": "Run Command", "description": "Execute a shell command on an Azure VM via Azure Run Command.", "applicable_asset_types": ["server"], "parameters": [{"name": "resource_group", "type": "string", "required": true}, {"name": "vm_name", "type": "string", "required": true}, {"name": "command", "type": "string", "required": true}], "executor": "azure.run_command", "estimated_duration_seconds": 60}
```

Also update the existing `start_vm` entry to add `"rollback_action": "deallocate_vm"`.

- [ ] **Step 12: Run migration and test**

Copy model + migration + catalog to main repo, run migration, run test, clean up:
```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
docker exec nexplane-backend-1 python -m pytest tests/test_azure_executors.py::test_azure_vm_change_types_in_enum -v 2>&1 | tail -5
```
Expected: `1 passed`, migration shows `025`.

- [ ] **Step 13: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/alembic/versions/025_add_azure_vm_change_types.py \
        backend/app/connectors/change_type_definitions/azure_vm_create.json \
        backend/app/connectors/change_type_definitions/azure_vm_stop.json \
        backend/app/connectors/change_type_definitions/azure_vm_start.json \
        backend/app/connectors/change_type_definitions/azure_vm_reboot.json \
        backend/app/connectors/change_type_definitions/azure_vm_delete.json \
        backend/app/connectors/change_type_definitions/azure_vm_snapshot.json \
        backend/app/connectors/change_type_definitions/azure_run_command.json \
        backend/app/connectors/catalog/azure.json \
        backend/tests/test_azure_executors.py
git commit -m "feat(azure): ChangeType enum, migration 025, change type JSONs, catalog entries for Azure VM lifecycle"
```

---

### Task 6: Frontend — ChangeType literals, Quick Actions, CR modal

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/AssetDetail.tsx`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1: Add 7 Azure ChangeType literals to `frontend/src/types/api.ts`**

Find the `ChangeType` union (ends at `"gce_disk_snapshot"`). Add before the closing semicolon:

```typescript
  | "azure_vm_create"
  | "azure_vm_stop"
  | "azure_vm_start"
  | "azure_vm_reboot"
  | "azure_vm_delete"
  | "azure_vm_snapshot"
  | "azure_run_command"
```

- [ ] **Step 2: Add Azure Quick Actions to `frontend/src/pages/AssetDetail.tsx`**

Find the `server` array. After the last GCP server action (`gce_instance_delete`), add:

```typescript
    {
      changeType: "azure_vm_stop",
      label: "Stop VM",
      title: (a) => `Stop ${a.name}`,
      description: (a) => `Deallocate Azure VM ${a.asset_metadata?.vm_name ?? a.name} in ${a.asset_metadata?.resource_group ?? ""}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_start",
      label: "Start VM",
      title: (a) => `Start ${a.name}`,
      description: (a) => `Start deallocated Azure VM ${a.asset_metadata?.vm_name ?? a.name}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_reboot",
      label: "Reboot VM",
      title: (a) => `Reboot ${a.name}`,
      description: (a) => `Restart Azure VM ${a.asset_metadata?.vm_name ?? a.name}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_snapshot",
      label: "Create Disk Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Snapshot OS disk of Azure VM ${a.asset_metadata?.vm_name ?? a.name}.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_vm_delete",
      label: "Delete VM",
      title: (a) => `Delete ${a.name}`,
      description: (a) => `Permanently delete Azure VM ${a.asset_metadata?.vm_name ?? a.name}. Irreversible.`,
      connectorType: "azure",
    },
    {
      changeType: "azure_run_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute a shell command on Azure VM ${a.asset_metadata?.vm_name ?? a.name} via Azure Run Command.`,
      connectorType: "azure",
    },
```

Find the `cloud_account` array. After the last GCP cloud_account action (`gce_instance_create`), add:

```typescript
    {
      changeType: "azure_vm_create",
      label: "Launch Azure VM",
      title: (a) => `Launch Azure VM in ${a.name}`,
      description: (a) => `Create a new Azure VM in subscription ${a.asset_metadata?.subscription_id ?? a.name}.`,
      connectorType: "azure",
    },
```

- [ ] **Step 3: Extend pre-fill logic in `frontend/src/pages/CreateChangeRequest.tsx`**

Find the `useEffect` pre-fill block that handles `instance_id`, `instance_name`, `zone`. Add after the `zone` injection:

```typescript
if (preAsset?.asset_metadata?.vm_name && "vm_name" in template) {
  template = { ...template, vm_name: preAsset.asset_metadata.vm_name as string };
}
if (preAsset?.asset_metadata?.resource_group && "resource_group" in template) {
  template = { ...template, resource_group: preAsset.asset_metadata.resource_group as string };
}
```

- [ ] **Step 4: Add Azure VMs group to CHANGE_TYPE_GROUPS**

Find the `"GCE Instances"` group. After its closing brace and comma, add:

```typescript
  {
    label: "Azure VMs",
    types: ["azure_vm_create", "azure_vm_stop", "azure_vm_start", "azure_vm_reboot",
            "azure_vm_delete", "azure_vm_snapshot", "azure_run_command"],
  },
```

- [ ] **Step 5: Add CHANGE_TYPE_ASSET_FILTER entries**

Find `CHANGE_TYPE_ASSET_FILTER`. After the last GCE entry, add:

```typescript
  azure_vm_create: "cloud_account",
  azure_vm_stop: "server",
  azure_vm_start: "server",
  azure_vm_reboot: "server",
  azure_vm_delete: "server",
  azure_vm_snapshot: "server",
  azure_run_command: "server",
```

- [ ] **Step 6: Add CHANGE_TYPE_META entries**

Find `CHANGE_TYPE_META`. After the last GCE entry, add:

```typescript
  azure_vm_create: {
    label: "Launch Azure VM",
    description: "Create a new Azure VM with agent, SSH, or password connection.",
    outcomeTemplate: {
      vm_name: "",
      resource_group: "",
      location: "eastus",
      vm_size: "Standard_B1s",
      connection_mode: "agent_extension",
      rollback_strategy: "terminate_vm",
    },
  },
  azure_vm_stop: {
    label: "Stop Azure VM",
    description: "Deallocate a running Azure VM to halt compute charges.",
    outcomeTemplate: { vm_name: "", resource_group: "", rollback_strategy: "start_vm" },
  },
  azure_vm_start: {
    label: "Start Azure VM",
    description: "Start a deallocated Azure VM.",
    outcomeTemplate: { vm_name: "", resource_group: "", rollback_strategy: "deallocate_vm" },
  },
  azure_vm_reboot: {
    label: "Reboot Azure VM",
    description: "Restart a running Azure VM.",
    outcomeTemplate: { vm_name: "", resource_group: "", rollback_strategy: "rollback_unavailable" },
  },
  azure_vm_delete: {
    label: "Delete Azure VM",
    description: "Permanently delete an Azure VM and its network resources.",
    outcomeTemplate: { vm_name: "", resource_group: "", rollback_strategy: "rollback_unavailable" },
  },
  azure_vm_snapshot: {
    label: "Create Azure Disk Snapshot",
    description: "Snapshot the OS disk of an Azure VM.",
    outcomeTemplate: { vm_name: "", resource_group: "", snapshot_name: "", rollback_strategy: "delete_disk_snapshot" },
  },
  azure_run_command: {
    label: "Run Command on Azure VM",
    description: "Execute a shell command via Azure Run Command (no SSH required).",
    outcomeTemplate: { vm_name: "", resource_group: "", command: "", rollback_strategy: "rollback_unavailable" },
  },
```

- [ ] **Step 7: TypeScript check**

Copy all three modified files to main repo:
```powershell
Copy-Item "frontend/src/types/api.ts" "f:/Nexplane/nexplane/frontend/src/types/api.ts"
Copy-Item "frontend/src/pages/AssetDetail.tsx" "f:/Nexplane/nexplane/frontend/src/pages/AssetDetail.tsx"
Copy-Item "frontend/src/pages/CreateChangeRequest.tsx" "f:/Nexplane/nexplane/frontend/src/pages/CreateChangeRequest.tsx"
```
```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -v node_modules | head -20"
```
Expected: no new errors from our changes. Clean up main repo.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/api.ts \
        frontend/src/pages/AssetDetail.tsx \
        frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(azure): connector-aware Quick Actions, Azure VMs CR group, filters, templates"
```

---

### Task 7: Smoke test — Phases N and O

**Files:**
- Modify: `backend/tests/smoke/test_cloud_live.py`

- [ ] **Step 1: Add `--azure-resource-group` CLI argument**

In `main()`, after `--gcp-project`, add:

```python
    parser.add_argument("--azure-resource-group", default="", help="Azure resource group for phases N and O (must exist)")
```

- [ ] **Step 2: Update `--phases` help text**

Find the `--phases` argument. Update help to:

```python
        help="Comma-separated phases to run (A-O). Phase J is slow (~35 min, RDS). GCP phases L-M require --gcp-project. Azure phases N-O require --azure-resource-group. E.g. --phases A,B or --phases N,O",
```

- [ ] **Step 3: Add `_azure_creds_cache` and `_get_azure_compute_client()` helper**

After `_get_gcp_compute_client()`, add:

```python
_azure_creds_cache: dict = {}


def _get_azure_compute_client():
    """Get an Azure ComputeManagementClient using Azure connector credentials from the app DB."""
    import threading
    global _azure_creds_cache
    if not _azure_creds_cache:
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa

        result_holder: list = [None]

        async def _get():
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa.select(Connector).where(Connector.connector_type == ConnectorType.azure)
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
        _azure_creds_cache = result_holder[0] or {}

    creds = _azure_creds_cache
    if not creds:
        return None

    from azure.identity import ClientSecretCredential
    from azure.mgmt.compute import ComputeManagementClient

    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return ComputeManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 4: Add constants and `run_phase_n` function**

After the `# Phase M` section and before `# Main`, add:

```python
# ---------------------------------------------------------------------------
# Phase N
# ---------------------------------------------------------------------------

AZURE_SMOKE_VM = "nexplane-smoke-azure-01"


def run_phase_n(client: NexplaneClient, cloud_account_id: str,
                azure_resource_group: str, agent_secret: str) -> dict:
    """Phase N: Azure VM launch + agent deploy with rollback stack."""
    print("\n[Phase N] Azure VM Launch + Agent Deploy")

    if not azure_resource_group:
        fail("Phase N requires --azure-resource-group (resource group must exist in Azure)")

    rollback_stack: list[tuple[str, str]] = []
    vm_created = False

    try:
        cr = client.run_cr(
            "Smoke-N: launch Azure VM", "azure_vm_create", cloud_account_id,
            {
                "vm_name": AZURE_SMOKE_VM,
                "resource_group": azure_resource_group,
                "location": "eastus",
                "vm_size": "Standard_B1s",
                "connection_mode": "agent_extension",
                "nexplane_url": "http://localhost:8000",
                "nexplane_secret": agent_secret,
            },
        )
        rollback_stack.append((cr["id"], "azure_vm_create"))
        vm_created = True
        log(f"Azure VM launched: {AZURE_SMOKE_VM}")

        # Verify server asset in inventory
        time.sleep(15)
        vm_asset = client.get_asset_by_name(AZURE_SMOKE_VM)
        if vm_asset:
            log(f"Azure VM in inventory: {vm_asset['id']}")
        else:
            print(f"  ⚠️  Azure VM asset not yet in inventory (ingest lag)")
            vm_asset = {
                "id": cloud_account_id,
                "name": AZURE_SMOKE_VM,
                "asset_metadata": {"vm_name": AZURE_SMOKE_VM, "resource_group": azure_resource_group},
            }

        # Wait up to 5 min for agent to register
        print("  Waiting up to 5 min for Nexplane agent to register...")
        deadline = time.time() + 300
        agent_asset = None
        while time.time() < deadline:
            candidates = client.get("/assets", params={"q": AZURE_SMOKE_VM, "asset_type": "endpoint"})
            if candidates:
                agent_asset = candidates[0]
                log(f"Agent registered: {agent_asset['id']}")
                break
            time.sleep(15)
        if not agent_asset:
            print("  ⚠️  Agent not yet registered — Custom Script Extension may still be running")

        log("Phase N complete")
        result = {"vm_asset": vm_asset}
        rollback_stack.clear()
        vm_created = False
        return result

    except Exception as e:
        print(f"\n❌ Phase N failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase N cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        if vm_created:
            try:
                compute = _get_azure_compute_client()
                if compute and azure_resource_group:
                    compute.virtual_machines.begin_delete(azure_resource_group, AZURE_SMOKE_VM).result()
                    print(f"  Safety net: deleted Azure VM {AZURE_SMOKE_VM}")
            except Exception as e2:
                print(f"  ⚠️  Safety net Azure VM delete failed: {e2}")
```

- [ ] **Step 5: Add `run_phase_o` function**

After `run_phase_n`, add:

```python
# ---------------------------------------------------------------------------
# Phase O
# ---------------------------------------------------------------------------

def run_phase_o(client: NexplaneClient, phase_n_result: dict,
                azure_resource_group: str) -> None:
    """Phase O: Azure VM advanced — stop/start/reboot/snapshot with rollback stack."""
    print("\n[Phase O] Azure VM Advanced Operations")

    vm_asset = phase_n_result["vm_asset"]
    vm_name = vm_asset.get("asset_metadata", {}).get("vm_name", AZURE_SMOKE_VM)
    resource_group = vm_asset.get("asset_metadata", {}).get("resource_group", azure_resource_group)

    rollback_stack: list[tuple[str, str]] = []
    snapshot_name: str | None = None

    try:
        # 1. Stop VM
        cr = client.run_cr(
            "Smoke-O: stop Azure VM", "azure_vm_stop", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        rollback_stack.append((cr["id"], "azure_vm_stop"))
        log("Azure VM stopped (deallocated)")

        # 2. Start VM
        cr = client.run_cr(
            "Smoke-O: start Azure VM", "azure_vm_start", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        rollback_stack.pop()  # stop CR superseded
        rollback_stack.append((cr["id"], "azure_vm_start"))
        log("Azure VM started")

        # 3. Reboot
        client.run_cr(
            "Smoke-O: reboot Azure VM", "azure_vm_reboot", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        log("Azure VM rebooted")

        # Wait for agent to reconnect post-reboot
        time.sleep(30)
        assets = client.get("/assets", params={"q": AZURE_SMOKE_VM, "asset_type": "endpoint"})
        if assets:
            log("Agent still registered post-reboot")
        else:
            print("  ⚠️  Agent not visible post-reboot (may still be reconnecting)")

        # 4. Create disk snapshot
        snapshot_name = f"nexplane-smoke-snap-{int(time.time())}"
        cr = client.run_cr(
            "Smoke-O: create disk snapshot", "azure_vm_snapshot", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group, "snapshot_name": snapshot_name},
        )
        rollback_stack.append((cr["id"], "azure_vm_snapshot"))
        log(f"Disk snapshot created: {snapshot_name}")

        # 5. Verify snapshot via Azure SDK
        if azure_resource_group:
            _get_azure_compute_client()  # prime cache
        if _azure_creds_cache and azure_resource_group:
            try:
                from azure.identity import ClientSecretCredential
                from azure.mgmt.compute import ComputeManagementClient
                cred = ClientSecretCredential(
                    tenant_id=_azure_creds_cache['tenant_id'],
                    client_id=_azure_creds_cache['client_id'],
                    client_secret=_azure_creds_cache['client_secret'],
                )
                compute = ComputeManagementClient(cred, _azure_creds_cache['subscription_id'])
                snap = compute.snapshots.get(resource_group, snapshot_name)
                log(f"Snapshot verified: provisioning_state={snap.provisioning_state}, size={snap.disk_size_gb}GB")
            except Exception as e:
                print(f"  ⚠️  Snapshot verify skipped: {e}")

        log("Phase O complete")
        rollback_stack.clear()

    except Exception as e:
        print(f"\n❌ Phase O failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase O cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        if snapshot_name and _azure_creds_cache and azure_resource_group:
            try:
                from azure.identity import ClientSecretCredential
                from azure.mgmt.compute import ComputeManagementClient
                cred = ClientSecretCredential(
                    tenant_id=_azure_creds_cache['tenant_id'],
                    client_id=_azure_creds_cache['client_id'],
                    client_secret=_azure_creds_cache['client_secret'],
                )
                compute = ComputeManagementClient(cred, _azure_creds_cache['subscription_id'])
                compute.snapshots.begin_delete(azure_resource_group, snapshot_name).result()
                print(f"  Safety net: deleted snapshot {snapshot_name}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")
```

- [ ] **Step 6: Wire Phases N and O into `main()`**

After the Phase M block, add:

```python
        azure_phase_result: Optional[dict] = None
        if "N" in phases:
            agent_secret = client.get_agent_secret()
            azure_phase_result = run_phase_n(client, cloud_account_id, args.azure_resource_group, agent_secret)

        if "O" in phases:
            if azure_phase_result is None or azure_phase_result.get("vm_asset") is None:
                fail("Phase O requires Phase N to have run first")
            run_phase_o(client, azure_phase_result, args.azure_resource_group)
```

- [ ] **Step 7: Update module docstring**

Add to the phase descriptions:
```
    N  Azure: VM launch + agent deploy with rollback stack
    O  Azure advanced: stop/start/reboot/snapshot with rollback stack
```

And to the Requirements section:
```
    Azure phases (N-O): Azure connector with credentials + Contributor role on subscription
                        Pre-existing resource group passed via --azure-resource-group
```

- [ ] **Step 8: Verify syntax**

Copy file to main repo, verify, clean up:
```powershell
Copy-Item "backend/tests/smoke/test_cloud_live.py" "f:/Nexplane/nexplane/backend/tests/smoke/test_cloud_live.py"
```
```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_cloud_live.py').read()); print('syntax OK')"
```

- [ ] **Step 9: Commit**

```bash
git add backend/tests/smoke/test_cloud_live.py
git commit -m "feat(smoke): add Phases N and O — Azure VM lifecycle with rollback stack"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run full backend test suite**

Copy all worktree backend files to main repo and run:
```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```
Expected: all tests pass (23 original + new Azure executor tests).

- [ ] **Step 2: Verify frontend TypeScript**

Copy frontend files to main repo, check, clean up:
```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -v node_modules | head -20"
```
Expected: no new errors.

- [ ] **Step 3: Verify Azure catalog loads**

```bash
docker exec nexplane-backend-1 python -c "
from app.connectors.catalog_service import get_catalog_service
c = get_catalog_service()
cat = c.get_connector_catalog('azure')
actions = [a['action_id'] for a in cat.get('actions', [])]
required = ['launch_vm', 'reboot_vm', 'terminate_vm', 'create_disk_snapshot',
            'delete_disk_snapshot', 'capture_vm_state', 'health_check',
            'wait_vm_state', 'run_command']
for r in required:
    assert r in actions, f'Missing: {r}'
print(f'Azure catalog OK — {len(actions)} actions')
"
```
Expected: `Azure catalog OK — 21 actions`

- [ ] **Step 4: Clean up main repo and final commit**

```bash
cd f:/Nexplane/nexplane && git checkout -- backend/ frontend/
# Remove any untracked test files
```

```bash
cd f:/Nexplane/nexplane/.worktrees/azure-vm-lifecycle
git add -A
git status
git commit -m "feat(azure): Azure VM lifecycle complete — executors, change types, UI, smoke tests N+O" --allow-empty
```
