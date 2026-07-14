# OCI Parity Spec 2 — OKE Cluster Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OCI Kubernetes Engine (OKE) full cluster lifecycle and day-2 ops to Nexplane via 7 executors, catalog entries, unit tests, and a live smoke test.

**Architecture:** Bundled cluster+node-pool create (avoids orphan clusters), work-request polling for all OKE async ops, PreStateStore captures mutable state before every destructive call so rollback is deterministic. A shared `_oke_helpers.py` module holds the work-request polling loop used by all executors.

**Tech Stack:** OCI Python SDK (`oci.container_engine`), FastAPI/SQLAlchemy async backend, pytest for unit + smoke tests, httpx for smoke CR lifecycle calls.

## Global Constraints

- All Python files start with `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- `ROLLBACK_CAPABILITY` is a module-level string constant (`"full"` or `"irreversible"`) on every executor
- `ROLLBACK_REASON` string required only when `ROLLBACK_CAPABILITY = "irreversible"`
- Executor signatures: `async def execute(parameters: dict, asset_ids: list, connector) -> dict` and `async def rollback(parameters: dict, execution_result: dict, connector) -> dict` (rollback has NO `asset_ids`)
- `import oci` deferred inside function bodies, never at module level
- `creds = getattr(connector, "credentials", {})` — no creds → return mock result with `"mock": True`
- All SDK calls via `await loop.run_in_executor(None, lambda: ...)` with `loop = asyncio.get_running_loop()`
- PreStateStore captured and committed **before** any destructive SDK call
- `from __future__ import annotations` is FORBIDDEN in every Python file
- Executor catalog field uses dot notation: `"executor": "oci.oci_create_oke_cluster"` (connector_type dot module_name)
- Unit tests patch OCI imports with `patch.dict(sys.modules, {...})` — OCI SDK not installed on dev
- Smoke tests run on EC2 against live OCI tenancy; no mocks; all CR lifecycle steps (create→plan→submit-for-approval→approve→execute→rollback)

---

### Task 1: Container Engine client factory and OKE work-request helper

**Files:**
- Modify: `backend/app/connectors/executors/oci/_client.py` (append one function)
- Create: `backend/app/connectors/executors/oci/_oke_helpers.py`
- Test: `backend/tests/unit/test_oci_parity_spec2_oke.py` (create file, Task 1 tests only)

**Interfaces:**
- Produces: `get_container_engine_client(creds: dict) -> oci.container_engine.ContainerEngineClient`
- Produces: `async def poll_work_request(client, work_request_id: str, entity_type: str, timeout: int) -> str` — returns resource identifier string

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_oci_parity_spec2_oke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import sys
from unittest.mock import MagicMock, patch, AsyncMock


def _connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {
        "user": "u", "key_content": "k", "fingerprint": "f",
        "tenancy": "t", "region": "us-ashburn-1", "private_key": "pk",
    }
    return c


def _empty_connector():
    c = MagicMock()
    c.credentials = {}
    return c


class TestClientFactory:
    def test_get_container_engine_client_exists(self):
        from app.connectors.executors.oci._client import get_container_engine_client
        assert callable(get_container_engine_client)

    def test_poll_work_request_exists(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        assert callable(poll_work_request)

    def test_poll_work_request_returns_identifier_on_success(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        fake_resource = MagicMock()
        fake_resource.entity_type = "cluster"
        fake_resource.identifier = "ocid1.cluster.x"
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = [fake_resource]
        fake_client = MagicMock()
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        result = asyncio.run(poll_work_request(fake_client, "wr-1", "cluster", timeout=60))
        assert result == "ocid1.cluster.x"

    def test_poll_work_request_raises_on_failed(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        fake_wr = MagicMock()
        fake_wr.status = "FAILED"
        fake_wr.time_finished = "2026-01-01"
        fake_client = MagicMock()
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        try:
            asyncio.run(poll_work_request(fake_client, "wr-1", "cluster", timeout=60))
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestClientFactory -v
```
Expected: `ModuleNotFoundError` or `ImportError` on `get_container_engine_client` / `poll_work_request`.

- [ ] **Step 3: Add `get_container_engine_client` to `_client.py`**

Append to `backend/app/connectors/executors/oci/_client.py`:

```python


def get_container_engine_client(creds: dict):
    """Return oci.container_engine.ContainerEngineClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.container_engine.ContainerEngineClient(config)
```

- [ ] **Step 4: Create `_oke_helpers.py`**

Create `backend/app/connectors/executors/oci/_oke_helpers.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def poll_work_request(client, work_request_id: str, entity_type: str, timeout: int) -> str:
    """Poll an OKE work request until SUCCEEDED. Returns the matching resource identifier."""
    loop = asyncio.get_running_loop()
    deadline = time.time() + timeout
    while time.time() < deadline:
        wr = await loop.run_in_executor(
            None, lambda: client.get_work_request(work_request_id).data
        )
        if wr.status == "SUCCEEDED":
            for r in wr.resources:
                if r.entity_type.lower() == entity_type.lower():
                    return r.identifier
            raise RuntimeError(
                f"Work request {work_request_id} SUCCEEDED but no {entity_type} resource found"
            )
        if wr.status == "FAILED":
            raise RuntimeError(f"OKE work request {work_request_id} FAILED")
        await asyncio.sleep(15)
    raise TimeoutError(f"OKE work request {work_request_id} did not complete within {timeout}s")
```

- [ ] **Step 5: Run tests to confirm PASS**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestClientFactory -v
```
Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/oci/_client.py \
        backend/app/connectors/executors/oci/_oke_helpers.py \
        backend/tests/unit/test_oci_parity_spec2_oke.py
git commit -m "feat(oci): add container engine client factory and OKE work-request poller"
```

---

### Task 2: `oci_create_oke_cluster` executor

**Files:**
- Create: `backend/app/connectors/executors/oci/oci_create_oke_cluster.py`
- Modify: `backend/tests/unit/test_oci_parity_spec2_oke.py` (append class)

**Interfaces:**
- Consumes: `get_container_engine_client` from `_client.py`; `poll_work_request` from `_oke_helpers.py`
- Produces execute result: `{"cluster_id": str, "node_pool_id": str, "created_at": str}`
- Produces rollback result: `{"rolled_back": True, "cluster_id": str, "node_pool_id": str}`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/unit/test_oci_parity_spec2_oke.py`:

```python
class TestCreateOkeCluster:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_create_oke_cluster as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import execute
        result = asyncio.run(execute(
            {"compartment_id": "ocid1.compartment.x", "name": "test-cluster",
             "vcn_id": "ocid1.vcn.x", "kubernetes_version": "v1.29.1",
             "subnet_ids": ["ocid1.subnet.x"], "node_shape": "VM.Standard.E3.Flex",
             "node_count": 1},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert "cluster_id" in result
        assert "node_pool_id" in result

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import rollback
        result = asyncio.run(rollback({}, {"cluster_id": "ocid1.cluster.x", "node_pool_id": "ocid1.nodepool.x"}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_creates_cluster_and_node_pool(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import execute
        fake_client = MagicMock()
        fake_wr_cluster = MagicMock()
        fake_wr_cluster.status = "SUCCEEDED"
        cluster_resource = MagicMock()
        cluster_resource.entity_type = "cluster"
        cluster_resource.identifier = "ocid1.cluster.real"
        fake_wr_cluster.resources = [cluster_resource]

        fake_wr_np = MagicMock()
        fake_wr_np.status = "SUCCEEDED"
        np_resource = MagicMock()
        np_resource.entity_type = "nodepool"
        np_resource.identifier = "ocid1.nodepool.real"
        fake_wr_np.resources = [np_resource]

        call_count = [0]
        def _get_wr(wr_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(data=fake_wr_cluster)
            return MagicMock(data=fake_wr_np)

        fake_client.create_cluster.return_value = MagicMock(
            headers={"opc-work-request-id": "wr-cluster-1"}
        )
        fake_client.create_node_pool.return_value = MagicMock(
            headers={"opc-work-request-id": "wr-np-1"}
        )
        fake_client.get_work_request.side_effect = _get_wr
        fake_oci = MagicMock()

        with patch("app.connectors.executors.oci.oci_create_oke_cluster.get_container_engine_client", return_value=fake_client), \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            result = asyncio.run(execute(
                {"compartment_id": "ocid1.compartment.x", "name": "test-cluster",
                 "vcn_id": "ocid1.vcn.x", "kubernetes_version": "v1.29.1",
                 "subnet_ids": ["ocid1.subnet.x"], "node_shape": "VM.Standard.E3.Flex",
                 "node_count": 1},
                [], _connector()
            ))
        assert result["cluster_id"] == "ocid1.cluster.real"
        assert result["node_pool_id"] == "ocid1.nodepool.real"
        fake_client.create_cluster.assert_called_once()
        fake_client.create_node_pool.assert_called_once()

    def test_rollback_deletes_node_pool_then_cluster(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import rollback
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-del-np"})
        fake_client.delete_cluster.return_value = MagicMock(headers={"opc-work-request-id": "wr-del-cl"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        delete_order = []
        fake_client.delete_node_pool.side_effect = lambda np_id: (delete_order.append("nodepool"), MagicMock(headers={"opc-work-request-id": "wr1"}))[1]
        fake_client.delete_cluster.side_effect = lambda cl_id: (delete_order.append("cluster"), MagicMock(headers={"opc-work-request-id": "wr2"}))[1]

        with patch("app.connectors.executors.oci.oci_create_oke_cluster.get_container_engine_client", return_value=fake_client):
            result = asyncio.run(rollback(
                {},
                {"cluster_id": "ocid1.cluster.real", "node_pool_id": "ocid1.nodepool.real"},
                _connector()
            ))
        assert result["rolled_back"] is True
        assert delete_order.index("nodepool") < delete_order.index("cluster")
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestCreateOkeCluster -v
```
Expected: `ModuleNotFoundError` on `oci_create_oke_cluster`.

- [ ] **Step 3: Create `oci_create_oke_cluster.py`**

Create `backend/app/connectors/executors/oci/oci_create_oke_cluster.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    name = parameters.get("name", "")
    vcn_id = parameters.get("vcn_id", "")
    kubernetes_version = parameters.get("kubernetes_version", "")
    subnet_ids = parameters.get("subnet_ids", [])
    node_shape = parameters.get("node_shape", "")
    node_count = parameters.get("node_count", 1)
    node_image_id = parameters.get("node_image_id")

    if not creds:
        return {
            "action": "create_oke_cluster",
            "cluster_id": "ocid1.cluster.mock",
            "node_pool_id": "ocid1.nodepool.mock",
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    cluster_details = oci.container_engine.models.CreateClusterDetails(
        name=name,
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        kubernetes_version=kubernetes_version,
        options=oci.container_engine.models.ClusterCreateOptions(
            service_lb_subnet_ids=subnet_ids,
        ),
    )
    create_resp = await loop.run_in_executor(None, lambda: client.create_cluster(cluster_details))
    cluster_wr_id = create_resp.headers["opc-work-request-id"]
    cluster_id = await poll_work_request(client, cluster_wr_id, "cluster", timeout=1200)

    placement_config = oci.container_engine.models.NodePoolPlacementConfigDetails(
        availability_domain="AD-1",
        subnet_id=subnet_ids[0],
    )
    node_config = oci.container_engine.models.CreateNodePoolNodeConfigDetails(
        size=node_count,
        placement_configs=[placement_config],
    )
    np_kwargs = dict(
        compartment_id=compartment_id,
        cluster_id=cluster_id,
        name=f"{name}-pool",
        kubernetes_version=kubernetes_version,
        node_shape=node_shape,
        node_config_details=node_config,
    )
    if node_image_id:
        np_kwargs["node_source_details"] = oci.container_engine.models.NodeSourceViaImageDetails(
            image_id=node_image_id,
            source_type="IMAGE",
        )
    np_details = oci.container_engine.models.CreateNodePoolDetails(**np_kwargs)
    np_resp = await loop.run_in_executor(None, lambda: client.create_node_pool(np_details))
    np_wr_id = np_resp.headers["opc-work-request-id"]
    node_pool_id = await poll_work_request(client, np_wr_id, "nodepool", timeout=900)

    return {
        "action": "create_oke_cluster",
        "cluster_id": cluster_id,
        "node_pool_id": node_pool_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = execution_result.get("cluster_id", "")
    node_pool_id = execution_result.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_create_oke_cluster", "rolled_back": True, "mock": True}

    client = get_container_engine_client(creds)

    loop = asyncio.get_running_loop()
    if node_pool_id:
        np_resp = await loop.run_in_executor(None, lambda: client.delete_node_pool(node_pool_id))
        np_wr_id = np_resp.headers.get("opc-work-request-id")
        if np_wr_id:
            await poll_work_request(client, np_wr_id, "nodepool", timeout=900)

    if cluster_id:
        cl_resp = await loop.run_in_executor(None, lambda: client.delete_cluster(cluster_id))
        cl_wr_id = cl_resp.headers.get("opc-work-request-id")
        if cl_wr_id:
            await poll_work_request(client, cl_wr_id, "cluster", timeout=1200)

    return {
        "action": "rollback_create_oke_cluster",
        "cluster_id": cluster_id,
        "node_pool_id": node_pool_id,
        "rolled_back": True,
    }
```

- [ ] **Step 4: Run tests to confirm PASS**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestCreateOkeCluster -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/oci/oci_create_oke_cluster.py \
        backend/tests/unit/test_oci_parity_spec2_oke.py
git commit -m "feat(oci): oci_create_oke_cluster executor — bundled cluster+node-pool create with rollback"
```

---

### Task 3: `oci_delete_oke_cluster` executor

**Files:**
- Create: `backend/app/connectors/executors/oci/oci_delete_oke_cluster.py`
- Modify: `backend/tests/unit/test_oci_parity_spec2_oke.py` (append class)

**Interfaces:**
- Consumes: `get_container_engine_client`, `poll_work_request`
- Produces execute result: `{"deleted": True, "cluster_id": str, "deleted_at": str}`
- Produces rollback result: `{"rolled_back": False, "reason": str}`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/unit/test_oci_parity_spec2_oke.py`:

```python
class TestDeleteOkeCluster:
    def test_rollback_capability_irreversible(self):
        import app.connectors.executors.oci.oci_delete_oke_cluster as m
        assert m.ROLLBACK_CAPABILITY == "irreversible"
        assert m.ROLLBACK_REASON

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_delete_oke_cluster import execute
        result = asyncio.run(execute(
            {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x"},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert result["deleted"] is True

    def test_rollback_returns_false(self):
        from app.connectors.executors.oci.oci_delete_oke_cluster import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False
        assert "reason" in result

    def test_execute_deletes_node_pools_before_cluster(self):
        from app.connectors.executors.oci.oci_delete_oke_cluster import execute
        fake_pool = MagicMock()
        fake_pool.id = "ocid1.nodepool.x"
        fake_pool.lifecycle_state = "ACTIVE"
        fake_client = MagicMock()
        fake_client.list_node_pools.return_value = MagicMock(data=[fake_pool])
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        delete_order = []
        fake_client.delete_node_pool.side_effect = lambda np_id: (delete_order.append("nodepool"), MagicMock(headers={"opc-work-request-id": "wr1"}))[1]
        fake_client.delete_cluster.side_effect = lambda cl_id: (delete_order.append("cluster"), MagicMock(headers={"opc-work-request-id": "wr2"}))[1]

        with patch("app.connectors.executors.oci.oci_delete_oke_cluster.get_container_engine_client", return_value=fake_client):
            result = asyncio.run(execute(
                {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x"},
                [], _connector()
            ))
        assert result["deleted"] is True
        assert delete_order.index("nodepool") < delete_order.index("cluster")
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestDeleteOkeCluster -v
```

- [ ] **Step 3: Create `oci_delete_oke_cluster.py`**

Create `backend/app/connectors/executors/oci/oci_delete_oke_cluster.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = (
    "Deleted OKE cluster cannot be reconstituted; "
    "VCN, subnets, and workloads must be reprovisioned."
)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = parameters.get("cluster_id", "")
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "delete_oke_cluster",
            "cluster_id": cluster_id,
            "deleted": True,
            "mock": True,
        }

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    node_pools = await loop.run_in_executor(
        None,
        lambda: client.list_node_pools(
            compartment_id=compartment_id, cluster_id=cluster_id
        ).data,
    )
    for pool in node_pools:
        if pool.lifecycle_state not in ("DELETED", "DELETING"):
            np_resp = await loop.run_in_executor(
                None, lambda p=pool: client.delete_node_pool(p.id)
            )
            np_wr_id = np_resp.headers.get("opc-work-request-id")
            if np_wr_id:
                await poll_work_request(client, np_wr_id, "nodepool", timeout=900)

    cl_resp = await loop.run_in_executor(None, lambda: client.delete_cluster(cluster_id))
    cl_wr_id = cl_resp.headers.get("opc-work-request-id")
    if cl_wr_id:
        await poll_work_request(client, cl_wr_id, "cluster", timeout=1200)

    return {
        "action": "delete_oke_cluster",
        "cluster_id": cluster_id,
        "deleted": True,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "rollback_delete_oke_cluster",
        "rolled_back": False,
        "reason": ROLLBACK_REASON,
    }
```

- [ ] **Step 4: Run tests to confirm PASS**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestDeleteOkeCluster -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/oci/oci_delete_oke_cluster.py \
        backend/tests/unit/test_oci_parity_spec2_oke.py
git commit -m "feat(oci): oci_delete_oke_cluster executor — drains node pools before cluster delete, irreversible"
```

---

### Task 4: `oci_add_oke_node_pool` and `oci_delete_oke_node_pool` executors

**Files:**
- Create: `backend/app/connectors/executors/oci/oci_add_oke_node_pool.py`
- Create: `backend/app/connectors/executors/oci/oci_delete_oke_node_pool.py`
- Modify: `backend/tests/unit/test_oci_parity_spec2_oke.py` (append two classes)

**Interfaces:**
- Consumes: `get_container_engine_client`, `poll_work_request`, `PreStateStore`, `AsyncSessionLocal`
- `oci_add_oke_node_pool` execute result: `{"node_pool_id": str, "cluster_id": str, "created_at": str}`
- `oci_delete_oke_node_pool` execute result: `{"deleted": True, "drain_recommended": bool, "active_node_count": int}`
- `oci_delete_oke_node_pool` rollback result: `{"rolled_back": True, "new_node_pool_id": str}`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/unit/test_oci_parity_spec2_oke.py`:

```python
class TestAddOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_add_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import execute
        result = asyncio.run(execute(
            {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x",
             "name": "pool-2", "kubernetes_version": "v1.29.1",
             "node_shape": "VM.Standard.E3.Flex", "node_count": 1, "subnet_id": "ocid1.subnet.x"},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert "node_pool_id" in result

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import rollback
        result = asyncio.run(rollback({}, {"node_pool_id": "ocid1.nodepool.x"}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_returns_node_pool_id(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import execute
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        np_resource = MagicMock()
        np_resource.entity_type = "nodepool"
        np_resource.identifier = "ocid1.nodepool.real"
        fake_wr.resources = [np_resource]
        fake_client.create_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_add_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            result = asyncio.run(execute(
                {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x",
                 "name": "pool-2", "kubernetes_version": "v1.29.1",
                 "node_shape": "VM.Standard.E3.Flex", "node_count": 1, "subnet_id": "ocid1.subnet.x"},
                [], _connector()
            ))
        assert result["node_pool_id"] == "ocid1.nodepool.real"

    def test_rollback_deletes_node_pool(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import rollback
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        with patch("app.connectors.executors.oci.oci_add_oke_node_pool.get_container_engine_client", return_value=fake_client):
            result = asyncio.run(rollback({}, {"node_pool_id": "ocid1.nodepool.x"}, _connector()))
        fake_client.delete_node_pool.assert_called_once_with("ocid1.nodepool.x")
        assert result["rolled_back"] is True


class TestDeleteOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_delete_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import execute
        result = asyncio.run(execute({"node_pool_id": "ocid1.nodepool.x"}, [], _empty_connector()))
        assert result["mock"] is True
        assert "drain_recommended" in result

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import rollback
        result = asyncio.run(rollback({}, {}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_drain_recommended_true_when_active_nodes(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import execute
        active_node = MagicMock()
        active_node.lifecycle_state = "ACTIVE"
        fake_pool = MagicMock()
        fake_pool.nodes = [active_node]
        fake_pool.compartment_id = "ocid1.compartment.x"
        fake_pool.cluster_id = "ocid1.cluster.x"
        fake_pool.name = "pool-1"
        fake_pool.kubernetes_version = "v1.29.1"
        fake_pool.node_shape = "VM.Standard.E3.Flex"
        fake_pool.node_config_details.size = 1
        fake_pool.node_config_details.placement_configs = []
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        with patch("app.connectors.executors.oci.oci_delete_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.AsyncSessionLocal") as mock_session:
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["drain_recommended"] is True
        assert result["active_node_count"] == 1

    def test_drain_recommended_false_when_no_active_nodes(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import execute
        fake_pool = MagicMock()
        fake_pool.nodes = []
        fake_pool.compartment_id = "ocid1.compartment.x"
        fake_pool.cluster_id = "ocid1.cluster.x"
        fake_pool.name = "pool-1"
        fake_pool.kubernetes_version = "v1.29.1"
        fake_pool.node_shape = "VM.Standard.E3.Flex"
        fake_pool.node_config_details.size = 0
        fake_pool.node_config_details.placement_configs = []
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        with patch("app.connectors.executors.oci.oci_delete_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.AsyncSessionLocal") as mock_session:
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["drain_recommended"] is False

    def test_rollback_recreates_pool_from_pre_state(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import rollback
        captured_state = {
            "compartment_id": "ocid1.compartment.x",
            "cluster_id": "ocid1.cluster.x",
            "name": "pool-1",
            "kubernetes_version": "v1.29.1",
            "node_shape": "VM.Standard.E3.Flex",
            "node_config_details": {
                "size": 1,
                "placement_configs": [{"availability_domain": "AD-1", "subnet_id": "ocid1.subnet.x"}],
            },
        }
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        np_resource = MagicMock()
        np_resource.entity_type = "nodepool"
        np_resource.identifier = "ocid1.nodepool.new"
        fake_wr.resources = [np_resource]
        fake_client.create_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_delete_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.retrieve = AsyncMock(return_value=captured_state)
            result = asyncio.run(rollback(
                {"node_pool_id": "ocid1.nodepool.old", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                {}, _connector()
            ))
        assert result["rolled_back"] is True
        assert result["new_node_pool_id"] == "ocid1.nodepool.new"
        fake_client.create_node_pool.assert_called_once()
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestAddOkeNodePool tests/unit/test_oci_parity_spec2_oke.py::TestDeleteOkeNodePool -v
```

- [ ] **Step 3: Create `oci_add_oke_node_pool.py`**

Create `backend/app/connectors/executors/oci/oci_add_oke_node_pool.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = parameters.get("cluster_id", "")
    compartment_id = parameters.get("compartment_id", "")
    name = parameters.get("name", "")
    kubernetes_version = parameters.get("kubernetes_version", "")
    node_shape = parameters.get("node_shape", "")
    node_count = parameters.get("node_count", 1)
    subnet_id = parameters.get("subnet_id", "")
    node_image_id = parameters.get("node_image_id")

    if not creds:
        return {
            "action": "add_oke_node_pool",
            "node_pool_id": "ocid1.nodepool.mock",
            "cluster_id": cluster_id,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    np_kwargs = dict(
        compartment_id=compartment_id,
        cluster_id=cluster_id,
        name=name,
        kubernetes_version=kubernetes_version,
        node_shape=node_shape,
        node_config_details=oci.container_engine.models.CreateNodePoolNodeConfigDetails(
            size=node_count,
            placement_configs=[
                oci.container_engine.models.NodePoolPlacementConfigDetails(
                    availability_domain="AD-1",
                    subnet_id=subnet_id,
                )
            ],
        ),
    )
    if node_image_id:
        np_kwargs["node_source_details"] = oci.container_engine.models.NodeSourceViaImageDetails(
            image_id=node_image_id,
            source_type="IMAGE",
        )
    details = oci.container_engine.models.CreateNodePoolDetails(**np_kwargs)
    response = await loop.run_in_executor(None, lambda: client.create_node_pool(details))
    wr_id = response.headers["opc-work-request-id"]
    node_pool_id = await poll_work_request(client, wr_id, "nodepool", timeout=900)

    return {
        "action": "add_oke_node_pool",
        "cluster_id": cluster_id,
        "node_pool_id": node_pool_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = execution_result.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_add_oke_node_pool", "rolled_back": True, "mock": True}

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, lambda: client.delete_node_pool(node_pool_id))
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        await poll_work_request(client, wr_id, "nodepool", timeout=900)

    return {
        "action": "rollback_add_oke_node_pool",
        "node_pool_id": node_pool_id,
        "rolled_back": True,
    }
```

- [ ] **Step 4: Create `oci_delete_oke_node_pool.py`**

Create `backend/app/connectors/executors/oci/oci_delete_oke_node_pool.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {
            "action": "delete_oke_node_pool",
            "node_pool_id": node_pool_id,
            "deleted": True,
            "drain_recommended": False,
            "active_node_count": 0,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    pool = await loop.run_in_executor(None, lambda: client.get_node_pool(node_pool_id).data)

    active_count = sum(
        1 for n in (pool.nodes or []) if n.lifecycle_state == "ACTIVE"
    )
    state = {
        "compartment_id": pool.compartment_id,
        "cluster_id": pool.cluster_id,
        "name": pool.name,
        "kubernetes_version": pool.kubernetes_version,
        "node_shape": pool.node_shape,
        "node_config_details": {
            "size": pool.node_config_details.size,
            "placement_configs": [
                {"availability_domain": pc.availability_domain, "subnet_id": pc.subnet_id}
                for pc in (pool.node_config_details.placement_configs or [])
            ],
        },
    }

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            state,
        )
        await db.commit()

    response = await loop.run_in_executor(None, lambda: client.delete_node_pool(node_pool_id))
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        await poll_work_request(client, wr_id, "nodepool", timeout=900)

    return {
        "action": "delete_oke_node_pool",
        "node_pool_id": node_pool_id,
        "deleted": True,
        "drain_recommended": active_count > 0,
        "active_node_count": active_count,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_delete_oke_node_pool", "rolled_back": True, "mock": True}

    import oci

    client = get_container_engine_client(creds)

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {
            "action": "rollback_delete_oke_node_pool",
            "rolled_back": False,
            "error": "pre-state not found",
        }

    placement_configs = [
        oci.container_engine.models.NodePoolPlacementConfigDetails(
            availability_domain=pc["availability_domain"],
            subnet_id=pc["subnet_id"],
        )
        for pc in state["node_config_details"]["placement_configs"]
    ]
    details = oci.container_engine.models.CreateNodePoolDetails(
        compartment_id=state["compartment_id"],
        cluster_id=state["cluster_id"],
        name=state["name"],
        kubernetes_version=state["kubernetes_version"],
        node_shape=state["node_shape"],
        node_config_details=oci.container_engine.models.CreateNodePoolNodeConfigDetails(
            size=state["node_config_details"]["size"],
            placement_configs=placement_configs,
        ),
    )
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, lambda: client.create_node_pool(details))
    wr_id = response.headers["opc-work-request-id"]
    new_pool_id = await poll_work_request(client, wr_id, "nodepool", timeout=900)

    return {
        "action": "rollback_delete_oke_node_pool",
        "original_node_pool_id": node_pool_id,
        "new_node_pool_id": new_pool_id,
        "rolled_back": True,
    }
```

- [ ] **Step 5: Run tests to confirm PASS**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestAddOkeNodePool tests/unit/test_oci_parity_spec2_oke.py::TestDeleteOkeNodePool -v
```
Expected: 10 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/oci/oci_add_oke_node_pool.py \
        backend/app/connectors/executors/oci/oci_delete_oke_node_pool.py \
        backend/tests/unit/test_oci_parity_spec2_oke.py
git commit -m "feat(oci): oci_add_oke_node_pool and oci_delete_oke_node_pool with drain advisory"
```

---

### Task 5: `oci_scale_oke_node_pool` and `oci_update_oke_node_pool` executors

**Files:**
- Create: `backend/app/connectors/executors/oci/oci_scale_oke_node_pool.py`
- Create: `backend/app/connectors/executors/oci/oci_update_oke_node_pool.py`
- Modify: `backend/tests/unit/test_oci_parity_spec2_oke.py` (append two classes)

**Interfaces:**
- Consumes: `get_container_engine_client`, `poll_work_request`, `PreStateStore`, `AsyncSessionLocal`
- `oci_scale_oke_node_pool` execute result: `{"scaled": True, "previous_count": int, "new_count": int}`
- `oci_update_oke_node_pool` execute result: `{"updated": True, "node_pool_id": str}`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/unit/test_oci_parity_spec2_oke.py`:

```python
class TestScaleOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_scale_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import execute
        result = asyncio.run(execute(
            {"node_pool_id": "ocid1.nodepool.x", "node_count": 3}, [], _empty_connector()
        ))
        assert result["mock"] is True
        assert result["scaled"] is True

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import rollback
        result = asyncio.run(rollback({"node_pool_id": "ocid1.nodepool.x"}, {}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_captures_prior_count_and_scales(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import execute
        fake_pool = MagicMock()
        fake_pool.node_config_details.size = 2
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.update_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_scale_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "node_count": 4,
                 "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["previous_count"] == 2
        assert result["new_count"] == 4
        assert result["scaled"] is True
        fake_client.update_node_pool.assert_called_once()

    def test_rollback_restores_prior_count(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import rollback
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.update_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_scale_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.retrieve = AsyncMock(return_value={"node_count": 2})
            result = asyncio.run(rollback(
                {"node_pool_id": "ocid1.nodepool.x", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                {}, _connector()
            ))
        assert result["rolled_back"] is True
        assert result["restored_count"] == 2


class TestUpdateOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_update_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_update_oke_node_pool import execute
        result = asyncio.run(execute(
            {"node_pool_id": "ocid1.nodepool.x", "name": "new-name"}, [], _empty_connector()
        ))
        assert result["mock"] is True
        assert result["updated"] is True

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_update_oke_node_pool import rollback
        result = asyncio.run(rollback({"node_pool_id": "ocid1.nodepool.x"}, {}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_captures_prior_name_and_updates(self):
        from app.connectors.executors.oci.oci_update_oke_node_pool import execute
        fake_pool = MagicMock()
        fake_pool.name = "old-name"
        fake_pool.initial_node_labels = []
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.update_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_update_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_update_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_update_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "name": "new-name",
                 "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["updated"] is True
        fake_client.update_node_pool.assert_called_once()
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestScaleOkeNodePool tests/unit/test_oci_parity_spec2_oke.py::TestUpdateOkeNodePool -v
```

- [ ] **Step 3: Create `oci_scale_oke_node_pool.py`**

Create `backend/app/connectors/executors/oci/oci_scale_oke_node_pool.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")
    new_count = parameters.get("node_count", 1)

    if not creds:
        return {
            "action": "scale_oke_node_pool",
            "node_pool_id": node_pool_id,
            "scaled": True,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    pool = await loop.run_in_executor(None, lambda: client.get_node_pool(node_pool_id).data)
    prior_count = pool.node_config_details.size

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            {"node_count": prior_count},
        )
        await db.commit()

    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(
                node_config_details=oci.container_engine.models.UpdateNodePoolNodeConfigDetails(
                    size=new_count,
                )
            ),
        ),
    )
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        await poll_work_request(client, wr_id, "nodepool", timeout=600)

    return {
        "action": "scale_oke_node_pool",
        "node_pool_id": node_pool_id,
        "previous_count": prior_count,
        "new_count": new_count,
        "scaled": True,
        "scaled_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_scale_oke_node_pool", "rolled_back": True, "mock": True}

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {"action": "rollback_scale_oke_node_pool", "rolled_back": False, "error": "pre-state not found"}

    prior_count = state["node_count"]
    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(
                node_config_details=oci.container_engine.models.UpdateNodePoolNodeConfigDetails(
                    size=prior_count,
                )
            ),
        ),
    )
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        await poll_work_request(client, wr_id, "nodepool", timeout=600)

    return {
        "action": "rollback_scale_oke_node_pool",
        "node_pool_id": node_pool_id,
        "restored_count": prior_count,
        "rolled_back": True,
    }
```

- [ ] **Step 4: Create `oci_update_oke_node_pool.py`**

Create `backend/app/connectors/executors/oci/oci_update_oke_node_pool.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_container_engine_client
from ._oke_helpers import poll_work_request
from app.services.pre_state_store import PreStateStore
from app.database import AsyncSessionLocal

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")
    new_name = parameters.get("name")
    new_labels = parameters.get("initial_node_labels")

    if not creds:
        return {
            "action": "update_oke_node_pool",
            "node_pool_id": node_pool_id,
            "updated": True,
            "mock": True,
        }

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    pool = await loop.run_in_executor(None, lambda: client.get_node_pool(node_pool_id).data)

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
            {
                "name": pool.name,
                "initial_node_labels": [
                    {"key": lbl.key, "value": lbl.value}
                    for lbl in (pool.initial_node_labels or [])
                ],
            },
        )
        await db.commit()

    update_kwargs = {}
    if new_name is not None:
        update_kwargs["name"] = new_name
    if new_labels is not None:
        update_kwargs["initial_node_labels"] = [
            oci.container_engine.models.KeyValue(key=lbl["key"], value=lbl["value"])
            for lbl in new_labels
        ]

    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(**update_kwargs),
        ),
    )
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        await poll_work_request(client, wr_id, "nodepool", timeout=600)

    return {
        "action": "update_oke_node_pool",
        "node_pool_id": node_pool_id,
        "updated": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_pool_id = parameters.get("node_pool_id", "")

    if not creds:
        return {"action": "rollback_update_oke_node_pool", "rolled_back": True, "mock": True}

    import oci

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            parameters.get("cr_id"),
            parameters.get("step_id"),
            parameters.get("org_id"),
        )

    if not state:
        return {"action": "rollback_update_oke_node_pool", "rolled_back": False, "error": "pre-state not found"}

    update_kwargs = {"name": state["name"]}
    if state.get("initial_node_labels") is not None:
        update_kwargs["initial_node_labels"] = [
            oci.container_engine.models.KeyValue(key=lbl["key"], value=lbl["value"])
            for lbl in state["initial_node_labels"]
        ]

    response = await loop.run_in_executor(
        None,
        lambda: client.update_node_pool(
            node_pool_id,
            oci.container_engine.models.UpdateNodePoolDetails(**update_kwargs),
        ),
    )
    wr_id = response.headers.get("opc-work-request-id")
    if wr_id:
        await poll_work_request(client, wr_id, "nodepool", timeout=600)

    return {
        "action": "rollback_update_oke_node_pool",
        "node_pool_id": node_pool_id,
        "rolled_back": True,
    }
```

- [ ] **Step 5: Run tests to confirm PASS**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestScaleOkeNodePool tests/unit/test_oci_parity_spec2_oke.py::TestUpdateOkeNodePool -v
```
Expected: 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/oci/oci_scale_oke_node_pool.py \
        backend/app/connectors/executors/oci/oci_update_oke_node_pool.py \
        backend/tests/unit/test_oci_parity_spec2_oke.py
git commit -m "feat(oci): oci_scale_oke_node_pool and oci_update_oke_node_pool with PreStateStore rollback"
```

---

### Task 6: `oci_get_oke_kubeconfig` executor and all catalog entries

**Files:**
- Create: `backend/app/connectors/executors/oci/oci_get_oke_kubeconfig.py`
- Modify: `backend/app/connectors/catalog/oci.json` (append 7 entries before closing `]`)
- Modify: `backend/tests/unit/test_oci_parity_spec2_oke.py` (append class)

**Interfaces:**
- Consumes: `get_container_engine_client`
- Produces execute result: `{"kubeconfig": str, "cluster_id": str}`
- Produces rollback result: `{"rolled_back": False, "reason": "read-only"}`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/unit/test_oci_parity_spec2_oke.py`:

```python
class TestGetOkeKubeconfig:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_get_oke_kubeconfig as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_get_oke_kubeconfig import execute
        result = asyncio.run(execute({"cluster_id": "ocid1.cluster.x"}, [], _empty_connector()))
        assert result["mock"] is True
        assert "kubeconfig" in result
        assert result["kubeconfig"]

    def test_rollback_returns_read_only(self):
        from app.connectors.executors.oci.oci_get_oke_kubeconfig import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False
        assert result["reason"] == "read-only"

    def test_execute_returns_kubeconfig_yaml(self):
        from app.connectors.executors.oci.oci_get_oke_kubeconfig import execute
        fake_content = b"apiVersion: v1\nkind: Config\n"
        fake_response = MagicMock()
        fake_response.data.content = fake_content
        fake_client = MagicMock()
        fake_client.create_kubeconfig.return_value = fake_response
        with patch("app.connectors.executors.oci.oci_get_oke_kubeconfig.get_container_engine_client", return_value=fake_client):
            result = asyncio.run(execute({"cluster_id": "ocid1.cluster.x"}, [], _connector()))
        assert "apiVersion" in result["kubeconfig"]
        fake_client.create_kubeconfig.assert_called_once_with("ocid1.cluster.x")
```

- [ ] **Step 2: Run to confirm FAIL**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestGetOkeKubeconfig -v
```

- [ ] **Step 3: Create `oci_get_oke_kubeconfig.py`**

Create `backend/app/connectors/executors/oci/oci_get_oke_kubeconfig.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

from ._client import get_container_engine_client

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_id = parameters.get("cluster_id", "")

    if not creds:
        return {
            "action": "get_oke_kubeconfig",
            "cluster_id": cluster_id,
            "kubeconfig": "apiVersion: v1\nkind: Config\n# mock\n",
            "mock": True,
        }

    client = get_container_engine_client(creds)
    loop = asyncio.get_running_loop()

    response = await loop.run_in_executor(None, lambda: client.create_kubeconfig(cluster_id))
    raw = response.data.content
    kubeconfig_yaml = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    return {
        "action": "get_oke_kubeconfig",
        "cluster_id": cluster_id,
        "kubeconfig": kubeconfig_yaml,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "rollback_get_oke_kubeconfig",
        "rolled_back": False,
        "reason": "read-only",
    }
```

- [ ] **Step 4: Run kubeconfig tests to confirm PASS**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py::TestGetOkeKubeconfig -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Add 7 catalog entries to `oci.json`**

Open `backend/app/connectors/catalog/oci.json`. The file ends with `...}]\n}`. Add the 7 entries by inserting them before the closing `]` of the actions array. The last existing entry currently ends with `}` followed by `]\n}`. Insert after that last `}` and before `]`:

```json
    ,
    {"action_id": "oci_create_oke_cluster", "action_type": "change", "display_name": "Create OKE Cluster", "description": "Create an OKE Kubernetes cluster with a bundled initial node pool. Rollback deletes both the node pool and the cluster.", "executor": "oci.oci_create_oke_cluster", "generic_action": "oci_create_oke_cluster", "rollback_action": "oci_create_oke_cluster", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 4, "estimated_duration_seconds": 1800, "parameters": [{"name": "compartment_id", "type": "string", "required": true}, {"name": "name", "type": "string", "required": true}, {"name": "vcn_id", "type": "string", "required": true}, {"name": "kubernetes_version", "type": "string", "required": true}, {"name": "subnet_ids", "type": "array", "required": true}, {"name": "node_shape", "type": "string", "required": true}, {"name": "node_count", "type": "integer", "required": true}, {"name": "node_image_id", "type": "string", "required": false}]},
    {"action_id": "oci_delete_oke_cluster", "action_type": "change", "display_name": "Delete OKE Cluster", "description": "Delete an OKE cluster and all its node pools. Irreversible — cluster cannot be reconstituted after deletion.", "executor": "oci.oci_delete_oke_cluster", "generic_action": "oci_delete_oke_cluster", "applicable_asset_types": ["cloud_account"], "execution_tier": 4, "estimated_duration_seconds": 1800, "parameters": [{"name": "cluster_id", "type": "string", "required": true}, {"name": "compartment_id", "type": "string", "required": true}]},
    {"action_id": "oci_add_oke_node_pool", "action_type": "change", "display_name": "Add OKE Node Pool", "description": "Add a new node pool to an existing OKE cluster. Rollback deletes the created pool.", "executor": "oci.oci_add_oke_node_pool", "generic_action": "oci_add_oke_node_pool", "rollback_action": "oci_add_oke_node_pool", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 3, "estimated_duration_seconds": 900, "parameters": [{"name": "cluster_id", "type": "string", "required": true}, {"name": "compartment_id", "type": "string", "required": true}, {"name": "name", "type": "string", "required": true}, {"name": "kubernetes_version", "type": "string", "required": true}, {"name": "node_shape", "type": "string", "required": true}, {"name": "node_count", "type": "integer", "required": true}, {"name": "subnet_id", "type": "string", "required": true}, {"name": "node_image_id", "type": "string", "required": false}]},
    {"action_id": "oci_delete_oke_node_pool", "action_type": "change", "display_name": "Delete OKE Node Pool", "description": "Delete a node pool with pre-state capture and drain advisory. Rollback recreates the pool from captured config.", "executor": "oci.oci_delete_oke_node_pool", "generic_action": "oci_delete_oke_node_pool", "rollback_action": "oci_delete_oke_node_pool", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 3, "estimated_duration_seconds": 900, "parameters": [{"name": "node_pool_id", "type": "string", "required": true}]},
    {"action_id": "oci_scale_oke_node_pool", "action_type": "change", "display_name": "Scale OKE Node Pool", "description": "Change the node count of an existing node pool. Pre-state captured; rollback restores original count.", "executor": "oci.oci_scale_oke_node_pool", "generic_action": "oci_scale_oke_node_pool", "rollback_action": "oci_scale_oke_node_pool", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 2, "estimated_duration_seconds": 600, "parameters": [{"name": "node_pool_id", "type": "string", "required": true}, {"name": "node_count", "type": "integer", "required": true}]},
    {"action_id": "oci_update_oke_node_pool", "action_type": "change", "display_name": "Update OKE Node Pool", "description": "Update node pool display name or node labels. Pre-state captured; rollback restores original config.", "executor": "oci.oci_update_oke_node_pool", "generic_action": "oci_update_oke_node_pool", "rollback_action": "oci_update_oke_node_pool", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 2, "estimated_duration_seconds": 600, "parameters": [{"name": "node_pool_id", "type": "string", "required": true}, {"name": "name", "type": "string", "required": false}, {"name": "initial_node_labels", "type": "array", "required": false}]},
    {"action_id": "oci_get_oke_kubeconfig", "action_type": "read", "display_name": "Get OKE Kubeconfig", "description": "Retrieve the kubeconfig YAML for an OKE cluster.", "executor": "oci.oci_get_oke_kubeconfig", "generic_action": "oci_get_oke_kubeconfig", "applicable_asset_types": ["cloud_account"], "execution_tier": 1, "estimated_duration_seconds": 10, "parameters": [{"name": "cluster_id", "type": "string", "required": true}]}
```

**Important:** Verify valid JSON after editing. Run:
```
cd backend && python -c "import json; json.load(open('app/connectors/catalog/oci.json')); print('JSON valid')"
```

- [ ] **Step 6: Run all unit tests for this spec**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec2_oke.py -v
```
Expected: all tests PASS (approximately 27 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/oci/oci_get_oke_kubeconfig.py \
        backend/app/connectors/catalog/oci.json \
        backend/tests/unit/test_oci_parity_spec2_oke.py
git commit -m "feat(oci): oci_get_oke_kubeconfig executor and 7 OKE catalog entries"
```

---

### Task 7: Smoke test

**Files:**
- Create: `backend/tests/smoke/test_oci_parity_spec2_oke_smoke.py`
- Modify: `backend/tests/smoke/smoke_helpers.py` (append `_get_oci_container_engine_client`)

**Interfaces:**
- Consumes: `NexplaneClient`, `OCI_CONNECTOR_ID`, `log`, `_get_oci_creds`, `_get_oci_network_client` from `smoke_helpers`
- Produces: passing live smoke phases on EC2 runner

Prerequisites for smoke run:
- OCI tenancy must have at least one VCN with at least one subnet in it
- `_get_oci_creds()` must return valid creds (connector already in DB)
- Run from inside the backend container on EC2: `docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_oci_parity_spec2_oke_smoke.py -v -s --timeout=2400`

- [ ] **Step 1: Add `_get_oci_container_engine_client` to `smoke_helpers.py`**

Append to `backend/tests/smoke/smoke_helpers.py` (after the `_get_oci_blockstorage_client` function):

```python


def _get_oci_container_engine_client():
    """Return an OCI ContainerEngineClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.container_engine.ContainerEngineClient(config)
```

- [ ] **Step 2: Create the smoke test file**

Create `backend/tests/smoke/test_oci_parity_spec2_oke_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OCI Parity Spec 2 — OKE Lifecycle Smoke Test

Phases (single test, shared cluster state):
  1. CREATE cluster + initial node pool (bundled CR, ~20 min)
  2. GET kubeconfig
  3. SCALE node pool up by 1 → rollback
  4. UPDATE node pool name → rollback
  5. ADD second node pool → wait ACTIVE
  6. DELETE second node pool → verify drain_recommended key present
  7. DELETE cluster (handles remaining node pool internally)

Timeout: 2400s (40 min) per pytest-timeout.
Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \
    /app/tests/smoke/test_oci_parity_spec2_oke_smoke.py -v -s --timeout=2400
"""

import os
import time
import uuid

import pytest

from smoke_helpers import (
    NexplaneClient,
    OCI_CONNECTOR_ID,
    log,
    _get_oci_creds,
    _get_oci_network_client,
    _get_oci_container_engine_client,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

CREATE_TIMEOUT = 1800   # 30 min: cluster + node pool provisioning
DAY2_TIMEOUT = 900      # 15 min: scale / update / add pool
DELETE_TIMEOUT = 1800   # 30 min: delete cluster (drains pools first)


def _get_client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _run_cr(client, label, action_id, params, timeout):
    """Create → plan → submit-for-approval → approve → execute → poll until terminal."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "oci", "action_id": action_id, "params": params},
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "oke-smoke"},
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
            raise AssertionError(f"[{label}] CR {cr_id} status={status!r}\n{str(cr.get('execution_runs', ''))[:400]}")
        time.sleep(10)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback(client, cr_id, label, timeout):
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
        time.sleep(10)
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
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _get_vcn_and_subnet():
    """Return (vcn_id, subnet_id, compartment_id) from the first available subnet."""
    creds = _get_oci_creds()
    compartment_id = creds.get("compartment_id", creds.get("tenancy", ""))
    net = _get_oci_network_client()
    if not net:
        pytest.skip("Cannot get network client")
    subnets = net.list_subnets(compartment_id=compartment_id).data
    if not subnets:
        pytest.skip("No subnets found in compartment — cannot create OKE cluster")
    subnet = subnets[0]
    return subnet.vcn_id, subnet.id, compartment_id


def _get_kubernetes_version():
    """Return the latest available OKE Kubernetes version."""
    ce = _get_oci_container_engine_client()
    if not ce:
        pytest.skip("Cannot get container engine client")
    options = ce.get_cluster_options(cluster_option_id="all").data
    versions = options.kubernetes_versions
    if not versions:
        pytest.skip("No Kubernetes versions available in this tenancy")
    return versions[-1]


class TestOkeLifecycle:
    """Full OKE lifecycle: create → day-2 ops → delete."""

    def test_oke_lifecycle(self):
        client = _get_client()
        creds = _get_oci_creds()
        compartment_id = creds.get("compartment_id", creds.get("tenancy", ""))
        vcn_id, subnet_id, _ = _get_vcn_and_subnet()
        k8s_version = _get_kubernetes_version()
        cluster_name = f"nexplane-smoke-{uuid.uuid4().hex[:8]}"
        log(f"[OKE] Using VCN {vcn_id[:40]}... subnet {subnet_id[:40]}... k8s {k8s_version}")

        # ---------------------------------------------------------------
        # Step 1: Create cluster + initial node pool (bundled)
        # ---------------------------------------------------------------
        log(f"[OKE] Creating cluster: {cluster_name}")
        create_cr = _run_cr(
            client, "[OKE] Create cluster + node pool", "oci_create_oke_cluster",
            {
                "compartment_id": compartment_id,
                "name": cluster_name,
                "vcn_id": vcn_id,
                "kubernetes_version": k8s_version,
                "subnet_ids": [subnet_id],
                "node_shape": "VM.Standard.E3.Flex",
                "node_count": 1,
            },
            timeout=CREATE_TIMEOUT,
        )
        create_result = _step_result(create_cr)
        cluster_id = create_result.get("cluster_id", "")
        node_pool_id = create_result.get("node_pool_id", "")
        assert cluster_id, f"cluster_id missing from create result: {create_result}"
        assert node_pool_id, f"node_pool_id missing from create result: {create_result}"
        create_cr_id = create_cr["id"]
        log(f"[OKE] Cluster: {cluster_id[:40]}... Pool: {node_pool_id[:40]}...")

        try:
            # -----------------------------------------------------------
            # Step 2: Get kubeconfig
            # -----------------------------------------------------------
            log("[OKE] Getting kubeconfig")
            kc_cr = _run_cr(
                client, "[OKE] Get kubeconfig", "oci_get_oke_kubeconfig",
                {"cluster_id": cluster_id},
                timeout=60,
            )
            kc_result = _step_result(kc_cr)
            kubeconfig = kc_result.get("kubeconfig", "")
            assert kubeconfig, f"kubeconfig missing from result: {kc_result}"
            assert "apiVersion" in kubeconfig, f"kubeconfig is not YAML: {kubeconfig[:100]}"
            log("[OKE] Kubeconfig retrieved OK")

            # -----------------------------------------------------------
            # Step 3: Scale node pool up by 1, then rollback
            # -----------------------------------------------------------
            log("[OKE] Scaling node pool to 2")
            scale_cr = _run_cr(
                client, "[OKE] Scale node pool", "oci_scale_oke_node_pool",
                {"node_pool_id": node_pool_id, "node_count": 2},
                timeout=DAY2_TIMEOUT,
            )
            scale_result = _step_result(scale_cr)
            assert scale_result.get("scaled") is True, f"Scale result: {scale_result}"
            assert scale_result.get("new_count") == 2
            log("[OKE] Scale OK — rolling back to 1")
            _rollback(client, scale_cr["id"], "[OKE] rollback scale", timeout=DAY2_TIMEOUT)
            log("[OKE] Scale rollback OK")

            # -----------------------------------------------------------
            # Step 4: Update node pool name, then rollback
            # -----------------------------------------------------------
            new_pool_name = f"{cluster_name}-pool-renamed"
            log(f"[OKE] Updating node pool name to: {new_pool_name}")
            update_cr = _run_cr(
                client, "[OKE] Update node pool name", "oci_update_oke_node_pool",
                {"node_pool_id": node_pool_id, "name": new_pool_name},
                timeout=DAY2_TIMEOUT,
            )
            update_result = _step_result(update_cr)
            assert update_result.get("updated") is True, f"Update result: {update_result}"
            log("[OKE] Update OK — rolling back")
            _rollback(client, update_cr["id"], "[OKE] rollback update", timeout=DAY2_TIMEOUT)
            log("[OKE] Update rollback OK")

            # -----------------------------------------------------------
            # Step 5: Add second node pool
            # -----------------------------------------------------------
            pool2_name = f"{cluster_name}-pool-2"
            log(f"[OKE] Adding second node pool: {pool2_name}")
            add_cr = _run_cr(
                client, "[OKE] Add second node pool", "oci_add_oke_node_pool",
                {
                    "cluster_id": cluster_id,
                    "compartment_id": compartment_id,
                    "name": pool2_name,
                    "kubernetes_version": k8s_version,
                    "node_shape": "VM.Standard.E3.Flex",
                    "node_count": 1,
                    "subnet_id": subnet_id,
                },
                timeout=DAY2_TIMEOUT,
            )
            add_result = _step_result(add_cr)
            pool2_id = add_result.get("node_pool_id", "")
            assert pool2_id, f"node_pool_id missing from add result: {add_result}"
            log(f"[OKE] Second pool: {pool2_id[:40]}...")

            # -----------------------------------------------------------
            # Step 6: Delete second node pool
            # -----------------------------------------------------------
            log("[OKE] Deleting second node pool")
            del_pool_cr = _run_cr(
                client, "[OKE] Delete second node pool", "oci_delete_oke_node_pool",
                {"node_pool_id": pool2_id},
                timeout=DAY2_TIMEOUT,
            )
            del_pool_result = _step_result(del_pool_cr)
            assert del_pool_result.get("deleted") is True, f"Delete pool result: {del_pool_result}"
            assert "drain_recommended" in del_pool_result, f"drain_recommended key missing: {del_pool_result}"
            log(f"[OKE] Second pool deleted. drain_recommended={del_pool_result.get('drain_recommended')}")

        finally:
            # -----------------------------------------------------------
            # Step 7: Delete cluster (handles remaining node pool)
            # -----------------------------------------------------------
            log("[OKE] Deleting cluster (cleanup)")
            try:
                _run_cr(
                    client, "[OKE] Delete cluster", "oci_delete_oke_cluster",
                    {"cluster_id": cluster_id, "compartment_id": compartment_id},
                    timeout=DELETE_TIMEOUT,
                )
                log("[OKE] Cluster deleted")
            except Exception as e:
                log(f"[OKE] Cluster delete warning (non-fatal in finally): {e}", ok=False)

        log("[OKE] PASS — full lifecycle: create, kubeconfig, scale, update, add pool, delete pool, delete cluster")
```

- [ ] **Step 3: SCP files to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
    backend/tests/smoke/test_oci_parity_spec2_oke_smoke.py \
    backend/tests/smoke/smoke_helpers.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/
```

- [ ] **Step 4: Run smoke test on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -m pytest \
    /app/tests/smoke/test_oci_parity_spec2_oke_smoke.py \
    -v -s --timeout=2400 2>&1 | tee /tmp/oke_smoke.log"
```

Expected: `1 passed` in `TestOkeLifecycle`.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_oci_parity_spec2_oke_smoke.py \
        backend/tests/smoke/smoke_helpers.py
git commit -m "test(oci): OKE lifecycle smoke test — full create/day-2-ops/delete against live OCI tenancy"
```

- [ ] **Step 6: Push to master**

```bash
git push origin master
```
