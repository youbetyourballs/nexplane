# GCP GKE Cluster Lifecycle Design

## Overview

Add Google Kubernetes Engine (GKE) support to Nexplane: full cluster lifecycle (create, delete) plus day-2 operations (add/delete node pool, scale, update, get kubeconfig). Seven executors, one new client factory addition, one new helper module, seven catalog entries, unit tests, and a live smoke test.

This is the first spec in the GCP parity series. The goal is cloud-agnostic completeness: a Nexplane operator should have no meaningful distinction between managing GKE, EKS, and OKE clusters.

---

## Scope

### Executors

| Executor module | Catalog action ID | Description |
|---|---|---|
| `gcp_create_gke_cluster.py` | `gcp_create_gke_cluster` | Create cluster + initial node pool (bundled) |
| `gcp_delete_gke_cluster.py` | `gcp_delete_gke_cluster` | Delete all node pools, then delete cluster |
| `gcp_add_gke_node_pool.py` | `gcp_add_gke_node_pool` | Add a node pool to an existing cluster |
| `gcp_delete_gke_node_pool.py` | `gcp_delete_gke_node_pool` | Delete node pool with config capture + drain advisory |
| `gcp_scale_gke_node_pool.py` | `gcp_scale_gke_node_pool` | Change node count |
| `gcp_update_gke_node_pool.py` | `gcp_update_gke_node_pool` | Change node pool display name or labels |
| `gcp_get_gke_kubeconfig.py` | `gcp_get_gke_kubeconfig` | Retrieve cluster kubeconfig as YAML string |

All files live in `backend/app/connectors/executors/gcp/`.

### Out of scope

- Worker node SSH access or OS-level configuration
- Cluster add-ons (e.g., network policy, Workload Identity)
- Autoscaler configuration
- Cluster version upgrades
- kubectl drain before node pool deletion (backlog — same as OKE; all executors for bare-metal k8s agent need drain wired through the go agent)

---

## Architecture

### Client factory

Add `get_container_client(creds)` to `backend/app/connectors/executors/gcp/_client.py`:

```python
def get_container_client(creds: dict):
    """Return google.cloud.container_v1.ClusterManagerClient authenticated with stored credentials."""
    from google.cloud import container_v1
    credentials = get_credentials(creds)
    return container_v1.ClusterManagerClient(credentials=credentials)
```

`get_credentials` already exists and is used by all other GCP client factories.

### Operation polling helper

New file: `backend/app/connectors/executors/gcp/_gke_helpers.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def poll_gke_operation(client, op_name: str, timeout: int) -> None:
    """Poll a GKE operation by name until DONE. Raises on failure or timeout."""
    from google.cloud import container_v1
    loop = asyncio.get_event_loop()
    deadline = time.time() + timeout
    while time.time() < deadline:
        op = await loop.run_in_executor(None, lambda: client.get_operation({"name": op_name}))
        if op.status == container_v1.Operation.Status.DONE:
            if op.status_message:
                raise RuntimeError(f"GKE operation failed: {op.status_message}")
            return
        await asyncio.sleep(15)
    raise TimeoutError(f"GKE operation {op_name} did not complete within {timeout}s")
```

### Executor pattern

All executors follow the established GCP pattern:

```python
ROLLBACK_CAPABILITY = "full"  # or "irreversible"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    ...

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    ...
```

- GCP imports deferred inside function bodies (not module-level)
- Async SDK calls use `await loop.run_in_executor(None, lambda: ...)`
- `loop = asyncio.get_event_loop()` (matches existing GCP executor pattern)
- Credentials absent → return mock result with `"mock": True`
- PreStateStore captured **before** any destructive SDK call

### Rollback capabilities

| Executor | `ROLLBACK_CAPABILITY` | Rollback strategy |
|---|---|---|
| `gcp_create_gke_cluster` | `full` | Delete node pool (poll to DONE), then delete cluster (poll to DONE) |
| `gcp_delete_gke_cluster` | `irreversible` | No reconstitution path |
| `gcp_add_gke_node_pool` | `full` | Delete the pool that was created |
| `gcp_delete_gke_node_pool` | `full` | Recreate pool from PreStateStore config |
| `gcp_scale_gke_node_pool` | `full` | Restore previous count from PreStateStore |
| `gcp_update_gke_node_pool` | `full` | Restore previous config from PreStateStore |
| `gcp_get_gke_kubeconfig` | `full` | No-op (read-only) |

`gcp_delete_gke_cluster` sets `ROLLBACK_CAPABILITY = "irreversible"` and includes `ROLLBACK_REASON = "Deleted GKE cluster cannot be reconstituted; VPC, subnets, and workloads must be reprovisioned."`. The executor handles node pool deletion internally before cluster deletion.

### Bundled create

`gcp_create_gke_cluster` creates both the cluster and an initial node pool in a single executor.

Polling strategy:
1. Call `create_cluster` → get operation name → `poll_gke_operation` up to 20 min
2. Call `create_node_pool` → get operation name → `poll_gke_operation` up to 15 min

Execute result contains: `cluster_name`, `location`, `node_pool_name`, `project_id`.

Rollback:
1. Delete initial node pool → `poll_gke_operation` up to 10 min
2. Delete cluster → `poll_gke_operation` up to 20 min

### Node pool deletion drain advisory

`gcp_delete_gke_node_pool` captures the full node pool config in PreStateStore, then checks `node_pool.status` and the count of `node_pool.instance_group_urls` before deleting. If the pool has active instance groups, execute result includes `"drain_recommended": true, "active_node_count": N`. The executor does not block or attempt kubectl drain. Rollback recreates the pool from captured PreStateStore config.

### GKE operation name format

GKE operations are identified by a full resource path:
```
projects/{project}/locations/{location}/operations/{operation_id}
```
This full path is passed to `get_operation({"name": op_name})`. The `location` used for cluster operations is the cluster's zone or region.

### Kubeconfig generation

`gcp_get_gke_kubeconfig` calls `get_cluster()`, extracts `cluster.endpoint` and `cluster.master_auth.cluster_ca_certificate` (base64-encoded CA cert), then generates a short-lived access token from the service account credentials. Returns a kubeconfig YAML string that requires no `gcloud` dependency on the client:

```yaml
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {base64_ca}
    server: https://{endpoint}
  name: {cluster_name}
contexts:
- context:
    cluster: {cluster_name}
    user: {cluster_name}
  name: {cluster_name}
current-context: {cluster_name}
kind: Config
preferences: {}
users:
- name: {cluster_name}
  user:
    token: {access_token}
```

---

## Parameters

### `gcp_create_gke_cluster`

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `cluster_name` | string | yes | — | Cluster display name |
| `location` | string | yes | — | Zone (`us-central1-a`) or region (`us-central1`); regional = HA |
| `node_count` | integer | no | 1 | Initial node pool size |
| `machine_type` | string | no | `e2-medium` | Node machine type |
| `disk_size_gb` | integer | no | 100 | Boot disk size |
| `network` | string | no | `default` | VPC network name |
| `subnetwork` | string | no | — | Subnetwork name; omitted = GCP default |

### `gcp_add_gke_node_pool`

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `cluster_name` | string | yes | — |
| `location` | string | yes | — |
| `node_pool_name` | string | yes | — |
| `node_count` | integer | no (default 1) | — |
| `machine_type` | string | no (default `e2-medium`) | — |

### `gcp_delete_gke_node_pool` / `gcp_scale_gke_node_pool` / `gcp_update_gke_node_pool`

All require `cluster_name`, `location`, `node_pool_name`. Scale also requires `node_count`. Update accepts `display_name` and/or `labels` (dict).

### `gcp_delete_gke_cluster`

Requires `cluster_name`, `location`. Handles remaining node pools internally.

### `gcp_get_gke_kubeconfig`

Requires `cluster_name`, `location`.

---

## Catalog entries (`backend/app/connectors/catalog/gcp.json`)

Seven new entries appended to the `"actions"` array. Pattern follows existing GCP catalog shape. Self-referential `rollback_action` for executors where rollback logic lives in the executor's own `rollback()` function:

```json
{
  "action_id": "gcp_create_gke_cluster",
  "generic_action": "gcp_create_gke_cluster",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Create GKE Cluster",
  "description": "Create a GKE Kubernetes cluster with an initial node pool (20-35 min). Rollback: delete node pool then cluster.",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [
    {"name": "cluster_name", "type": "string", "required": true},
    {"name": "location", "type": "string", "required": true},
    {"name": "node_count", "type": "integer", "required": false, "default": 1},
    {"name": "machine_type", "type": "string", "required": false, "default": "e2-medium"},
    {"name": "disk_size_gb", "type": "integer", "required": false, "default": 100},
    {"name": "network", "type": "string", "required": false, "default": "default"},
    {"name": "subnetwork", "type": "string", "required": false}
  ],
  "executor": "gcp.gcp_create_gke_cluster",
  "rollback_action": "gcp_create_gke_cluster",
  "rollback_connector_type": "gcp",
  "estimated_duration_seconds": 2100
}
```

`gcp_delete_gke_cluster` has no `rollback_action` (irreversible).
`gcp_get_gke_kubeconfig` has no `rollback_action` (read-only), `action_type: "read"`.

---

## Unit tests

File: `backend/tests/unit/test_gcp_gke_parity.py`

GCP SDK patched at module level using `patch.dict(sys.modules, {...})`:

```python
{
    "google": MagicMock(),
    "google.cloud": MagicMock(),
    "google.cloud.container_v1": MagicMock(),
    "google.oauth2": MagicMock(),
    "google.oauth2.service_account": MagicMock(),
}
```

Test classes and coverage:

| Class | Tests |
|---|---|
| `TestGkeClientFactory` | `get_container_client` returns client; `poll_gke_operation` raises on failure; poll raises TimeoutError; poll returns on DONE |
| `TestCreateGkeCluster` | mock path returns expected keys + `mock: True`; execute calls create_cluster + create_node_pool; rollback deletes node pool then cluster; rollback calls poll twice; `ROLLBACK_CAPABILITY == "full"` |
| `TestDeleteGkeCluster` | mock path; execute lists + deletes node pools before cluster; rollback returns `rolled_back: False`; `ROLLBACK_CAPABILITY == "irreversible"` |
| `TestAddGkeNodePool` | mock path; execute calls create_node_pool; rollback deletes pool; `ROLLBACK_CAPABILITY == "full"` |
| `TestDeleteGkeNodePool` | mock path; drain_recommended true when instance groups present; drain_recommended false when none; PreStateStore captured before delete; rollback recreates pool; `ROLLBACK_CAPABILITY == "full"` |
| `TestScaleGkeNodePool` | mock path; execute captures prior count; rollback restores count; `ROLLBACK_CAPABILITY == "full"` |
| `TestUpdateGkeNodePool` | mock path; execute captures prior name/labels; rollback restores; `ROLLBACK_CAPABILITY == "full"` |
| `TestGetGkeKubeconfig` | mock path returns kubeconfig string; execute builds valid YAML; rollback returns `rolled_back: False, reason: "read-only"`; `ROLLBACK_CAPABILITY == "full"` |

Target: ~35 tests across 8 test classes.

---

## Smoke test

File: `backend/tests/smoke/test_gcp_gke_smoke.py`

Single test class `TestGkeLifecycle`. All steps in one test method to share cluster state.

`smoke_helpers.py` gets a new `_get_gcp_container_client()` helper following the pattern of `_get_gcp_compute_client()`.

### Flow

```
1. CREATE cluster + initial node pool (bundled CR)
   - Poll until CR completed (up to 2400s)
   - Assert cluster_name, node_pool_name in result

2. GET kubeconfig
   - Assert kubeconfig YAML returned (non-empty string, contains "apiVersion")

3. SCALE node pool up by 1
   - Assert scaled: true in result
   - ROLLBACK scale → assert rolled_back: true, node count restored

4. UPDATE node pool (add freeform label)
   - Assert updated: true in result
   - ROLLBACK update → assert rolled_back: true, config restored

5. ADD second node pool (1 node)
   - Assert node_pool_name in result

6. DELETE second node pool
   - Assert deleted: true in result
   - Assert drain_recommended key present in result

7. DELETE cluster (explicit delete CR)
   - Handles remaining first node pool internally
   - Assert deleted: true in result
```

### xfail conditions

- `RESOURCE_EXHAUSTED` or quota errors on create → `pytest.xfail("GKE quota exceeded on this project. Executor code is correct. Re-run against a project with GKE quota.")`
- Missing GCP project credentials → `pytest.skip`

### Environment / parameters

Location read from `_get_gcp_creds()` dict key `gke_location` or env `GCP_GKE_LOCATION` (default `us-central1`). Project ID from creds as usual.

---

## Ordering constraints

1. `_client.py` update + `_gke_helpers.py` — Task 1, no dependencies
2. Executor files — Task 2 (create/delete cluster), Task 3 (add/delete/scale/update node pool), Task 4 (get kubeconfig)
3. Catalog entries — Task 5, depends on executor names
4. Unit tests — Task 6, depends on Tasks 1–4
5. Smoke test — Task 7, depends on Tasks 1–6

---

## Rollback challenges and mitigations

**Cluster create rollback timing**: Cluster deletion after a failed/rolled-back create takes 5–15 min. Rollback polls until cluster is GONE (operation DONE) before returning.

**Node pool delete rollback (recreate)**: Recreating a node pool from PreStateStore config is a full provisioning call (~10 min). Rollback timeout for `gcp_delete_gke_node_pool` set to 900s.

**Irreversible cluster delete**: Platform surfaces `ROLLBACK_CAPABILITY = "irreversible"` before approval, consistent with how other irreversible CRs are presented.

**GKE operation polling**: GKE operations are identified by a full resource-path name (not just an ID). `poll_gke_operation` always receives the full path returned by the mutating call.
