# OCI Parity Spec 2 — OKE Cluster Lifecycle Design

## Overview

Add OCI Kubernetes Engine (OKE) support to Nexplane: full cluster lifecycle (create, delete) plus day-2 operations (add/delete node pool, scale, update, get kubeconfig). Seven executors, one new client factory, seven catalog entries, unit tests, and a live smoke test.

This is the second of two OCI parity specs. Spec 1 closed OCIR, block volume restore, and resource tagging. Data Guard / ADB HA is a separate follow-on spec.

---

## Scope

### Executors

| Executor module | Catalog action ID | Description |
|---|---|---|
| `oci_create_oke_cluster.py` | `oci_create_oke_cluster` | Create cluster + initial node pool (bundled) |
| `oci_delete_oke_cluster.py` | `oci_delete_oke_cluster` | Delete all node pools, then delete cluster |
| `oci_add_oke_node_pool.py` | `oci_add_oke_node_pool` | Add a node pool to an existing cluster |
| `oci_delete_oke_node_pool.py` | `oci_delete_oke_node_pool` | Delete node pool with config capture + drain advisory |
| `oci_scale_oke_node_pool.py` | `oci_scale_oke_node_pool` | Change node count |
| `oci_update_oke_node_pool.py` | `oci_update_oke_node_pool` | Change node pool display name, labels, or shape |
| `oci_get_oke_kubeconfig.py` | `oci_get_oke_kubeconfig` | Retrieve cluster kubeconfig as YAML string |

All files live in `backend/app/connectors/executors/oci/`.

### Out of scope

- Worker node SSH access or OS-level configuration
- Cluster add-ons (e.g., Flannel CNI selection, OCI native ingress)
- Autoscaler configuration
- Cluster upgrades (Kubernetes version)
- kubectl drain before node pool deletion (backlog — needed for bare-metal k8s agent; all executors for that use case will need drain capability wired through the go agent)

---

## Architecture

### Client factory

Add `get_container_engine_client(creds)` to `backend/app/connectors/executors/oci/_client.py`:

```python
def get_container_engine_client(creds: dict):
    import oci
    config = _build_config(creds)
    return oci.container_engine.ContainerEngineClient(config)
```

`_build_config` already exists and is used by all other OCI client factories.

### Executor pattern

All executors follow the established OCI pattern:

```python
ROLLBACK_CAPABILITY = "full"  # or "irreversible"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    ...

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    ...
```

- `import oci` is deferred inside function bodies (not module-level)
- Async SDK calls use `await loop.run_in_executor(None, lambda: ...)`
- Credentials absent → return mock result with `"mock": True`
- PreStateStore captured **before** any destructive SDK call

### Rollback capabilities

| Executor | `ROLLBACK_CAPABILITY` | Rollback strategy |
|---|---|---|
| `oci_create_oke_cluster` | `full` | Delete all node pools, then delete cluster |
| `oci_delete_oke_cluster` | `irreversible` | No reconstitution path for deleted cluster |
| `oci_add_oke_node_pool` | `full` | Delete the pool that was created |
| `oci_delete_oke_node_pool` | `full` | Recreate pool from PreStateStore config |
| `oci_scale_oke_node_pool` | `full` | Restore previous count from PreStateStore |
| `oci_update_oke_node_pool` | `full` | Restore previous config from PreStateStore |
| `oci_get_oke_kubeconfig` | `full` | No-op (read-only) |

`oci_delete_oke_cluster` sets `ROLLBACK_CAPABILITY = "irreversible"` and includes `ROLLBACK_REASON = "Deleted OKE cluster cannot be reconstituted; VCN, subnets, and workloads must be reprovisioned."`. The executor handles node pool deletion internally before cluster deletion — the operator does not need to pre-delete pools.

### Node pool deletion drain advisory

`oci_delete_oke_node_pool` captures the full node pool config in PreStateStore, then checks whether the pool has any nodes in `ACTIVE` state before deleting. If active nodes are found, the execute result includes `"drain_recommended": true, "active_node_count": N`. The operator sees this in the CR result. The executor does not block or attempt kubectl drain. Rollback recreates the pool with the captured config.

### Bundled create

`oci_create_oke_cluster` creates both the cluster and an initial node pool in a single executor. The execute result contains both `cluster_id` and `node_pool_id`. Rollback deletes the node pool first, then the cluster. This avoids the orphaned-cluster problem that would arise from separate create-cluster and create-node-pool CRs.

Polling strategy for create:
1. Create cluster → poll `get_cluster` until `lifecycle_state == "ACTIVE"` (up to 20 min)
2. Create node pool → poll `get_node_pool` until `lifecycle_state == "ACTIVE"` (up to 15 min)

---

## Catalog entries (`backend/app/connectors/catalog/oci.json`)

Seven new entries. Each follows the existing OCI catalog shape:

```json
{
  "action_id": "oci_create_oke_cluster",
  "display_name": "Create OKE Cluster",
  "description": "Create an OKE Kubernetes cluster with an initial node pool.",
  "executor": "oci.oci_create_oke_cluster",
  "rollback_action": "oci_create_oke_cluster",
  "rollback_connector_type": "oci",
  "params_schema": {
    "compartment_id": {"type": "string", "required": true},
    "name": {"type": "string", "required": true},
    "vcn_id": {"type": "string", "required": true},
    "kubernetes_version": {"type": "string", "required": true},
    "subnet_ids": {"type": "array", "required": true},
    "node_shape": {"type": "string", "required": true},
    "node_count": {"type": "integer", "required": true},
    "node_image_id": {"type": "string", "required": false}
  }
}
```

`oci_delete_oke_cluster` has no `rollback_action` (irreversible). All others include `rollback_action` pointing back to themselves (rollback is handled by the executor's own `rollback()` function, consistent with Spec 1 pattern).

`oci_get_oke_kubeconfig` has no `rollback_action` (read-only no-op).

---

## Unit tests

File: `backend/tests/unit/test_oci_parity_spec2_oke.py`

OCI SDK patched at module level using `patch.dict(sys.modules, {...})` — same pattern as `test_oci_parity_spec1.py`. Patch targets:

```python
{
    "oci": MagicMock(),
    "oci.container_engine": MagicMock(),
    "oci.container_engine.models": MagicMock(),
}
```

Tests cover:
- `execute()` mock path (no creds) returns expected keys and `mock: True`
- `rollback()` mock path returns expected keys
- `execute()` with creds calls correct SDK methods
- `rollback()` with creds restores state via PreStateStore
- `oci_delete_oke_node_pool` execute sets `drain_recommended: true` when active nodes present
- `oci_delete_oke_node_pool` execute sets `drain_recommended: false` when no active nodes
- `oci_get_oke_kubeconfig` rollback returns `rolled_back: false, reason: "read-only"`

Target: ~35 tests across 7 executors.

---

## Smoke test

File: `backend/tests/smoke/test_oci_parity_spec2_oke_smoke.py`

Single test class `TestOkeLifecycle`. All steps in one test method to share cluster state. Timeout: `1800` seconds (30 min) for create steps; `600` seconds for day-2 ops.

### Flow

```
1. CREATE cluster + initial node pool (bundled CR)
   - Poll until cluster ACTIVE (up to 20 min)
   - Assert cluster_id and node_pool_id in result

2. GET kubeconfig
   - Assert kubeconfig YAML returned (non-empty string)

3. SCALE node pool up by 1
   - Assert scaled: true in result
   - ROLLBACK scale → assert rolled_back: true, node count restored

4. UPDATE node pool (add freeform label)
   - Assert updated: true in result
   - ROLLBACK update → assert rolled_back: true, config restored

5. ADD second node pool (1 node)
   - Assert node_pool_id in result
   - Poll until ACTIVE

6. DELETE second node pool
   - Assert deleted: true in result
   - Assert drain_recommended key present in result (value may be true or false)

7. DELETE cluster (explicit delete CR)
   - Handles remaining first node pool internally
   - Assert deleted: true in result
```

No xfail conditions expected — OKE is available on the dev OCI tenancy. If the tenancy lacks VCN/subnet prerequisites, the test skips with `pytest.skip`.

### Required smoke helpers

`smoke_helpers.py` already provides `NexplaneClient`, `OCI_CONNECTOR_ID`, `log`, and `_get_oci_creds`. No new helpers needed; VCN/subnet IDs are read from OCI creds dict (keys: `vcn_id`, `subnet_ids`) or from environment variables `OCI_VCN_ID` and `OCI_SUBNET_IDS`.

---

## Ordering constraints

1. `_client.py` update (add `get_container_engine_client`) — Task 1, no dependencies
2. Executor files — Task 2, depends on Task 1
3. Catalog entries — Task 3, depends on Task 2 (executor names must exist)
4. Unit tests — Task 4, depends on Tasks 1–2
5. Smoke test — Task 5, depends on Tasks 1–4 (and live EC2 infrastructure)

---

## Rollback challenges and mitigations

**Cluster create rollback timing**: Cluster deletion after a failed/rolled-back create can itself take 10–15 min. Rollback executor polls until cluster reaches `DELETED` state before returning.

**Node pool delete rollback (recreate)**: Recreating a node pool from PreStateStore config is a full provisioning call (~10 min). Rollback timeout for `oci_delete_oke_node_pool` is set to 900s.

**Irreversible cluster delete**: Operator UI should surface the `ROLLBACK_CAPABILITY = "irreversible"` flag before approval, consistent with how other irreversible CRs are presented.

**OCI work request polling**: OKE operations return a work request ID, not a final resource state. Executors poll `get_work_request` until `status == "SUCCEEDED"` or `"FAILED"`, then fetch the final resource.
