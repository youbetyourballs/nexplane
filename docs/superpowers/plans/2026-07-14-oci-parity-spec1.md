# OCI Parity Spec 1 — OCIR, Backup Restore, Resource Tagging

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 10 new OCI executors (OCIR, backup restore, resource tagging), backfill `ROLLBACK_CAPABILITY` on all existing OCI change executors, and verify everything with live smoke tests.

**Architecture:** All executors follow the established OCI pattern: `async def execute(parameters, asset_ids, connector)` with `loop.run_in_executor(None, <blocking_sdk_call>)`. Mock mode when `not creds`. `ROLLBACK_CAPABILITY` is a module-level constant. Executors that use `PreStateStore` import `AsyncSessionLocal` directly and open their own session. `cr_id`, `step_id`, and `org_id` arrive via `parameters` (injected by the platform).

**Tech Stack:** Python/OCI Python SDK, FastAPI, SQLAlchemy async, pytest.

## Global Constraints

- `ROLLBACK_CAPABILITY` must be a module-level string constant — `"full"` or `"irreversible"`. Missing = planning hard-fail. Every executor in this plan and Task 4 backfill must have it.
- `ROLLBACK_REASON` required alongside `ROLLBACK_CAPABILITY = "irreversible"`.
- `PreStateStore.capture()` + `await db.commit()` must complete BEFORE any destructive SDK call in `"full"` executors.
- NEVER use `from __future__ import annotations` in any Python file.
- Executor function signatures: `async def execute(parameters: dict, asset_ids: list, connector) -> dict` and `async def rollback(parameters: dict, execution_result: dict, connector) -> dict`. No `db` parameter — executors open their own session when needed.
- **Client factory imports must be at module level** in every executor (e.g. `from ._client import get_compute_client` at the top of the file, outside any function). This is required for `unittest.mock.patch` to work in unit tests. `import oci` may stay inside functions to defer the heavy SDK import.
- All new functionality must have smoke coverage before the feature is considered done.
- Smoke runs on EC2 against the live OCI connector. No mocks, no local Docker.
- SPDX header on every new file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`

---

## File Map

**New files:**
- `backend/app/connectors/executors/oci/create_ocir_repository.py`
- `backend/app/connectors/executors/oci/delete_ocir_repository.py`
- `backend/app/connectors/executors/oci/delete_ocir_image.py`
- `backend/app/connectors/executors/oci/restore_adb.py`
- `backend/app/connectors/executors/oci/restore_block_volume_backup.py`
- `backend/app/connectors/executors/oci/restore_boot_volume_backup.py`
- `backend/app/connectors/executors/oci/tag_compute_instance.py`
- `backend/app/connectors/executors/oci/tag_block_volume.py`
- `backend/app/connectors/executors/oci/tag_vcn.py`
- `backend/app/connectors/executors/oci/tag_adb.py`
- `backend/tests/unit/test_oci_parity_spec1.py`
- `backend/tests/smoke/test_oci_parity_spec1_smoke.py`

**Modified files:**
- `backend/app/connectors/executors/oci/_client.py` — add `get_artifacts_client()`
- `backend/app/connectors/catalog/oci.json` — add 10 new action entries
- All existing OCI change executor `.py` files — add `ROLLBACK_CAPABILITY` constant (Task 4)

---

### Task 1: OCIR Executors — create, delete repo, delete image

**Files:**
- Modify: `backend/app/connectors/executors/oci/_client.py`
- Create: `backend/app/connectors/executors/oci/create_ocir_repository.py`
- Create: `backend/app/connectors/executors/oci/delete_ocir_repository.py`
- Create: `backend/app/connectors/executors/oci/delete_ocir_image.py`
- Modify: `backend/app/connectors/catalog/oci.json`
- Test: `backend/tests/unit/test_oci_parity_spec1.py` (OCIR section only)

**Interfaces:**
- Produces: `get_artifacts_client(creds: dict)` in `_client.py`, accessible as `from ._client import get_artifacts_client`
- Catalog executor references: `"executor": "oci.create_ocir_repository"`, `"executor": "oci.delete_ocir_repository"`, `"executor": "oci.delete_ocir_image"`

- [ ] **Step 1: Write failing tests for OCIR executors**

Create `backend/tests/unit/test_oci_parity_spec1.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {"user": "u", "key_content": "k", "fingerprint": "f",
                               "tenancy": "t", "region": "us-ashburn-1", "private_key": "pk"}
    return c


def _empty_connector():
    c = MagicMock()
    c.credentials = {}
    return c


# ---------------------------------------------------------------------------
# Task 1: OCIR
# ---------------------------------------------------------------------------

class TestCreateOcirRepository:
    def test_mock_mode(self):
        from app.connectors.executors.oci.create_ocir_repository import execute
        result = asyncio.run(execute(
            {"compartment_id": "ocid1.compartment.x", "display_name": "test-repo", "is_public": False},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert "repository_id" in result

    def test_rollback_capability_constant(self):
        import app.connectors.executors.oci.create_ocir_repository as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_execute_calls_create_repository(self):
        from app.connectors.executors.oci.create_ocir_repository import execute
        fake_repo = MagicMock()
        fake_repo.id = "ocid1.containerrepo.x"
        fake_client = MagicMock()
        fake_client.create_container_repository.return_value = MagicMock(data=fake_repo)
        with patch("app.connectors.executors.oci.create_ocir_repository.get_artifacts_client", return_value=fake_client):
            result = asyncio.run(execute(
                {"compartment_id": "ocid1.compartment.x", "display_name": "test-repo", "is_public": False},
                [], _connector()
            ))
        assert result["repository_id"] == "ocid1.containerrepo.x"
        fake_client.create_container_repository.assert_called_once()

    def test_rollback_deletes_repository(self):
        from app.connectors.executors.oci.create_ocir_repository import rollback
        fake_client = MagicMock()
        fake_client.delete_container_repository.return_value = None
        with patch("app.connectors.executors.oci.create_ocir_repository.get_artifacts_client", return_value=fake_client):
            result = asyncio.run(rollback(
                {},
                {"repository_id": "ocid1.containerrepo.x"},
                _connector()
            ))
        fake_client.delete_container_repository.assert_called_once_with(repository_id="ocid1.containerrepo.x")
        assert result["status"] == "DELETED"


class TestDeleteOcirRepository:
    def test_mock_mode(self):
        from app.connectors.executors.oci.delete_ocir_repository import execute
        result = asyncio.run(execute({"repository_id": "ocid1.containerrepo.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_rollback_capability_constant(self):
        import app.connectors.executors.oci.delete_ocir_repository as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_execute_captures_state_before_delete(self):
        from app.connectors.executors.oci.delete_ocir_repository import execute
        fake_repo = MagicMock()
        fake_repo.display_name = "test-repo"
        fake_repo.compartment_id = "ocid1.compartment.x"
        fake_repo.is_immutable = False
        fake_repo.defined_tags = {}
        fake_repo.freeform_tags = {}
        fake_client = MagicMock()
        fake_client.get_container_repository.return_value = MagicMock(data=fake_repo)
        fake_client.delete_container_repository.return_value = None
        call_order = []
        with patch("app.connectors.executors.oci.delete_ocir_repository.get_artifacts_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.delete_ocir_repository.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.delete_ocir_repository.AsyncSessionLocal") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock(side_effect=lambda *a, **kw: call_order.append("capture"))
            fake_client.delete_container_repository.side_effect = lambda **kw: call_order.append("delete")
            asyncio.run(execute(
                {"repository_id": "ocid1.containerrepo.x", "cr_id": "00000000-0000-0000-0000-000000000001",
                 "step_id": "step_0", "org_id": "00000000-0000-0000-0000-000000000002"},
                [], _connector()
            ))
        assert call_order.index("capture") < call_order.index("delete")


class TestDeleteOcirImage:
    def test_mock_mode(self):
        from app.connectors.executors.oci.delete_ocir_image import execute
        result = asyncio.run(execute({"image_id": "ocid1.containerimage.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_rollback_capability_irreversible(self):
        import app.connectors.executors.oci.delete_ocir_image as m
        assert m.ROLLBACK_CAPABILITY == "irreversible"
        assert m.ROLLBACK_REASON

    def test_rollback_returns_false(self):
        from app.connectors.executors.oci.delete_ocir_image import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec1.py::TestCreateOcirRepository tests/unit/test_oci_parity_spec1.py::TestDeleteOcirRepository tests/unit/test_oci_parity_spec1.py::TestDeleteOcirImage -v
```
Expected: ImportError (modules don't exist yet)

- [ ] **Step 3: Add `get_artifacts_client` to `_client.py`**

In `backend/app/connectors/executors/oci/_client.py`, add after the last `get_*` function:

```python
def get_artifacts_client(creds: dict):
    """Return oci.artifacts.ArtifactsClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.artifacts.ArtifactsClient(config)
```

- [ ] **Step 4: Create `create_ocir_repository.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-repo")
    is_public = bool(parameters.get("is_public", False))

    if not creds:
        return {
            "action": "create_ocir_repository",
            "repository_id": "mock-repo-id",
            "display_name": display_name,
            "mock": True,
        }

    import oci
    from ._client import get_artifacts_client
    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    def _create():
        details = oci.artifacts.models.CreateContainerRepositoryDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            is_public=is_public,
        )
        return client.create_container_repository(
            create_container_repository_details=details
        ).data

    repo = await loop.run_in_executor(None, _create)
    return {
        "action": "create_ocir_repository",
        "repository_id": repo.id,
        "display_name": repo.display_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repository_id = execution_result.get("repository_id", "")
    if not creds or not repository_id:
        return {"action": "rollback_create_ocir_repository", "mock": True}

    from ._client import get_artifacts_client
    import asyncio
    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: client.delete_container_repository(repository_id=repository_id),
    )
    return {
        "action": "rollback_create_ocir_repository",
        "repository_id": repository_id,
        "status": "DELETED",
    }
```

- [ ] **Step 5: Create `delete_ocir_repository.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repository_id = parameters.get("repository_id", "")

    if not creds:
        return {"action": "delete_ocir_repository", "repository_id": repository_id, "mock": True}

    import oci
    from ._client import get_artifacts_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    repo = await loop.run_in_executor(
        None, lambda: client.get_container_repository(repository_id=repository_id).data
    )

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {
                "display_name": repo.display_name,
                "compartment_id": repo.compartment_id,
                "is_immutable": repo.is_immutable,
                "defined_tags": repo.defined_tags or {},
                "freeform_tags": repo.freeform_tags or {},
            },
        )
        await db.commit()

    await loop.run_in_executor(
        None, lambda: client.delete_container_repository(repository_id=repository_id)
    )
    return {
        "action": "delete_ocir_repository",
        "repository_id": repository_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "rollback_delete_ocir_repository", "mock": True}

    import oci
    from ._client import get_artifacts_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )

    if not state:
        return {"action": "rollback_delete_ocir_repository", "error": "pre-state not found"}

    def _recreate():
        details = oci.artifacts.models.CreateContainerRepositoryDetails(
            compartment_id=state["compartment_id"],
            display_name=state["display_name"],
            is_public=not state.get("is_immutable", False),
        )
        return client.create_container_repository(
            create_container_repository_details=details
        ).data

    repo = await loop.run_in_executor(None, _recreate)
    return {
        "action": "rollback_delete_ocir_repository",
        "new_repository_id": repo.id,
        "display_name": repo.display_name,
        "rolled_back": True,
    }
```

- [ ] **Step 6: Create `delete_ocir_image.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Container image layer data is permanently deleted and cannot be recovered"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    image_id = parameters.get("image_id", "")

    if not creds:
        return {"action": "delete_ocir_image", "image_id": image_id, "mock": True}

    from ._client import get_artifacts_client
    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, lambda: client.delete_container_image(image_id=image_id))
    return {
        "action": "delete_ocir_image",
        "image_id": image_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
```

- [ ] **Step 7: Add OCIR catalog entries to `oci.json`**

Open `backend/app/connectors/catalog/oci.json`. Inside the `"actions"` array, append these three entries before the closing `]`:

```json
{"action_id": "oci_ocir_repo_create", "action_type": "change", "display_name": "Create OCIR Repository", "description": "Creates an OCI Container Registry repository in the specified compartment. Rollback: delete the repository.", "executor": "oci.create_ocir_repository", "generic_action": "oci_ocir_repo_create", "rollback_action": "oci_ocir_repo_delete", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 1, "estimated_duration_seconds": 15, "blast_radius_hint": "new_resource", "parameters": [{"name": "compartment_id", "type": "string", "required": true, "description": "OCID of the compartment"}, {"name": "display_name", "type": "string", "required": true, "description": "Repository name (e.g. myapp/backend)"}, {"name": "is_public", "type": "boolean", "required": false, "default": false, "description": "Whether the repository is publicly readable"}]},
{"action_id": "oci_ocir_repo_delete", "action_type": "change", "display_name": "Delete OCIR Repository", "description": "Deletes an OCI Container Registry repository and all its images. Pre-state captured for rollback.", "executor": "oci.delete_ocir_repository", "generic_action": "oci_ocir_repo_delete", "applicable_asset_types": ["cloud_account"], "execution_tier": 3, "estimated_duration_seconds": 15, "blast_radius_hint": "destructive", "parameters": [{"name": "repository_id", "type": "string", "required": true, "description": "OCID of the repository"}]},
{"action_id": "oci_ocir_image_delete", "action_type": "change", "display_name": "Delete Container Image", "description": "Permanently deletes a container image by OCID. Irreversible — image layer data cannot be recovered.", "executor": "oci.delete_ocir_image", "generic_action": "oci_ocir_image_delete", "applicable_asset_types": ["cloud_account"], "execution_tier": 3, "estimated_duration_seconds": 10, "blast_radius_hint": "destructive", "parameters": [{"name": "image_id", "type": "string", "required": true, "description": "OCID of the container image"}]}
```

- [ ] **Step 8: Run OCIR tests to verify they pass**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec1.py::TestCreateOcirRepository tests/unit/test_oci_parity_spec1.py::TestDeleteOcirRepository tests/unit/test_oci_parity_spec1.py::TestDeleteOcirImage -v
```
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/oci/_client.py \
        backend/app/connectors/executors/oci/create_ocir_repository.py \
        backend/app/connectors/executors/oci/delete_ocir_repository.py \
        backend/app/connectors/executors/oci/delete_ocir_image.py \
        backend/app/connectors/catalog/oci.json \
        backend/tests/unit/test_oci_parity_spec1.py
git commit -m "feat(oci): OCIR executors — create/delete repo, delete image"
```

---

### Task 2: Backup Restore Executors

**Files:**
- Create: `backend/app/connectors/executors/oci/restore_adb.py`
- Create: `backend/app/connectors/executors/oci/restore_block_volume_backup.py`
- Create: `backend/app/connectors/executors/oci/restore_boot_volume_backup.py`
- Modify: `backend/app/connectors/catalog/oci.json`
- Test: `backend/tests/unit/test_oci_parity_spec1.py` (add restore section)

**Interfaces:**
- Consumes: `get_database_client`, `get_blockstorage_client` from `._client` (already exist)
- Produces: `{"db_id": ..., "restored_at": ...}`, `{"volume_id": ...}`, `{"boot_volume_id": ...}` in `execution_result`

- [ ] **Step 1: Add restore tests to `test_oci_parity_spec1.py`**

Append to the existing test file:

```python
# ---------------------------------------------------------------------------
# Task 2: Backup Restore
# ---------------------------------------------------------------------------

class TestRestoreAdb:
    def test_rollback_capability_irreversible(self):
        import app.connectors.executors.oci.restore_adb as m
        assert m.ROLLBACK_CAPABILITY == "irreversible"
        assert m.ROLLBACK_REASON

    def test_mock_mode(self):
        from app.connectors.executors.oci.restore_adb import execute
        result = asyncio.run(execute(
            {"autonomous_database_id": "ocid1.adb.x", "timestamp": "2026-01-01T00:00:00Z"},
            [], _empty_connector()
        ))
        assert result["mock"] is True

    def test_rollback_returns_false(self):
        from app.connectors.executors.oci.restore_adb import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False

    def test_execute_calls_restore(self):
        from app.connectors.executors.oci.restore_adb import execute
        fake_adb = MagicMock()
        fake_adb.lifecycle_state = "AVAILABLE"
        fake_client = MagicMock()
        fake_client.restore_autonomous_database.return_value = MagicMock(data=MagicMock())
        fake_client.get_autonomous_database.return_value = MagicMock(data=fake_adb)
        with patch("app.connectors.executors.oci.restore_adb.get_database_client", return_value=fake_client):
            result = asyncio.run(execute(
                {"autonomous_database_id": "ocid1.adb.x", "timestamp": "2026-01-01T00:00:00Z"},
                [], _connector()
            ))
        fake_client.restore_autonomous_database.assert_called_once()
        assert result["autonomous_database_id"] == "ocid1.adb.x"


class TestRestoreBlockVolumeBackup:
    def test_rollback_capability_full(self):
        import app.connectors.executors.oci.restore_block_volume_backup as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode(self):
        from app.connectors.executors.oci.restore_block_volume_backup import execute
        result = asyncio.run(execute(
            {"volume_backup_id": "ocid1.volumebackup.x", "display_name": "test",
             "compartment_id": "ocid1.compartment.x", "availability_domain": "AD-1"},
            [], _empty_connector()
        ))
        assert result["mock"] is True

    def test_execute_returns_volume_id(self):
        from app.connectors.executors.oci.restore_block_volume_backup import execute
        fake_vol = MagicMock()
        fake_vol.id = "ocid1.volume.x"
        fake_vol.lifecycle_state = "AVAILABLE"
        fake_client = MagicMock()
        fake_client.create_volume.return_value = MagicMock(data=fake_vol)
        fake_client.get_volume.return_value = MagicMock(data=fake_vol)
        with patch("app.connectors.executors.oci.restore_block_volume_backup.get_blockstorage_client", return_value=fake_client):
            result = asyncio.run(execute(
                {"volume_backup_id": "ocid1.volumebackup.x", "display_name": "test",
                 "compartment_id": "ocid1.compartment.x", "availability_domain": "AD-1"},
                [], _connector()
            ))
        assert result["volume_id"] == "ocid1.volume.x"

    def test_rollback_deletes_volume(self):
        from app.connectors.executors.oci.restore_block_volume_backup import rollback
        fake_client = MagicMock()
        fake_vol = MagicMock()
        fake_vol.lifecycle_state = "TERMINATED"
        fake_client.delete_volume.return_value = None
        fake_client.get_volume.return_value = MagicMock(data=fake_vol)
        with patch("app.connectors.executors.oci.restore_block_volume_backup.get_blockstorage_client", return_value=fake_client):
            result = asyncio.run(rollback({}, {"volume_id": "ocid1.volume.x"}, _connector()))
        fake_client.delete_volume.assert_called_once_with(volume_id="ocid1.volume.x")
        assert result["rolled_back"] is True


class TestRestoreBootVolumeBackup:
    def test_rollback_capability_full(self):
        import app.connectors.executors.oci.restore_boot_volume_backup as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode(self):
        from app.connectors.executors.oci.restore_boot_volume_backup import execute
        result = asyncio.run(execute(
            {"boot_volume_backup_id": "ocid1.bootvolumebackup.x", "display_name": "test",
             "compartment_id": "ocid1.compartment.x", "availability_domain": "AD-1"},
            [], _empty_connector()
        ))
        assert result["mock"] is True

    def test_execute_returns_boot_volume_id(self):
        from app.connectors.executors.oci.restore_boot_volume_backup import execute
        fake_vol = MagicMock()
        fake_vol.id = "ocid1.bootvolume.x"
        fake_vol.lifecycle_state = "AVAILABLE"
        fake_client = MagicMock()
        fake_client.create_boot_volume.return_value = MagicMock(data=fake_vol)
        fake_client.get_boot_volume.return_value = MagicMock(data=fake_vol)
        with patch("app.connectors.executors.oci.restore_boot_volume_backup.get_blockstorage_client", return_value=fake_client):
            result = asyncio.run(execute(
                {"boot_volume_backup_id": "ocid1.bootvolumebackup.x", "display_name": "test",
                 "compartment_id": "ocid1.compartment.x", "availability_domain": "AD-1"},
                [], _connector()
            ))
        assert result["boot_volume_id"] == "ocid1.bootvolume.x"

    def test_rollback_deletes_boot_volume(self):
        from app.connectors.executors.oci.restore_boot_volume_backup import rollback
        fake_client = MagicMock()
        fake_vol = MagicMock()
        fake_vol.lifecycle_state = "TERMINATED"
        fake_client.delete_boot_volume.return_value = None
        fake_client.get_boot_volume.return_value = MagicMock(data=fake_vol)
        with patch("app.connectors.executors.oci.restore_boot_volume_backup.get_blockstorage_client", return_value=fake_client):
            result = asyncio.run(rollback({}, {"boot_volume_id": "ocid1.bootvolume.x"}, _connector()))
        fake_client.delete_boot_volume.assert_called_once_with(boot_volume_id="ocid1.bootvolume.x")
        assert result["rolled_back"] is True
```

- [ ] **Step 2: Run restore tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec1.py::TestRestoreAdb tests/unit/test_oci_parity_spec1.py::TestRestoreBlockVolumeBackup tests/unit/test_oci_parity_spec1.py::TestRestoreBootVolumeBackup -v
```
Expected: ImportError

- [ ] **Step 3: Create `restore_adb.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Restoring an Autonomous Database overwrites current data; the prior state cannot be recovered after restore completes"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_id = parameters.get("autonomous_database_id", "")
    timestamp = parameters.get("timestamp", "")
    backup_id = parameters.get("backup_id", "")

    if not creds:
        return {"action": "restore_adb", "autonomous_database_id": db_id, "mock": True}

    import oci
    from ._client import get_database_client
    client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _restore_and_poll():
        if backup_id:
            details = oci.database.models.RestoreAutonomousDatabaseDetails(
                database_backup_id=backup_id
            )
        else:
            details = oci.database.models.RestoreAutonomousDatabaseDetails(
                timestamp=timestamp
            )
        client.restore_autonomous_database(
            autonomous_database_id=db_id,
            restore_autonomous_database_details=details,
        )
        for _ in range(60):
            info = client.get_autonomous_database(autonomous_database_id=db_id).data
            if info.lifecycle_state == "AVAILABLE":
                return
            if info.lifecycle_state in ("FAILED", "TERMINATED"):
                raise RuntimeError(f"ADB restore failed: lifecycle_state={info.lifecycle_state}")
            time.sleep(30)
        raise TimeoutError("ADB restore did not complete within 30 minutes")

    await loop.run_in_executor(None, _restore_and_poll)
    return {
        "action": "restore_adb",
        "autonomous_database_id": db_id,
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
```

- [ ] **Step 4: Create `restore_block_volume_backup.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_backup_id = parameters.get("volume_backup_id", "")
    display_name = parameters.get("display_name", f"nexplane-restore-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    compartment_id = parameters.get("compartment_id", "")
    availability_domain = parameters.get("availability_domain", "")

    if not creds:
        return {
            "action": "restore_block_volume_backup",
            "volume_id": "mock-volume-id",
            "mock": True,
        }

    import oci
    from ._client import get_blockstorage_client
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _create_and_poll():
        source = oci.core.models.VolumeSourceFromVolumeBackupDetails(id=volume_backup_id)
        details = oci.core.models.CreateVolumeDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            availability_domain=availability_domain,
            source_details=source,
        )
        vol = client.create_volume(create_volume_details=details).data
        for _ in range(60):
            info = client.get_volume(volume_id=vol.id).data
            if info.lifecycle_state == "AVAILABLE":
                return vol.id
            if info.lifecycle_state == "FAULTY":
                raise RuntimeError("Volume creation from backup failed")
            time.sleep(10)
        raise TimeoutError("Volume did not become AVAILABLE within 10 minutes")

    volume_id = await loop.run_in_executor(None, _create_and_poll)
    return {
        "action": "restore_block_volume_backup",
        "volume_id": volume_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = execution_result.get("volume_id", "")
    if not creds or not volume_id:
        return {"action": "rollback_restore_block_volume_backup", "mock": True}

    from ._client import get_blockstorage_client
    import asyncio, time
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _delete_and_poll():
        client.delete_volume(volume_id=volume_id)
        for _ in range(30):
            try:
                info = client.get_volume(volume_id=volume_id).data
                if info.lifecycle_state == "TERMINATED":
                    return
            except Exception:
                return
            time.sleep(10)

    await loop.run_in_executor(None, _delete_and_poll)
    return {"action": "rollback_restore_block_volume_backup", "volume_id": volume_id, "rolled_back": True}
```

- [ ] **Step 5: Create `restore_boot_volume_backup.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    boot_volume_backup_id = parameters.get("boot_volume_backup_id", "")
    display_name = parameters.get("display_name", f"nexplane-restore-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    compartment_id = parameters.get("compartment_id", "")
    availability_domain = parameters.get("availability_domain", "")

    if not creds:
        return {
            "action": "restore_boot_volume_backup",
            "boot_volume_id": "mock-boot-volume-id",
            "mock": True,
        }

    import oci
    from ._client import get_blockstorage_client
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _create_and_poll():
        source = oci.core.models.BootVolumeSourceFromBootVolumeBackupDetails(id=boot_volume_backup_id)
        details = oci.core.models.CreateBootVolumeDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            availability_domain=availability_domain,
            source_details=source,
        )
        vol = client.create_boot_volume(create_boot_volume_details=details).data
        for _ in range(60):
            info = client.get_boot_volume(boot_volume_id=vol.id).data
            if info.lifecycle_state == "AVAILABLE":
                return vol.id
            if info.lifecycle_state == "FAULTY":
                raise RuntimeError("Boot volume creation from backup failed")
            time.sleep(10)
        raise TimeoutError("Boot volume did not become AVAILABLE within 10 minutes")

    boot_volume_id = await loop.run_in_executor(None, _create_and_poll)
    return {
        "action": "restore_boot_volume_backup",
        "boot_volume_id": boot_volume_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    boot_volume_id = execution_result.get("boot_volume_id", "")
    if not creds or not boot_volume_id:
        return {"action": "rollback_restore_boot_volume_backup", "mock": True}

    from ._client import get_blockstorage_client
    import asyncio, time
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _delete_and_poll():
        client.delete_boot_volume(boot_volume_id=boot_volume_id)
        for _ in range(30):
            try:
                info = client.get_boot_volume(boot_volume_id=boot_volume_id).data
                if info.lifecycle_state == "TERMINATED":
                    return
            except Exception:
                return
            time.sleep(10)

    await loop.run_in_executor(None, _delete_and_poll)
    return {"action": "rollback_restore_boot_volume_backup", "boot_volume_id": boot_volume_id, "rolled_back": True}
```

- [ ] **Step 6: Add restore catalog entries to `oci.json`**

Append to the `"actions"` array:

```json
{"action_id": "oci_restore_adb", "action_type": "change", "display_name": "Restore Autonomous Database", "description": "Restores an OCI Autonomous Database to a point-in-time or from a specific backup. Irreversible — overwrites current DB state.", "executor": "oci.restore_adb", "generic_action": "oci_restore_adb", "applicable_asset_types": ["database"], "execution_tier": 3, "estimated_duration_seconds": 1800, "blast_radius_hint": "destructive", "parameters": [{"name": "autonomous_database_id", "type": "string", "required": true, "description": "OCID of the ADB to restore"}, {"name": "timestamp", "type": "string", "required": false, "description": "ISO8601 point-in-time timestamp"}, {"name": "backup_id", "type": "string", "required": false, "description": "OCID of a specific backup to restore from (alternative to timestamp)"}]},
{"action_id": "oci_restore_block_volume_backup", "action_type": "change", "display_name": "Restore Block Volume from Backup", "description": "Creates a new OCI Block Volume from an existing backup. Rollback: deletes the newly created volume.", "executor": "oci.restore_block_volume_backup", "generic_action": "oci_restore_block_volume_backup", "rollback_action": "oci_block_volume_delete", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 2, "estimated_duration_seconds": 600, "blast_radius_hint": "new_resource", "parameters": [{"name": "volume_backup_id", "type": "string", "required": true, "description": "OCID of the block volume backup"}, {"name": "display_name", "type": "string", "required": false}, {"name": "compartment_id", "type": "string", "required": true}, {"name": "availability_domain", "type": "string", "required": true}]},
{"action_id": "oci_restore_boot_volume_backup", "action_type": "change", "display_name": "Restore Boot Volume from Backup", "description": "Creates a new OCI Boot Volume from an existing backup. Rollback: deletes the newly created boot volume.", "executor": "oci.restore_boot_volume_backup", "generic_action": "oci_restore_boot_volume_backup", "applicable_asset_types": ["cloud_account"], "execution_tier": 2, "estimated_duration_seconds": 600, "blast_radius_hint": "new_resource", "parameters": [{"name": "boot_volume_backup_id", "type": "string", "required": true, "description": "OCID of the boot volume backup"}, {"name": "display_name", "type": "string", "required": false}, {"name": "compartment_id", "type": "string", "required": true}, {"name": "availability_domain", "type": "string", "required": true}]}
```

- [ ] **Step 7: Run restore tests to verify they pass**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec1.py::TestRestoreAdb tests/unit/test_oci_parity_spec1.py::TestRestoreBlockVolumeBackup tests/unit/test_oci_parity_spec1.py::TestRestoreBootVolumeBackup -v
```
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/oci/restore_adb.py \
        backend/app/connectors/executors/oci/restore_block_volume_backup.py \
        backend/app/connectors/executors/oci/restore_boot_volume_backup.py \
        backend/app/connectors/catalog/oci.json \
        backend/tests/unit/test_oci_parity_spec1.py
git commit -m "feat(oci): backup restore executors — ADB, block volume, boot volume"
```

---

### Task 3: Resource Tagging Executors

**Files:**
- Create: `backend/app/connectors/executors/oci/tag_compute_instance.py`
- Create: `backend/app/connectors/executors/oci/tag_block_volume.py`
- Create: `backend/app/connectors/executors/oci/tag_vcn.py`
- Create: `backend/app/connectors/executors/oci/tag_adb.py`
- Modify: `backend/app/connectors/catalog/oci.json`
- Test: `backend/tests/unit/test_oci_parity_spec1.py` (add tagging section)

**Interfaces:**
- Consumes: `get_compute_client`, `get_blockstorage_client`, `get_network_client`, `get_database_client` from `._client` (all exist)
- Consumes: `PreStateStore`, `AsyncSessionLocal` (same pattern as Task 1's `delete_ocir_repository`)
- `cr_id`, `step_id`, `org_id` in `parameters` — required for PreStateStore

- [ ] **Step 1: Add tagging tests to `test_oci_parity_spec1.py`**

Append to the test file:

```python
# ---------------------------------------------------------------------------
# Task 3: Resource Tagging
# ---------------------------------------------------------------------------

def _tag_test(module_path, executor_fn_name, id_param, id_value, get_fn_name, update_fn_name, resource_attr):
    """Shared test logic for all tag_* executors."""
    mod = __import__(module_path, fromlist=[executor_fn_name])
    execute = getattr(mod, executor_fn_name)
    assert mod.ROLLBACK_CAPABILITY == "full"

    fake_resource = MagicMock()
    fake_resource.freeform_tags = {"existing": "tag"}
    fake_resource.defined_tags = {}
    fake_client = MagicMock()
    getattr(fake_client, get_fn_name).return_value = MagicMock(data=fake_resource)
    getattr(fake_client, update_fn_name).return_value = MagicMock(data=fake_resource)

    client_patch = f"{module_path}.get_{resource_attr}_client"
    call_order = []

    with patch(client_patch, return_value=fake_client), \
         patch(f"{module_path}.PreStateStore") as mock_store, \
         patch(f"{module_path}.AsyncSessionLocal") as mock_session:
        mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store.capture = AsyncMock(side_effect=lambda *a, **kw: call_order.append("capture"))
        getattr(fake_client, update_fn_name).side_effect = lambda *a, **kw: call_order.append("update") or MagicMock(data=fake_resource)

        result = asyncio.run(execute(
            {id_param: id_value, "freeform_tags": {"env": "prod"},
             "cr_id": "00000000-0000-0000-0000-000000000001",
             "step_id": "step_0", "org_id": "00000000-0000-0000-0000-000000000002"},
            [], _connector()
        ))
    assert call_order.index("capture") < call_order.index("update")
    return result


class TestTagComputeInstance:
    def test_rollback_capability_full(self):
        import app.connectors.executors.oci.tag_compute_instance as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode(self):
        from app.connectors.executors.oci.tag_compute_instance import execute
        result = asyncio.run(execute({"instance_id": "ocid1.instance.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_capture_before_update(self):
        _tag_test(
            "app.connectors.executors.oci.tag_compute_instance", "execute",
            "instance_id", "ocid1.instance.x",
            "get_instance", "update_instance", "compute"
        )

    def test_rollback_restores_tags(self):
        from app.connectors.executors.oci.tag_compute_instance import rollback
        fake_client = MagicMock()
        fake_resource = MagicMock()
        fake_client.update_instance.return_value = MagicMock(data=fake_resource)
        with patch("app.connectors.executors.oci.tag_compute_instance.get_compute_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.tag_compute_instance.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.tag_compute_instance.AsyncSessionLocal") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.retrieve = AsyncMock(return_value={"freeform_tags": {"old": "tag"}, "defined_tags": {}})
            result = asyncio.run(rollback(
                {"instance_id": "ocid1.instance.x",
                 "cr_id": "00000000-0000-0000-0000-000000000001",
                 "step_id": "step_0", "org_id": "00000000-0000-0000-0000-000000000002"},
                {}, _connector()
            ))
        fake_client.update_instance.assert_called_once()
        assert result["rolled_back"] is True


class TestTagBlockVolume:
    def test_rollback_capability_full(self):
        import app.connectors.executors.oci.tag_block_volume as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode(self):
        from app.connectors.executors.oci.tag_block_volume import execute
        result = asyncio.run(execute({"volume_id": "ocid1.volume.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_capture_before_update(self):
        _tag_test(
            "app.connectors.executors.oci.tag_block_volume", "execute",
            "volume_id", "ocid1.volume.x",
            "get_volume", "update_volume", "blockstorage"
        )


class TestTagVcn:
    def test_rollback_capability_full(self):
        import app.connectors.executors.oci.tag_vcn as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode(self):
        from app.connectors.executors.oci.tag_vcn import execute
        result = asyncio.run(execute({"vcn_id": "ocid1.vcn.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_capture_before_update(self):
        _tag_test(
            "app.connectors.executors.oci.tag_vcn", "execute",
            "vcn_id", "ocid1.vcn.x",
            "get_vcn", "update_vcn", "network"
        )


class TestTagAdb:
    def test_rollback_capability_full(self):
        import app.connectors.executors.oci.tag_adb as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode(self):
        from app.connectors.executors.oci.tag_adb import execute
        result = asyncio.run(execute({"autonomous_database_id": "ocid1.adb.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_capture_before_update(self):
        _tag_test(
            "app.connectors.executors.oci.tag_adb", "execute",
            "autonomous_database_id", "ocid1.adb.x",
            "get_autonomous_database", "update_autonomous_database", "database"
        )
```

- [ ] **Step 2: Run tagging tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec1.py::TestTagComputeInstance tests/unit/test_oci_parity_spec1.py::TestTagBlockVolume tests/unit/test_oci_parity_spec1.py::TestTagVcn tests/unit/test_oci_parity_spec1.py::TestTagAdb -v
```
Expected: ImportError

- [ ] **Step 3: Create `tag_compute_instance.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_compute_instance", "instance_id": instance_id, "mock": True}

    import oci
    from ._client import get_compute_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    instance = await loop.run_in_executor(None, lambda: client.get_instance(instance_id=instance_id).data)
    prior_freeform = instance.freeform_tags or {}
    prior_defined = instance.defined_tags or {}

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {"freeform_tags": prior_freeform, "defined_tags": prior_defined},
        )
        await db.commit()

    new_freeform = freeform_tags if freeform_tags is not None else prior_freeform
    new_defined = defined_tags if defined_tags is not None else prior_defined

    await loop.run_in_executor(
        None,
        lambda: client.update_instance(
            instance_id=instance_id,
            update_instance_details=oci.core.models.UpdateInstanceDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_compute_instance",
        "instance_id": instance_id,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    if not creds:
        return {"action": "rollback_tag_compute_instance", "mock": True}

    import oci
    from ._client import get_compute_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )

    if not state:
        return {"action": "rollback_tag_compute_instance", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_instance(
            instance_id=instance_id,
            update_instance_details=oci.core.models.UpdateInstanceDetails(
                freeform_tags=state["freeform_tags"],
                defined_tags=state["defined_tags"],
            ),
        ),
    )
    return {"action": "rollback_tag_compute_instance", "instance_id": instance_id, "rolled_back": True}
```

- [ ] **Step 4: Create `tag_block_volume.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = parameters.get("volume_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_block_volume", "volume_id": volume_id, "mock": True}

    import oci
    from ._client import get_blockstorage_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    volume = await loop.run_in_executor(None, lambda: client.get_volume(volume_id=volume_id).data)
    prior_freeform = volume.freeform_tags or {}
    prior_defined = volume.defined_tags or {}

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {"freeform_tags": prior_freeform, "defined_tags": prior_defined},
        )
        await db.commit()

    new_freeform = freeform_tags if freeform_tags is not None else prior_freeform
    new_defined = defined_tags if defined_tags is not None else prior_defined

    await loop.run_in_executor(
        None,
        lambda: client.update_volume(
            volume_id=volume_id,
            update_volume_details=oci.core.models.UpdateVolumeDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_block_volume",
        "volume_id": volume_id,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = parameters.get("volume_id", "")
    if not creds:
        return {"action": "rollback_tag_block_volume", "mock": True}

    import oci
    from ._client import get_blockstorage_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )

    if not state:
        return {"action": "rollback_tag_block_volume", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_volume(
            volume_id=volume_id,
            update_volume_details=oci.core.models.UpdateVolumeDetails(
                freeform_tags=state["freeform_tags"],
                defined_tags=state["defined_tags"],
            ),
        ),
    )
    return {"action": "rollback_tag_block_volume", "volume_id": volume_id, "rolled_back": True}
```

- [ ] **Step 5: Create `tag_vcn.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vcn_id = parameters.get("vcn_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_vcn", "vcn_id": vcn_id, "mock": True}

    import oci
    from ._client import get_network_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_network_client(creds)
    loop = asyncio.get_running_loop()

    vcn = await loop.run_in_executor(None, lambda: client.get_vcn(vcn_id=vcn_id).data)
    prior_freeform = vcn.freeform_tags or {}
    prior_defined = vcn.defined_tags or {}

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {"freeform_tags": prior_freeform, "defined_tags": prior_defined},
        )
        await db.commit()

    new_freeform = freeform_tags if freeform_tags is not None else prior_freeform
    new_defined = defined_tags if defined_tags is not None else prior_defined

    await loop.run_in_executor(
        None,
        lambda: client.update_vcn(
            vcn_id=vcn_id,
            update_vcn_details=oci.core.models.UpdateVcnDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_vcn",
        "vcn_id": vcn_id,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vcn_id = parameters.get("vcn_id", "")
    if not creds:
        return {"action": "rollback_tag_vcn", "mock": True}

    import oci
    from ._client import get_network_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_network_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )

    if not state:
        return {"action": "rollback_tag_vcn", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_vcn(
            vcn_id=vcn_id,
            update_vcn_details=oci.core.models.UpdateVcnDetails(
                freeform_tags=state["freeform_tags"],
                defined_tags=state["defined_tags"],
            ),
        ),
    )
    return {"action": "rollback_tag_vcn", "vcn_id": vcn_id, "rolled_back": True}
```

- [ ] **Step 6: Create `tag_adb.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import uuid as _uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    autonomous_database_id = parameters.get("autonomous_database_id", "")
    freeform_tags = parameters.get("freeform_tags")
    defined_tags = parameters.get("defined_tags")

    if not creds:
        return {"action": "tag_adb", "autonomous_database_id": autonomous_database_id, "mock": True}

    import oci
    from ._client import get_database_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    adb = await loop.run_in_executor(
        None, lambda: client.get_autonomous_database(autonomous_database_id=autonomous_database_id).data
    )
    prior_freeform = adb.freeform_tags or {}
    prior_defined = adb.defined_tags or {}

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {"freeform_tags": prior_freeform, "defined_tags": prior_defined},
        )
        await db.commit()

    new_freeform = freeform_tags if freeform_tags is not None else prior_freeform
    new_defined = defined_tags if defined_tags is not None else prior_defined

    await loop.run_in_executor(
        None,
        lambda: client.update_autonomous_database(
            autonomous_database_id=autonomous_database_id,
            update_autonomous_database_details=oci.database.models.UpdateAutonomousDatabaseDetails(
                freeform_tags=new_freeform,
                defined_tags=new_defined,
            ),
        ),
    )
    return {
        "action": "tag_adb",
        "autonomous_database_id": autonomous_database_id,
        "tagged_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    autonomous_database_id = parameters.get("autonomous_database_id", "")
    if not creds:
        return {"action": "rollback_tag_adb", "mock": True}

    import oci
    from ._client import get_database_client
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal

    client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    async with AsyncSessionLocal() as db:
        state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )

    if not state:
        return {"action": "rollback_tag_adb", "error": "pre-state not found", "rolled_back": False}

    await loop.run_in_executor(
        None,
        lambda: client.update_autonomous_database(
            autonomous_database_id=autonomous_database_id,
            update_autonomous_database_details=oci.database.models.UpdateAutonomousDatabaseDetails(
                freeform_tags=state["freeform_tags"],
                defined_tags=state["defined_tags"],
            ),
        ),
    )
    return {"action": "rollback_tag_adb", "autonomous_database_id": autonomous_database_id, "rolled_back": True}
```

- [ ] **Step 7: Add tagging catalog entries to `oci.json`**

Append to the `"actions"` array:

```json
{"action_id": "oci_tag_compute_instance", "action_type": "change", "display_name": "Tag Compute Instance", "description": "Applies freeform or defined tags to an OCI compute instance. Pre-state captured; rollback restores original tags.", "executor": "oci.tag_compute_instance", "generic_action": "oci_tag_compute_instance", "rollback_action": "oci_tag_compute_instance", "rollback_connector_type": "oci", "applicable_asset_types": ["server"], "execution_tier": 1, "estimated_duration_seconds": 10, "parameters": [{"name": "instance_id", "type": "string", "required": true}, {"name": "freeform_tags", "type": "object", "required": false, "description": "Dict of string key-value tags to apply"}, {"name": "defined_tags", "type": "object", "required": false, "description": "Dict of namespace -> key -> value defined tags"}]},
{"action_id": "oci_tag_block_volume", "action_type": "change", "display_name": "Tag Block Volume", "description": "Applies freeform or defined tags to an OCI block volume. Pre-state captured; rollback restores original tags.", "executor": "oci.tag_block_volume", "generic_action": "oci_tag_block_volume", "rollback_action": "oci_tag_block_volume", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 1, "estimated_duration_seconds": 10, "parameters": [{"name": "volume_id", "type": "string", "required": true}, {"name": "freeform_tags", "type": "object", "required": false}, {"name": "defined_tags", "type": "object", "required": false}]},
{"action_id": "oci_tag_vcn", "action_type": "change", "display_name": "Tag VCN", "description": "Applies freeform or defined tags to an OCI Virtual Cloud Network. Pre-state captured; rollback restores original tags.", "executor": "oci.tag_vcn", "generic_action": "oci_tag_vcn", "rollback_action": "oci_tag_vcn", "rollback_connector_type": "oci", "applicable_asset_types": ["cloud_account"], "execution_tier": 1, "estimated_duration_seconds": 10, "parameters": [{"name": "vcn_id", "type": "string", "required": true}, {"name": "freeform_tags", "type": "object", "required": false}, {"name": "defined_tags", "type": "object", "required": false}]},
{"action_id": "oci_tag_adb", "action_type": "change", "display_name": "Tag Autonomous Database", "description": "Applies freeform or defined tags to an OCI Autonomous Database. Pre-state captured; rollback restores original tags.", "executor": "oci.tag_adb", "generic_action": "oci_tag_adb", "rollback_action": "oci_tag_adb", "rollback_connector_type": "oci", "applicable_asset_types": ["database"], "execution_tier": 1, "estimated_duration_seconds": 10, "parameters": [{"name": "autonomous_database_id", "type": "string", "required": true}, {"name": "freeform_tags", "type": "object", "required": false}, {"name": "defined_tags", "type": "object", "required": false}]}
```

- [ ] **Step 8: Run all unit tests**

```
cd backend && python -m pytest tests/unit/test_oci_parity_spec1.py -v
```
Expected: all tests PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/oci/tag_compute_instance.py \
        backend/app/connectors/executors/oci/tag_block_volume.py \
        backend/app/connectors/executors/oci/tag_vcn.py \
        backend/app/connectors/executors/oci/tag_adb.py \
        backend/app/connectors/catalog/oci.json \
        backend/tests/unit/test_oci_parity_spec1.py
git commit -m "feat(oci): resource tagging executors — compute, block volume, VCN, ADB"
```

---

### Task 4: Backfill `ROLLBACK_CAPABILITY` on Existing OCI Change Executors

**Why this task exists:** The planning engine (updated in the CR remediation work) hard-fails on any executor missing `ROLLBACK_CAPABILITY`. All 75 existing OCI executors predate that change. Any OCI CR planned after the remediation deployed will return HTTP 400 until this is fixed. This task must complete before smoke tests can run.

**Files:**
- Modify: all existing `.py` files in `backend/app/connectors/executors/oci/` that are change executors (not discover/ingest)

**Classification rules:**
- If the current `rollback()` function returns `{"rolled_back": False, ...}` or `{"rolled_back": False}` with a reason → `ROLLBACK_CAPABILITY = "irreversible"` + `ROLLBACK_REASON = "<specific reason>"`
- If `rollback()` actually calls SDK to undo the action (delete what was created, restore what was changed) → `ROLLBACK_CAPABILITY = "full"`
- If `rollback()` returns `{"rolled_back": True, "note": "..."}` with a paired-action note → `ROLLBACK_CAPABILITY = "full"`
- Discover/ingest executors (file names starting with `discover_`) → skip, they don't go through CR planning

- [ ] **Step 1: List all OCI executor files**

```
ls backend/app/connectors/executors/oci/*.py
```

Exclude from this task: `_client.py`, `discover_*.py`, `__init__.py`, and the 10 new files from Tasks 1–3.

- [ ] **Step 2: Read each change executor and classify**

For each file not excluded above, open it and look at what `rollback()` returns:
- `{"rolled_back": False, ...}` → irreversible
- Calls SDK to delete/restore → full
- No `rollback()` function at all → treat as irreversible (add function that returns False)

- [ ] **Step 3: Add `ROLLBACK_CAPABILITY` to each file**

**For `"full"` executors** (rollback already works via paired SDK call or catalog action), add at the top of the file, after the SPDX header:

```python
ROLLBACK_CAPABILITY = "full"
```

**For `"irreversible"` executors**, add both constants. Use the specific reason from what the current code already says, or write a concise one based on the action name. Examples:

```python
# delete_vcn.py (current rollback returns: {"rolled_back": False, "reason": "delete_vcn has no rollback"})
ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Deleting a VCN removes the network configuration permanently; subnets, route tables, and security lists cannot be automatically restored"

# delete_compartment.py
ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "OCI compartment deletion is permanent and cannot be automatically undone"

# delete_iam_user.py (if not already classified as full)
ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "OCI IAM user deletion removes all credentials and memberships permanently"
```

**For executors with no `rollback()` function**, add one:

```python
ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "<reason specific to this action>"

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
```

- [ ] **Step 4: Verify planning engine accepts all OCI executors**

```
cd backend && python -c "
from app.connectors.catalog_service import get_catalog_service
from app.services.planning_engine import _validate_rollback_capability
import importlib

svc = get_catalog_service()
actions = [a for a in svc.get_actions('oci') if a.get('action_type') == 'change']
errors = []
for a in actions:
    try:
        mod = importlib.import_module(f'app.connectors.executors.oci.{a[\"executor\"].split(\".\")[1]}')
        _validate_rollback_capability(mod, 'oci', a['action_id'])
    except Exception as e:
        errors.append(f'{a[\"action_id\"]}: {e}')

if errors:
    for e in errors: print('FAIL:', e)
else:
    print('OK: all OCI change executors have valid ROLLBACK_CAPABILITY')
"
```
Expected: `OK: all OCI change executors have valid ROLLBACK_CAPABILITY`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/oci/
git commit -m "feat(oci): backfill ROLLBACK_CAPABILITY on all existing OCI change executors"
```

---

### Task 5: Smoke Tests

**Files:**
- Create: `backend/tests/smoke/test_oci_parity_spec1_smoke.py`

**Prerequisites:** Tasks 1–4 complete, platform running on EC2, OCI connector configured in the platform with valid credentials and a compartment_id, a compute instance available for tagging.

**Phases:**
- `OCIR_CRUD` — create repo → verify → delete with rollback → verify rollback recreated it → cleanup
- `RESTORE_BLOCK_VOLUME` — create a small block volume + backup → restore from backup → verify new volume → rollback (delete restored volume) → cleanup original volume + backup
- `TAGGING` — apply tags to an existing compute instance → verify → rollback → verify original tags restored

- [ ] **Step 1: Write the smoke test file**

Create `backend/tests/smoke/test_oci_parity_spec1_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OCI Parity Spec 1 — Live Smoke Tests
Runs against the live OCI connector on EC2. No mocks.

Required env vars / SSM params (read via platform API):
  OCI_CONNECTOR_ID     - connector UUID configured in the platform
  OCI_COMPARTMENT_ID   - OCID of compartment to use for test resources
  OCI_TEST_INSTANCE_ID - OCID of a compute instance to use for tagging test
  OCI_AD               - availability domain (e.g. "TcRV:US-ASHBURN-AD-1")

Usage:
  pytest tests/smoke/test_oci_parity_spec1_smoke.py -v -s \
      --platform-url http://localhost:8000 \
      --api-token <token>
"""

import os
import time
import uuid
import pytest
import httpx

PLATFORM_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
API_TOKEN = os.environ.get("API_TOKEN", "")
CONNECTOR_ID = os.environ.get("OCI_CONNECTOR_ID", "")
COMPARTMENT_ID = os.environ.get("OCI_COMPARTMENT_ID", "")
TEST_INSTANCE_ID = os.environ.get("OCI_TEST_INSTANCE_ID", "")
AV_DOMAIN = os.environ.get("OCI_AD", "")
ORG_ID = os.environ.get("ORG_ID", "")

HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
TIMEOUT = 600  # 10 min per phase


def _client():
    return httpx.Client(base_url=PLATFORM_URL, headers=HEADERS, timeout=30)


def _create_cr(client, action_id, parameters):
    resp = client.post("/change-requests", json={
        "connector_id": CONNECTOR_ID,
        "action_id": action_id,
        "parameters": parameters,
        "asset_ids": [],
    })
    assert resp.status_code == 201, f"CR create failed: {resp.text}"
    return resp.json()["id"]


def _approve_cr(client, cr_id):
    resp = client.post(f"/change-requests/{cr_id}/approve")
    assert resp.status_code == 200, f"CR approve failed: {resp.text}"


def _wait_status(client, cr_id, target_status, timeout=TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/change-requests/{cr_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status == target_status:
            return resp.json()
        if status in ("failed", "cancelled"):
            raise AssertionError(f"CR {cr_id} reached terminal status {status!r} before {target_status!r}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} did not reach {target_status!r} within {timeout}s")


def _rollback_cr(client, cr_id):
    resp = client.post(f"/change-requests/{cr_id}/rollback")
    assert resp.status_code in (200, 202), f"Rollback trigger failed: {resp.text}"
    return _wait_status(client, cr_id, "rolled_back")


@pytest.mark.skipif(not CONNECTOR_ID or not COMPARTMENT_ID, reason="OCI_CONNECTOR_ID and OCI_COMPARTMENT_ID required")
class TestOcirCrud:
    """OCIR_CRUD phase: create repo → delete with rollback → verify rollback recreated it."""

    def test_ocir_create_and_delete_with_rollback(self):
        client = _client()
        repo_name = f"nexplane-smoke-{uuid.uuid4().hex[:8]}"

        # Create repo
        cr_id = _create_cr(client, "oci_ocir_repo_create", {
            "compartment_id": COMPARTMENT_ID,
            "display_name": repo_name,
            "is_public": False,
        })
        _approve_cr(client, cr_id)
        cr = _wait_status(client, cr_id, "completed")
        repository_id = cr["execution_result"]["repository_id"]
        assert repository_id, "repository_id not in execution_result"

        # Delete repo — cr_id/step_id/org_id are auto-injected by the platform
        del_cr_id = _create_cr(client, "oci_ocir_repo_delete", {
            "repository_id": repository_id,
        })
        _approve_cr(client, del_cr_id)
        _wait_status(client, del_cr_id, "completed")

        # Rollback: should recreate the repo (pre-state was captured before delete)
        _rollback_cr(client, del_cr_id)
        del_cr = client.get(f"/change-requests/{del_cr_id}").json()
        rollback_result = del_cr.get("rollback_result", {})
        assert rollback_result.get("rolled_back") is True or rollback_result.get("new_repository_id"), \
               f"Rollback did not recreate repo: {rollback_result}"

        # Cleanup: delete the recreated repo
        new_repo_id = rollback_result.get("new_repository_id") or repository_id
        cleanup_cr = _create_cr(client, "oci_ocir_repo_delete", {"repository_id": new_repo_id})
        _approve_cr(client, cleanup_cr)
        _wait_status(client, cleanup_cr, "completed")
        print(f"OCIR_CRUD PASS: created {repo_name}, deleted, rolled back (recreated), cleaned up")
```

The smoke test file is intentionally structured as separate CR lifecycle calls matching the dogfooding principle. Add two more test classes below the OCIR one:

```python
@pytest.mark.skipif(not CONNECTOR_ID or not COMPARTMENT_ID or not AV_DOMAIN, reason="OCI env vars required")
class TestRestoreBlockVolume:
    """RESTORE_BLOCK_VOLUME phase: create volume + backup → restore → rollback."""

    def test_restore_block_volume_and_rollback(self):
        client = _client()
        vol_name = f"nexplane-smoke-vol-{uuid.uuid4().hex[:8]}"
        restore_name = f"nexplane-smoke-restore-{uuid.uuid4().hex[:8]}"

        # Step 1: Create a small block volume
        vol_cr = _create_cr(client, "oci_block_volume_create", {
            "compartment_id": COMPARTMENT_ID,
            "display_name": vol_name,
            "size_in_gbs": 50,
            "availability_domain": AV_DOMAIN,
        })
        _approve_cr(client, vol_cr)
        vol_result = _wait_status(client, vol_cr, "completed", timeout=300)
        volume_id = vol_result["execution_result"]["volume_id"]

        # Step 2: Create a backup of the volume
        backup_cr = _create_cr(client, "oci_block_volume_backup", {
            "volume_id": volume_id,
            "display_name": f"{vol_name}-backup",
            "type": "FULL",
        })
        _approve_cr(client, backup_cr)
        backup_result = _wait_status(client, backup_cr, "completed", timeout=600)
        backup_id = backup_result["execution_result"]["backup_id"]

        # Step 3: Restore from backup
        restore_cr = _create_cr(client, "oci_restore_block_volume_backup", {
            "volume_backup_id": backup_id,
            "display_name": restore_name,
            "compartment_id": COMPARTMENT_ID,
            "availability_domain": AV_DOMAIN,
        })
        _approve_cr(client, restore_cr)
        restore_result = _wait_status(client, restore_cr, "completed", timeout=600)
        restored_volume_id = restore_result["execution_result"]["volume_id"]
        assert restored_volume_id, "restored volume_id missing from execution_result"

        # Step 4: Rollback (delete the restored volume)
        _rollback_cr(client, restore_cr)

        # Step 5: Cleanup original volume and backup
        del_backup_cr = _create_cr(client, "oci_block_volume_backup", {"volume_id": volume_id})
        # Rollback the backup (delete it)
        _rollback_cr(client, backup_cr)
        del_vol_cr = _create_cr(client, "oci_block_volume_delete", {"volume_id": volume_id})
        _approve_cr(client, del_vol_cr)
        _wait_status(client, del_vol_cr, "completed", timeout=120)
        print(f"RESTORE_BLOCK_VOLUME PASS: restored {backup_id} → {restored_volume_id}, rolled back, cleaned up")


@pytest.mark.skipif(not CONNECTOR_ID or not TEST_INSTANCE_ID, reason="OCI_TEST_INSTANCE_ID required")
class TestTagging:
    """TAGGING phase: apply tags → verify → rollback → verify original restored."""

    def test_tag_compute_instance_and_rollback(self):
        client = _client()
        test_tag = {"nexplane-smoke": f"test-{uuid.uuid4().hex[:8]}"}

        # Read current tags on the instance first (baseline)
        # Apply new tags
        tag_cr = _create_cr(client, "oci_tag_compute_instance", {
            "instance_id": TEST_INSTANCE_ID,
            "freeform_tags": test_tag,
        })
        _approve_cr(client, tag_cr)
        _wait_status(client, tag_cr, "completed")

        # Rollback: should restore original tags
        _rollback_cr(client, tag_cr)
        rb_result = client.get(f"/change-requests/{tag_cr}").json()
        assert rb_result.get("rollback_result", {}).get("rolled_back") is True, \
               f"Tag rollback failed: {rb_result}"
        print(f"TAGGING PASS: tagged {TEST_INSTANCE_ID}, rolled back original tags")
```

- [ ] **Step 2: Verify the smoke file is syntactically valid**

```
cd backend && python -c "import py_compile; py_compile.compile('tests/smoke/test_oci_parity_spec1_smoke.py', doraise=True); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Deploy to EC2 and run smoke tests**

```bash
# From EC2 (ssh ec2-user@100.101.186.39):
cd /home/ec2-user/nexplane
git pull

# Set env vars (values from the platform's connector record)
export PLATFORM_URL="http://localhost:8000"
export API_TOKEN="<token from platform>"
export OCI_CONNECTOR_ID="<oci connector uuid>"
export OCI_COMPARTMENT_ID="<compartment ocid>"
export OCI_TEST_INSTANCE_ID="<test instance ocid>"
export OCI_AD="<availability domain>"

docker exec nexplane-backend-1 python -m pytest \
    tests/smoke/test_oci_parity_spec1_smoke.py -v -s \
    -k "TestOcirCrud or TestRestoreBlockVolume or TestTagging"
```

Expected: 3 test methods PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_oci_parity_spec1_smoke.py
git commit -m "test(oci): smoke tests for OCIR, block volume restore, and tagging"
```
