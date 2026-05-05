# GCP Instance Lifecycle — Sub-project A Design

**Date:** 2026-05-05  
**Status:** Approved for implementation

---

## Goal

Build GCP Compute Engine instance lifecycle feature parity with the existing AWS EC2 stack: launch, stop, start, reboot, delete, and disk snapshot — with all three Nexplane connection modes (agent startup script, IAP, SSH), a connector-aware Quick Actions UI, and live smoke test phases L and M using the rollback stack pattern.

This is Sub-project A of a seven-part GCP parity initiative (A: Instance Lifecycle, B: Networking, C: Storage, D: IAM, E: Cloud DNS, F: Cloud SQL, G: Cloud Monitoring).

---

## Architecture

GCP Compute Engine instances are discovered as `server` assets via the existing `discover_compute_instances` executor. A new `launch_instance` executor creates instances with one of three connection modes baked in at launch. All subsequent lifecycle operations (stop, start, reboot, snapshot, delete) target the asset by its `instance_id` and `zone`. The Nexplane agent — once deployed via startup script — provides the full command channel used by agent hardening, patching, and compliance commands.

The connector-aware Quick Actions fix ships as part of this sub-project: `ASSET_ACTIONS` entries gain an optional `connectorType` field, and the Quick Actions panel filters by the asset's connector type. This prevents AWS and GCP stop/start/snapshot actions from appearing together on the same asset.

---

## GCP ↔ AWS Mapping

| AWS | GCP Equivalent | Status |
|-----|---------------|--------|
| `ec2_launch` | `gce_instance_create` | New |
| `ec2_stop` | `gce_stop` | New change type (executor exists) |
| `ec2_start` | `gce_start` | New change type (executor exists) |
| `ec2_reboot` | `gce_instance_reboot` | New |
| `ec2_terminate` | `gce_instance_delete` | New change type (executor exists) |
| `snapshot_asset` (EBS) | `gce_disk_snapshot` | New |
| `ssm_command` | Agent channel (post-deploy) | Agent already cross-platform |
| `capture_instance_state` | `gce_capture_instance_state` | New executor |

Security Command Center is excluded — requires GCP organization, not available on personal accounts.

---

## Connection Modes

The `gce_instance_create` change type accepts a `connection_mode` parameter:

| Mode | Mechanism | When to use |
|------|-----------|-------------|
| `agent_startup` | Startup script installs Nexplane agent on first boot. Agent phones home — no inbound ports required. | Default. Best for instances Nexplane provisions itself. |
| `iap` | Adds `allow-iap-ssh` network tag. Operator uses IAP TCP tunneling for SSH. Requires `roles/iap.tunnelResourceAccessor` on the service account. | Instances needing SSH without public IPs. |
| `ssh` | Injects SSH public key into instance metadata (project-level or instance-level OS Login). | Legacy environments where IAP is not configured. |

All three modes can coexist on an instance. Smoke test covers `agent_startup` only.

---

## New Executors

All in `backend/app/connectors/executors/gcp/`.

| File | Purpose | Rollback action |
|------|---------|----------------|
| `launch_instance.py` | Create GCP Compute Engine instance. Accepts `name`, `machine_type`, `zone`, `image_family`, `image_project`, `connection_mode`, `startup_script` (override), `ssh_public_key` (ssh mode), `network_tags`, `labels`. Emits `_auto_asset` (server). | `delete_instance` |
| `reboot_instance.py` | Reset a running instance via `instances().reset()`. | `rollback_unavailable` |
| `create_disk_snapshot.py` | Snapshot the boot disk of an instance. Returns `snapshot_name`, `disk_name`, `zone`. | `delete_disk_snapshot` |
| `delete_disk_snapshot.py` | Delete a disk snapshot by name. | `rollback_unavailable` |
| `capture_instance_state.py` | Record instance labels, machine type, zone, disk config, network tags to execution result. Used as preflight capture before mutations. | `rollback_unavailable` |
| `health_check.py` | Verify instance status is RUNNING via `instances().get()`. Fails CR if not running after timeout. | `rollback_unavailable` |
| `wait_instance_state.py` | Poll `instances().get()` until instance reaches target state (`RUNNING`, `TERMINATED`, `STAGING`). Used inside launch pipeline. | `rollback_unavailable` |

**Updated executors** — add rollback functions:
- `stop_instance.py` — rollback calls `start_instance`
- `start_instance.py` — rollback calls `stop_instance`
- `delete_instance.py` — rollback: `rollback_unavailable` (terminal)

All executors follow the established pattern: mock path when `not creds`, real path via `google-cloud-compute` SDK, async via `loop.run_in_executor`.

---

## New Change Type Definitions

In `backend/app/connectors/change_type_definitions/`:

| File | Steps |
|------|-------|
| `gce_instance_create.json` | `capture_instance_state` → `launch_instance` → `wait_instance_state` → `health_check` |
| `gce_stop.json` | `stop_instance` |
| `gce_start.json` | `start_instance` |
| `gce_instance_reboot.json` | `reboot_instance` |
| `gce_instance_delete.json` | `delete_instance` |
| `gce_disk_snapshot.json` | `capture_instance_state` → `create_disk_snapshot` |

Rollback actions specified in each JSON match the executor rollback table above.

---

## New ChangeType Enum Values

Added to `backend/app/models/change_request.py` and a new Alembic migration:

```
gce_instance_create
gce_stop
gce_start
gce_instance_reboot
gce_instance_delete
gce_disk_snapshot
```

---

## New Catalog Entries

In `backend/app/connectors/catalog/gcp.json`, new action entries for:
`launch_instance`, `reboot_instance`, `create_disk_snapshot`, `delete_disk_snapshot`, `capture_instance_state`, `health_check`, `wait_instance_state`, plus change-type entries for all six new change types.

Existing entries for `stop_instance`, `start_instance`, `delete_instance` gain `rollback_action` fields.

---

## `_auto_asset` on Launch

`launch_instance` returns an `_auto_asset` payload that creates a `server` asset:

```python
{
    "name": instance_name,
    "asset_type": "server",
    "environment": "prod",
    "criticality": "medium",
    "asset_metadata": {
        "instance_id": instance_id,
        "zone": zone,
        "machine_type": machine_type,
        "internal_ip": internal_ip,
        "connection_mode": connection_mode,
        "image_family": image_family,
        "provider": "gcp",
    },
    "tags": ["gce", "nexplane-managed"],
}
```

---

## Smoke Test

Test file: `backend/tests/smoke/test_aws_live.py` renamed to `backend/tests/smoke/test_cloud_live.py`. AWS phases A–K are unchanged. GCP phases added after K.

New CLI flag: `--gcp-project <project-id>` (required when running GCP phases).

A `_get_gcp_compute_client()` helper mirrors `_get_aws_boto3_client()` — loads GCP credentials from the DB, returns a `google.cloud.compute_v1` client.

### Phase L — GCP Instance Launch + Agent Deploy

```
1. Run gce_instance_create CR:
     machine_type=e2-micro, image_family=ubuntu-2204-lts,
     image_project=ubuntu-os-cloud, zone=us-central1-a,
     connection_mode=agent_startup
   → push CR to rollback_stack

2. Verify server asset in inventory (instance_id + zone present)

3. Wait up to 5 min for Nexplane agent to register as endpoint asset
   (polls /assets?asset_type=endpoint&q=<instance_name>)

4. Log agent asset ID if registered; warn if not (may still be starting)

Cleanup (finally):
  - Rollback stack (reverse): gce_instance_create → delete_instance
  - Safety net: GCP SDK instances().delete() if instance still exists
```

### Phase M — GCP Instance Advanced Operations

```
Requires Phase L result (instance_asset, instance_id, zone)

1. gce_stop CR → push to rollback_stack
2. gce_start CR → pop stop CR, push start CR
3. gce_instance_reboot CR (no rollback entry — rollback_unavailable)
4. Sleep 30s, verify agent still registered in inventory
5. gce_disk_snapshot CR → push to rollback_stack
6. Verify snapshot exists via GCP SDK compute.snapshots().get()

Cleanup (finally):
  - Rollback stack (reverse):
      gce_disk_snapshot → delete_disk_snapshot
      gce_start → stop_instance (harmless if instance already deleted)
  - Safety net: GCP SDK snapshots().delete() + instances().delete()
```

Both phases follow the exact rollback stack pattern established in AWS Phases E–K: `rollback_stack: list[tuple[str, str]]`, `finally` block iterates `reversed(rollback_stack)` calling `client.rollback_cr(cr_id, label)`, followed by GCP SDK safety net calls.

---

## UI Changes

### `frontend/src/types/api.ts`

Add 6 new ChangeType literals:
```typescript
| "gce_instance_create"
| "gce_stop"
| "gce_start"
| "gce_instance_reboot"
| "gce_instance_delete"
| "gce_disk_snapshot"
```

### `frontend/src/pages/CreateChangeRequest.tsx`

New group after the EC2 group in `CHANGE_TYPE_GROUPS`:
```typescript
{ label: "GCE Instances", types: ["gce_instance_create", "gce_stop", "gce_start",
                                    "gce_instance_reboot", "gce_instance_delete", "gce_disk_snapshot"] }
```

Asset filters:
```typescript
gce_instance_create: "cloud_account",
gce_stop:            "server",
gce_start:           "server",
gce_instance_reboot: "server",
gce_instance_delete: "server",
gce_disk_snapshot:   "server",
```

Outcome template for `gce_instance_create`:
```typescript
{
  name: "",
  machine_type: "e2-micro",
  zone: "us-central1-a",
  image_family: "ubuntu-2204-lts",
  image_project: "ubuntu-os-cloud",
  connection_mode: "agent_startup",
  rollback_strategy: "delete_instance",
}
```

### `frontend/src/pages/AssetDetail.tsx`

**Connector-aware Quick Actions fix (required, ships with this sub-project):**

Add optional `connectorType` field to the action entry type:
```typescript
interface QuickAction {
  changeType: ChangeType;
  label: string;
  title: (a: Asset) => string;
  description: (a: Asset) => string;
  connectorType?: string;  // if set, only shown when asset.connector_type matches
}
```

The Quick Actions panel resolves `asset.connector_id → connector_type` (available on the asset object from the API) and filters: show action if `action.connectorType` is undefined OR matches asset's connector type.

**Existing AWS server actions** get `connectorType: "aws"`:
```typescript
{ changeType: "ec2_stop",            connectorType: "aws", ... }
{ changeType: "ec2_start",           connectorType: "aws", ... }
{ changeType: "ec2_reboot",          connectorType: "aws", ... }  // via ssm_command
{ changeType: "snapshot_asset",      connectorType: "aws", ... }
{ changeType: "ec2_terminate",       connectorType: "aws", ... }
{ changeType: "cloudwatch_alarm_create", connectorType: "aws", ... }
{ changeType: "security_group_update",   connectorType: "aws", ... }
```

**New GCP server actions** with `connectorType: "gcp"`:
```typescript
{ changeType: "gce_stop",            label: "Stop Instance",          connectorType: "gcp", ... }
{ changeType: "gce_start",           label: "Start Instance",         connectorType: "gcp", ... }
{ changeType: "gce_instance_reboot", label: "Reboot Instance",        connectorType: "gcp", ... }
{ changeType: "gce_disk_snapshot",   label: "Create Disk Snapshot",   connectorType: "gcp", ... }
{ changeType: "gce_instance_delete", label: "Delete Instance",        connectorType: "gcp", ... }
```

**`cloud_account` actions** — add with `connectorType: "gcp"`:
```typescript
{ changeType: "gce_instance_create", label: "Launch GCE Instance", connectorType: "gcp", ... }
```

Connector-agnostic actions (no `connectorType`): `deploy_nexplane_agent`, `key_rotation`, `patch_packages`, `ssm_command`.

---

## Testing

- Backend unit tests: mock executor tests for each new GCP executor (mock path when `not creds`)
- Live smoke test: Phases L and M via `test_cloud_live.py --phases L,M --gcp-project <id>`
- TypeScript: `npx tsc --noEmit` after UI changes

---

## Out of Scope

- Security Command Center (requires GCP org)
- IAP and SSH connection modes are implemented in executor but not smoke-tested
- Sub-projects B–G (Networking, Storage, IAM, Cloud DNS, Cloud SQL, Cloud Monitoring)
