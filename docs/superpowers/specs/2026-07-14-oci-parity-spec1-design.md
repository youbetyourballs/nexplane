# OCI Parity Spec 1 — OCIR, Backup Restore, Resource Tagging

**Goal:** Close the three straightforward OCI parity gaps — Container Registry (OCIR), backup restore workflows, and resource tagging — to match AWS/Azure executor coverage.

**Architecture:** All executors follow the established OCI pattern: `loop.run_in_executor(None, <blocking_sdk_call>)`, module-level `ROLLBACK_CAPABILITY` constant, catalog entry in `oci.json`. One new SDK client (`oci.artifacts.ArtifactsClient`) is added to `_client.py` for OCIR. Tagging executors reuse `PreStateStore` for pre-state capture. Restore executors that create new resources use the new resource's OCID (from `execution_result`) as the rollback target — no PreStateStore needed.

**Tech Stack:** Python/FastAPI/SQLAlchemy (existing OCI connector pattern), OCI Python SDK, pytest.

**Out of scope (Spec 2):** Data Guard / HA automation, OKE cluster lifecycle.

## Global Constraints

- `ROLLBACK_CAPABILITY` must be a module-level string constant in every executor — `"full"` or `"irreversible"`. Missing = hard-fail.
- `ROLLBACK_REASON` required alongside `ROLLBACK_CAPABILITY = "irreversible"`.
- `PreStateStore.capture()` + `db.commit()` must complete BEFORE any destructive SDK call in `"full"` executors.
- NEVER use `from __future__ import annotations` in any Python file.
- All new functionality must have smoke coverage before the feature is considered done.
- Smoke runs on EC2 against the live OCI connector — no mocks, no local Docker.

---

## Component Map

| File | Status | Responsibility |
|---|---|---|
| `backend/app/connectors/executors/oci/_client.py` | Modify | Add `oci.artifacts.ArtifactsClient` |
| `backend/app/connectors/executors/oci/create_ocir_repository.py` | Create | Create OCIR repo; rollback = delete |
| `backend/app/connectors/executors/oci/delete_ocir_repository.py` | Create | Capture repo metadata, delete; rollback = recreate |
| `backend/app/connectors/executors/oci/delete_ocir_image.py` | Create | Delete image by digest; irreversible |
| `backend/app/connectors/executors/oci/restore_adb.py` | Create | Restore ADB to point-in-time; irreversible |
| `backend/app/connectors/executors/oci/restore_block_volume_backup.py` | Create | Create volume from backup; rollback = delete new volume |
| `backend/app/connectors/executors/oci/restore_boot_volume_backup.py` | Create | Create boot volume from backup; rollback = delete new volume |
| `backend/app/connectors/executors/oci/tag_compute_instance.py` | Create | Capture tags, apply new tags; rollback = restore prior tags |
| `backend/app/connectors/executors/oci/tag_block_volume.py` | Create | Same pattern as tag_compute_instance |
| `backend/app/connectors/executors/oci/tag_vcn.py` | Create | Same pattern as tag_compute_instance |
| `backend/app/connectors/executors/oci/tag_adb.py` | Create | Same pattern as tag_compute_instance |
| `backend/app/connectors/catalog/oci.json` | Modify | Add 10 new catalog entries |
| `backend/tests/unit/test_oci_parity_spec1.py` | Create | Unit tests for all 10 executors (mocked SDK) |
| `backend/tests/smoke/test_oci_parity_spec1_smoke.py` | Create | Live smoke: OCIR CRUD, block volume restore + rollback, tag + rollback |

---

## Executor Contracts

### OCIR

**`create_ocir_repository`**

```python
ROLLBACK_CAPABILITY = "full"

# params: compartment_id, display_name, is_public (bool, default False)
# execute(): artifacts_client.create_repository(CreateRepositoryDetails(...))
#   returns execution_result = {"repository_id": repo.id, "display_name": display_name}
# rollback(): artifacts_client.delete_repository(repository_id from execution_result)
```

**`delete_ocir_repository`**

```python
ROLLBACK_CAPABILITY = "full"

# params: repository_id
# execute():
#   1. repo = artifacts_client.get_repository(repository_id).data
#   2. PreStateStore.capture(db, cr_id, step_id, org_id, {
#        "display_name": repo.display_name,
#        "compartment_id": repo.compartment_id,
#        "is_public": repo.is_public,
#        "defined_tags": repo.defined_tags,
#        "freeform_tags": repo.freeform_tags,
#      })
#   3. await db.commit()
#   4. artifacts_client.delete_repository(repository_id)
# rollback():
#   state = await PreStateStore.retrieve(db, cr_id, step_id, org_id)
#   artifacts_client.create_repository(CreateRepositoryDetails(
#     compartment_id=state["compartment_id"],
#     display_name=state["display_name"],
#     is_public=state["is_public"],
#     ...
#   ))
```

**`delete_ocir_image`**

```python
ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Container image layer data is permanently deleted and cannot be recovered"

# params: image_id
# execute(): artifacts_client.delete_container_image(image_id)
# rollback(): returns {"rolled_back": False, "reason": ROLLBACK_REASON}
```

---

### Backup Restore

**`restore_adb`**

```python
ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Restoring an Autonomous Database overwrites current data; the prior state cannot be recovered after restore completes"

# params: autonomous_database_id, timestamp (ISO8601) or backup_id
# execute(): database_client.restore_autonomous_database(autonomous_database_id, RestoreAutonomousDatabaseDetails(...))
#   poll until AVAILABLE (interval 30s, timeout 30min)
# rollback(): returns {"rolled_back": False, "reason": ROLLBACK_REASON}
```

**`restore_block_volume_backup`**

```python
ROLLBACK_CAPABILITY = "full"

# params: volume_backup_id, display_name, compartment_id, availability_domain
# execute():
#   volume = blockstorage_client.create_volume(CreateVolumeDetails(
#     source_details=VolumeSourceFromVolumeBackupDetails(id=volume_backup_id),
#     ...
#   )).data
#   poll until AVAILABLE
#   returns execution_result = {"volume_id": volume.id}
# rollback():
#   blockstorage_client.delete_volume(execution_result["volume_id"])
#   poll until TERMINATED
```

**`restore_boot_volume_backup`**

```python
ROLLBACK_CAPABILITY = "full"

# params: boot_volume_backup_id, display_name, compartment_id, availability_domain
# execute():
#   boot_vol = blockstorage_client.create_boot_volume(CreateBootVolumeDetails(
#     source_details=BootVolumeSourceFromBootVolumeBackupDetails(id=boot_volume_backup_id),
#     ...
#   )).data
#   poll until AVAILABLE
#   returns execution_result = {"boot_volume_id": boot_vol.id}
# rollback():
#   blockstorage_client.delete_boot_volume(execution_result["boot_volume_id"])
```

---

### Resource Tagging

All four tagging executors follow the same pattern. Shown once for `tag_compute_instance`; the others substitute the appropriate SDK client and update call.

**`tag_compute_instance`**

```python
ROLLBACK_CAPABILITY = "full"

# params: instance_id, freeform_tags (dict, optional), defined_tags (dict, optional)
# execute():
#   instance = compute_client.get_instance(instance_id).data
#   PreStateStore.capture(db, cr_id, step_id, org_id, {
#     "freeform_tags": instance.freeform_tags,
#     "defined_tags": instance.defined_tags,
#   })
#   await db.commit()
#   compute_client.update_instance(instance_id, UpdateInstanceDetails(
#     freeform_tags=freeform_tags,
#     defined_tags=defined_tags,
#   ))
# rollback():
#   state = await PreStateStore.retrieve(db, cr_id, step_id, org_id)
#   compute_client.update_instance(instance_id, UpdateInstanceDetails(
#     freeform_tags=state["freeform_tags"],
#     defined_tags=state["defined_tags"],
#   ))
```

**`tag_block_volume`** — same pattern; `blockstorage_client.get_volume()` / `update_volume()`

**`tag_vcn`** — same pattern; `network_client.get_vcn()` / `update_vcn()`

**`tag_adb`** — same pattern; `database_client.get_autonomous_database()` / `update_autonomous_database()`

---

## `_client.py` Addition

Add to the existing client initialization block:

```python
self.artifacts = oci.artifacts.ArtifactsClient(config, **kwargs)
```

Accessible as `self._oci.artifacts` in all OCIR executors, consistent with the existing pattern (`self._oci.compute`, `self._oci.network`, etc.).

---

## Catalog Entries (`oci.json`)

Ten new entries following the existing pattern:

```json
{
  "change_type": "create_ocir_repository",
  "display_name": "Create OCIR Repository",
  "description": "Creates an OCI Container Registry repository in the specified compartment",
  "params": ["compartment_id", "display_name", "is_public"],
  "rollback_action": "delete_ocir_repository"
},
{
  "change_type": "delete_ocir_repository",
  "display_name": "Delete OCIR Repository",
  "description": "Deletes an OCI Container Registry repository and all its images",
  "params": ["repository_id"],
  "rollback_action": "create_ocir_repository"
},
{
  "change_type": "delete_ocir_image",
  "display_name": "Delete Container Image",
  "description": "Permanently deletes a container image by ID",
  "params": ["image_id"],
  "rollback_action": null
},
{
  "change_type": "restore_adb",
  "display_name": "Restore Autonomous Database",
  "description": "Restores an Autonomous Database to a point-in-time or from a backup",
  "params": ["autonomous_database_id", "timestamp"],
  "rollback_action": null
},
{
  "change_type": "restore_block_volume_backup",
  "display_name": "Restore Block Volume from Backup",
  "description": "Creates a new block volume from an existing backup",
  "params": ["volume_backup_id", "display_name", "compartment_id", "availability_domain"],
  "rollback_action": "delete_block_volume"
},
{
  "change_type": "restore_boot_volume_backup",
  "display_name": "Restore Boot Volume from Backup",
  "description": "Creates a new boot volume from an existing backup",
  "params": ["boot_volume_backup_id", "display_name", "compartment_id", "availability_domain"],
  "rollback_action": "delete_boot_volume"
},
{
  "change_type": "tag_compute_instance",
  "display_name": "Tag Compute Instance",
  "description": "Applies freeform or defined tags to an OCI compute instance",
  "params": ["instance_id", "freeform_tags", "defined_tags"],
  "rollback_action": "tag_compute_instance"
},
{
  "change_type": "tag_block_volume",
  "display_name": "Tag Block Volume",
  "description": "Applies freeform or defined tags to an OCI block volume",
  "params": ["volume_id", "freeform_tags", "defined_tags"],
  "rollback_action": "tag_block_volume"
},
{
  "change_type": "tag_vcn",
  "display_name": "Tag VCN",
  "description": "Applies freeform or defined tags to an OCI Virtual Cloud Network",
  "params": ["vcn_id", "freeform_tags", "defined_tags"],
  "rollback_action": "tag_vcn"
},
{
  "change_type": "tag_adb",
  "display_name": "Tag Autonomous Database",
  "description": "Applies freeform or defined tags to an OCI Autonomous Database",
  "params": ["autonomous_database_id", "freeform_tags", "defined_tags"],
  "rollback_action": "tag_adb"
}
```

---

## Smoke Test Phases

**OCIR_CRUD** — create a repo, verify it exists, delete it, verify rollback recreates it, cleanup.

**RESTORE_BLOCK_VOLUME** — create a block volume + backup in the live OCI tenancy, execute `restore_block_volume_backup`, verify new volume exists, rollback (delete new volume), verify gone.

**TAGGING** — apply freeform tags to a test compute instance, verify tags present, rollback, verify original tags restored.

All phases run on EC2 against the live OCI connector. No mocks.
