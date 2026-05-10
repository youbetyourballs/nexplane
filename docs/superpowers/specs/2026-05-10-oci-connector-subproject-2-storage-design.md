# OCI Connector — Sub-project 2: Storage Design

## Scope

Object Storage buckets (CRUD, lifecycle, public access block) and Block Volumes (create, attach, detach, delete, backup). Follows the executor/catalog/frontend patterns established in sub-project 1.

**Key design decisions:**
- Object Storage buckets → `storage_bucket` asset type (exact fit)
- Block Volumes → `storage_bucket` asset type with tag `oci-block-volume` (no dedicated AssetType exists; consistent with how EBS snapshots are handled)
- Block Volume attach/detach target the `server` asset (instance), not the volume asset

---

## New Change Types (9)

```
oci_bucket_create           Object Storage bucket create
oci_bucket_delete           Object Storage bucket delete
oci_bucket_lifecycle_set    Configure lifecycle rules (transition/expire objects)
oci_bucket_block_public     Block public access on bucket
oci_block_volume_create     Create a Block Volume
oci_block_volume_attach     Attach a Block Volume to an instance
oci_block_volume_detach     Detach a Block Volume from an instance
oci_block_volume_delete     Delete a Block Volume
oci_block_volume_backup     Create a Block Volume backup
```

All 9 added to `_IMPLICIT_ROLLBACK_TYPES`.

---

## New Files

```
backend/app/connectors/executors/oci/
  discover_buckets.py
  discover_block_volumes.py
  create_bucket.py
  delete_bucket.py
  configure_bucket_lifecycle.py
  block_bucket_public_access.py
  create_block_volume.py
  attach_block_volume.py
  detach_block_volume.py
  delete_block_volume.py
  create_block_volume_backup.py

backend/alembic/versions/041_add_oci_storage_change_types.py
```

New OCI SDK clients in `_client.py`:
```python
def get_objectstorage_client(creds) -> oci.object_storage.ObjectStorageClient
def get_blockstorage_client(creds) -> oci.core.BlockstorageClient  # already present
```

---

## Discovery

### `discover_buckets.py`
- Calls `objectstorage.get_namespace()` then `objectstorage.list_buckets(namespace, compartment_id)`
- Each bucket → `storage_bucket` asset:
  ```json
  {
    "name": "<bucket_name>",
    "asset_type": "storage_bucket",
    "asset_metadata": {
      "bucket_name": "<name>",
      "namespace": "<namespace>",
      "compartment_id": "ocid1.compartment...",
      "region": "us-ashburn-1",
      "provider": "oci",
      "public_access_type": "NoPublicAccess",
      "storage_tier": "Standard",
      "versioning": "Disabled"
    },
    "tags": ["oci", "object-storage"]
  }
  ```
- Dedup key: `bucket_name`

### `discover_block_volumes.py`
- Calls `blockstorage.list_volumes(compartment_id)`
- Each volume → `storage_bucket` asset tagged `oci-block-volume`:
  ```json
  {
    "name": "<display_name>",
    "asset_type": "storage_bucket",
    "asset_metadata": {
      "volume_id": "ocid1.volume...",
      "compartment_id": "ocid1.compartment...",
      "size_in_gbs": 50,
      "lifecycle_state": "AVAILABLE",
      "vpus_per_gb": 10,
      "region": "us-ashburn-1"
    },
    "tags": ["oci", "block-volume"]
  }
  ```
- Dedup key: `volume_id`

---

## Object Storage Executors

### `create_bucket.py` (`oci_bucket_create`)
**Parameters (all pre-populated):**
```
compartment_id       resolved from target compartment asset
name                 "nexplane-bucket-<timestamp>"
storage_tier         "Standard"
public_access_type   "NoPublicAccess"
versioning           "Disabled"
```
Returns `_auto_asset` (`storage_bucket`). Rollback: `oci_bucket_delete`.

### `delete_bucket.py` (`oci_bucket_delete`)
- Calls `objectstorage.delete_bucket(namespace, bucket_name)`
- Requires bucket to be empty (preflight check: list objects, fail if non-empty with clear message)
- Parameters: `bucket_name` (auto-populated from target asset metadata), `namespace` (auto-populated)
- Rollback: none (destructive)

### `configure_bucket_lifecycle.py` (`oci_bucket_lifecycle_set`)
**Parameters (all pre-populated):**
```
bucket_name          auto-populated from target asset
namespace            auto-populated from target asset
rules                [{"name": "expire-objects", "action": "DELETE", "time_amount": 90, "time_unit": "DAYS", "is_enabled": true}]
```
Rollback: clear lifecycle rules (set to empty list).

### `block_bucket_public_access.py` (`oci_bucket_block_public`)
- Sets `public_access_type = "NoPublicAccess"` on bucket
- Parameters: `bucket_name`, `namespace` (both auto-populated)
- Rollback: restore previous `public_access_type` (stored in execution result)

---

## Block Volume Executors

### `create_block_volume.py` (`oci_block_volume_create`)
**Parameters (all pre-populated):**
```
compartment_id       resolved from target compartment asset
display_name         "nexplane-volume"
size_in_gbs          50
vpus_per_gb          10
availability_domain  resolved from compartment's first AD via identity.list_availability_domains()
```
Returns `_auto_asset` (`storage_bucket`, tagged `oci-block-volume`). Rollback: `oci_block_volume_delete`.

### `attach_block_volume.py` (`oci_block_volume_attach`)
**Parameters (all pre-populated):**
```
instance_id          resolved from target server asset metadata
volume_id            resolved from target block-volume asset metadata
display_name         "nexplane-attachment"
type                 "paravirtualized"
is_read_only         false
```
Polls until attachment lifecycle = ATTACHED. Rollback: `oci_block_volume_detach`.

### `detach_block_volume.py` (`oci_block_volume_detach`)
- Resolves `volume_attachment_id` from `compute.list_volume_attachments(instance_id)`
- Calls `compute.detach_volume(volume_attachment_id)`, polls until DETACHED
- Parameters: `instance_id`, `volume_id` (both auto-populated from target assets)
- Rollback: `oci_block_volume_attach` (re-attach)

### `delete_block_volume.py` (`oci_block_volume_delete`)
- Preflight: verify volume not attached (fail with message if attached)
- Calls `blockstorage.delete_volume(volume_id)`
- Parameters: `volume_id` (auto-populated from target asset)
- Rollback: none (destructive)

### `create_block_volume_backup.py` (`oci_block_volume_backup`)
**Parameters (all pre-populated):**
```
volume_id            resolved from target block-volume asset metadata
display_name         "nexplane-backup-<timestamp>"
type                 "INCREMENTAL"
```
Rollback: delete backup.

---

## Catalog (`oci.json` additions)

New ingest actions:
- `discover_buckets` → `oci.discover_buckets`
- `discover_block_volumes` → `oci.discover_block_volumes`

New change actions: 9 entries following the same schema as sub-project 1.

---

## Frontend Wiring

### `api.ts`
9 new `ChangeType` values added.

### `CreateChangeRequest.tsx`
"Oracle Cloud" category extended with 9 new entries. All outcome templates pre-populated with sensible defaults. Block volume attach/detach actions require two target assets (instance + volume) — uses the same multi-target pattern as `register_targets` in AWS.

### `AssetDetail.tsx`
Object Storage buckets and Block Volumes already rendered by the `storage_bucket` metadata panel (bucket_name, versioning, public_access_type; or volume_id, size_in_gbs, lifecycle_state).

Quick actions for `storage_bucket` assets tagged `oci-block-volume`: attach, detach, backup, delete.
Quick actions for `storage_bucket` assets tagged `oci` (Object Storage): configure lifecycle, block public access, delete.

---

## Smoke Test Phases

### OCI_F — Object Storage
1. Fire `oci_bucket_create` → verify `storage_bucket` asset in inventory
2. Fire `oci_bucket_lifecycle_set` → OCI SDK verify lifecycle rules set
3. Fire `oci_bucket_block_public` → OCI SDK verify `public_access_type = NoPublicAccess`
4. Fire `oci_bucket_delete` rollback → verify asset removed

### OCI_G — Block Volumes
1. Fire `oci_block_volume_create` → OCI SDK verify AVAILABLE, `storage_bucket` asset in inventory
2. Fire `oci_block_volume_attach` (attach to Phase OCI_B instance) → OCI SDK verify ATTACHED
3. Fire `oci_block_volume_detach` → OCI SDK verify DETACHED
4. Fire `oci_block_volume_backup` → OCI SDK verify backup AVAILABLE
5. Fire `oci_block_volume_delete` → OCI SDK verify TERMINATED

---

## DB Migration (041)

```sql
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_bucket_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_bucket_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_bucket_lifecycle_set';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_bucket_block_public';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_attach';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_detach';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_backup';
```
