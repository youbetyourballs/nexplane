# Azure VM Lifecycle — Sub-project A Design

**Date:** 2026-05-05
**Status:** Approved for implementation

---

## Goal

Build Azure VM lifecycle feature parity with the existing AWS EC2 and GCP GCE stacks: launch, stop, start, reboot, delete, disk snapshot, and ad-hoc command execution — with three connection modes, real Azure API calls replacing the current hardcoded discovery mocks, connector-aware Quick Actions UI, and live smoke test phases N and O using the rollback stack pattern.

This is Sub-project A of a seven-part Azure parity initiative:
- **A: VM Lifecycle** (this spec)
- B: Networking (NSGs)
- C: Blob Storage
- D: IAM (Entra ID)
- E: Azure DNS
- F: Azure SQL
- G: Azure Monitor

---

## Architecture

Azure VMs are discovered as `server` assets via the existing `discover_vms` executor (currently returning hardcoded mock data — fixed in this sub-project to use `ComputeManagementClient`). A new `launch_vm` executor creates VMs with one of three connection modes. All lifecycle operations target the asset by `vm_name` and `resource_group`. The Nexplane agent — bootstrapped via Custom Script Extension — provides the full command channel for hardening, patching, and compliance. Azure Run Command provides SSM-equivalent ad-hoc execution on existing VMs.

The `connectorType: "azure"` field on all Quick Actions entries ensures Azure actions only appear on Azure-sourced assets, consistent with the `connectorType: "aws"` and `connectorType: "gcp"` pattern already in place.

---

## Azure ↔ AWS/GCP Mapping

| AWS | GCP | Azure | Status |
|-----|-----|-------|--------|
| `ec2_launch` | `gce_instance_create` | `azure_vm_create` | New |
| `ec2_stop` | `gce_stop` | `azure_vm_stop` | Exists (deallocate_vm — fix real API + rollback) |
| `ec2_start` | `gce_start` | `azure_vm_start` | Exists (start_vm — confirm real API + rollback) |
| `ec2_reboot` | `gce_instance_reboot` | `azure_vm_reboot` | New |
| `ec2_terminate` | `gce_instance_delete` | `azure_vm_delete` | New |
| `snapshot_asset` | `gce_disk_snapshot` | `azure_vm_snapshot` | New |
| `ssm_command` | — | `azure_run_command` | New |
| `capture_instance_state` | `capture_instance_state` | `capture_vm_state` | New |
| `health_check` | `health_check` | `health_check` | New |
| `wait_instance_state` | `wait_instance_state` | `wait_vm_state` | New |
| `discover_ec2_instances` | `discover_compute_instances` | `discover_vms` | Fix (currently hardcoded mock) |

---

## Connection Modes

The `azure_vm_create` change type accepts a `connection_mode` parameter:

| Mode | Mechanism | When to use |
|------|-----------|-------------|
| `agent_extension` | Custom Script Extension runs on first boot, installs Nexplane agent via systemd. No inbound ports needed. | Default. Best for VMs Nexplane provisions itself. |
| `ssh` | Injects SSH public key into VM metadata at creation. | Linux VMs needing SSH access. |
| `password` | Sets admin password at creation. | Windows VMs or environments without key-based SSH. |

Azure Run Command (`azure_run_command` change type) provides ad-hoc command execution on existing VMs via the Azure control plane — no SSH or agent required. Equivalent to `ssm_command` on AWS.

---

## Executors

All in `backend/app/connectors/executors/azure/`. All use `asyncio.get_running_loop()` and the mock-when-no-credentials pattern. The `_client.py` helper provides `get_credential(creds)` returning `ClientSecretCredential`.

### New Executors

| File | Purpose | Rollback |
|------|---------|---------|
| `launch_vm.py` | Create Azure VM. Accepts `vm_name`, `resource_group`, `location`, `vm_size`, `image`, `connection_mode`, `nexplane_url`, `nexplane_secret`, `ssh_public_key`, `admin_password`, `network_security_group`. Custom Script Extension used for `agent_extension` mode. Emits `_auto_asset` (server). | `terminate_vm` |
| `reboot_vm.py` | Restart VM via `virtual_machines.begin_restart()` | `rollback_unavailable` |
| `terminate_vm.py` | Delete VM via `virtual_machines.begin_delete()` | `rollback_unavailable` |
| `create_disk_snapshot.py` | Snapshot OS disk via `snapshots.begin_create_or_update()`. Returns `snapshot_name`, `resource_group`, `disk_name`. | `delete_disk_snapshot` |
| `delete_disk_snapshot.py` | Delete snapshot via `snapshots.begin_delete()` | `rollback_unavailable` |
| `capture_vm_state.py` | Record VM size, location, tags, OS disk name. Read-only preflight. | `rollback_unavailable` |
| `health_check.py` | Verify `instance_view.statuses` contains `PowerState/running`. Raises `RuntimeError` if not running. | `rollback_unavailable` |
| `wait_vm_state.py` | Poll VM power state until target reached (default `running`). 300s timeout, 10s poll interval. | `rollback_unavailable` |
| `run_command.py` | Execute shell script on VM via `virtual_machines.begin_run_command()`. Returns stdout/stderr. | `rollback_unavailable` |

### Updated Executors

- `discover_vms.py` — Remove `_VMS` hardcoded list. Use `ComputeManagementClient.virtual_machines.list_all()` in real path. Emit one `_auto_asset` per VM with `vm_name`, `resource_group`, `location`, `vm_size`, `power_state`, `os_type`, `provider: "azure"`. Mock path returns `[]`.
- `deallocate_vm.py` — Add rollback calling `start_vm.execute`. Confirm real API path uses `virtual_machines.begin_deallocate()`.
- `start_vm.py` — Add rollback calling `deallocate_vm.execute`. Confirm real API path uses `virtual_machines.begin_start()`.

### `_auto_asset` on Launch

```python
{
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
```

---

## Change Type Definitions

All in `backend/app/connectors/change_type_definitions/`:

| File | Steps | Rollback action |
|------|-------|----------------|
| `azure_vm_create.json` | `launch_vm` → `wait_vm_state` → `health_check` | `terminate_vm` |
| `azure_vm_stop.json` | `capture_vm_state` → `deallocate_vm` → `wait_vm_state` | `start_vm` |
| `azure_vm_start.json` | `start_vm` → `wait_vm_state` → `health_check` | `deallocate_vm` |
| `azure_vm_reboot.json` | `reboot_vm` → `wait_vm_state` → `health_check` | `rollback_unavailable` |
| `azure_vm_delete.json` | `capture_vm_state` → `terminate_vm` | `rollback_unavailable` |
| `azure_vm_snapshot.json` | `capture_vm_state` → `create_disk_snapshot` | `delete_disk_snapshot` |
| `azure_run_command.json` | `run_command` | `rollback_unavailable` |

---

## ChangeType Enum + Migration

New values added to `backend/app/models/change_request.py`:

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

New Alembic migration `025_add_azure_vm_change_types.py` (down_revision=024) using `ALTER TYPE change_type ADD VALUE IF NOT EXISTS` for all 7 values.

---

## Azure Catalog Entries

`backend/app/connectors/catalog/azure.json` gains 9 new action entries:
`launch_vm`, `reboot_vm`, `terminate_vm`, `create_disk_snapshot`, `delete_disk_snapshot`, `capture_vm_state`, `health_check`, `wait_vm_state`, `run_command`.

Existing `deallocate_vm` and `start_vm` entries gain `rollback_action` fields.

---

## Smoke Test

`backend/tests/smoke/test_cloud_live.py` — two new phases. Same rollback stack pattern as Phases L/M: `rollback_stack.clear()` before return on success, `finally` only fires on error path.

New CLI flag: `--azure-resource-group <name>` (required when running Azure phases). A `_get_azure_compute_client()` helper mirrors `_get_gcp_compute_client()`.

### Phase N — Azure VM Launch + Agent Deploy

```
1. azure_vm_create CR:
     Standard_B1s, Ubuntu 22.04 LTS, eastus,
     connection_mode=agent_extension
   → push to rollback_stack; set vm_created=True

2. Verify server asset in inventory (vm_name + resource_group)

3. Wait up to 5 min for Nexplane agent to register as endpoint asset
   (polls /assets?asset_type=endpoint&q=<vm_name>)

4. Log agent asset ID if registered; warn if not yet

Success path: rollback_stack.clear(); vm_created=False; return result

Finally (error path only):
  reversed(rollback_stack) → client.rollback_cr(cr_id, label)
  Safety net: Azure SDK virtual_machines.begin_delete() if vm_created
```

### Phase O — Azure VM Advanced Operations

```
Requires Phase N result (instance_asset with vm_name + resource_group)

1. azure_vm_stop CR → push rollback_stack
2. azure_vm_start CR → pop stop, push start
3. azure_vm_reboot CR (no rollback push — rollback_unavailable)
4. Sleep 30s; verify agent still registered in inventory
5. azure_vm_snapshot CR → push rollback_stack; record snapshot_name
6. Verify snapshot via Azure SDK snapshots.get()

Success path: rollback_stack.clear(); return

Finally (error path only):
  reversed(rollback_stack) → client.rollback_cr(cr_id, label)
  Safety net: Azure SDK snapshots.begin_delete() if snapshot_name set
```

### Docstring update

Phases N and O added to module docstring. `--azure-resource-group` added to CLI.

---

## UI Changes

### `frontend/src/types/api.ts`

Add 7 new `ChangeType` literals:
```typescript
| "azure_vm_create"
| "azure_vm_stop"
| "azure_vm_start"
| "azure_vm_reboot"
| "azure_vm_delete"
| "azure_vm_snapshot"
| "azure_run_command"
```

### `frontend/src/pages/CreateChangeRequest.tsx`

New group after "GCE Instances":
```typescript
{ label: "Azure VMs", types: ["azure_vm_create", "azure_vm_stop", "azure_vm_start",
                               "azure_vm_reboot", "azure_vm_delete", "azure_vm_snapshot",
                               "azure_run_command"] }
```

Asset filters:
```typescript
azure_vm_create: "cloud_account",
azure_vm_stop: "server",
azure_vm_start: "server",
azure_vm_reboot: "server",
azure_vm_delete: "server",
azure_vm_snapshot: "server",
azure_run_command: "server",
```

Outcome templates with `rollback_strategy` for all 7 types. `azure_vm_create` pre-fills: `vm_name: ""`, `resource_group: ""`, `location: "eastus"`, `vm_size: "Standard_B1s"`, `image: "Ubuntu2204"`, `connection_mode: "agent_extension"`, `rollback_strategy: "terminate_vm"`.

Pre-fill logic extended to inject `vm_name` and `resource_group` from `asset_metadata` when template has those keys (same pattern as `instance_name`/`zone` for GCE).

### `frontend/src/pages/AssetDetail.tsx`

New `server` Quick Actions with `connectorType: "azure"`:
```typescript
{ changeType: "azure_vm_stop",     label: "Stop VM",             connectorType: "azure" }
{ changeType: "azure_vm_start",    label: "Start VM",            connectorType: "azure" }
{ changeType: "azure_vm_reboot",   label: "Reboot VM",           connectorType: "azure" }
{ changeType: "azure_vm_snapshot", label: "Create Disk Snapshot", connectorType: "azure" }
{ changeType: "azure_vm_delete",   label: "Delete VM",           connectorType: "azure" }
{ changeType: "azure_run_command", label: "Run Command",          connectorType: "azure" }
```

New `cloud_account` Quick Action with `connectorType: "azure"`:
```typescript
{ changeType: "azure_vm_create", label: "Launch Azure VM", connectorType: "azure" }
```

Existing `connectorType` filter in `AssetDetail.tsx` handles routing automatically.

---

## Out of Scope

- Sub-projects B–G (Networking, Storage, IAM, DNS, SQL, Monitor)
- Cleaning up existing fake Azure assets already in inventory (manual step)
- Azure Bastion / RDP connection mode
- Windows-specific agent packaging
