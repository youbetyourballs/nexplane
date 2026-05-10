# OCI Connector Sub-project 2: Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add Object Storage and Block Volume executors to the OCI connector
**Architecture:** All executors live under `backend/app/connectors/executors/oci/` and import OCI SDK clients from the existing `_client.py` created in sub-project 1. Object Storage buckets map to the `storage_bucket` asset type; Block Volumes also use `storage_bucket` with the `oci-block-volume` tag, mirroring how EBS snapshots are handled. The frontend wires 9 new ChangeType values into the existing "Oracle Cloud" category in `CreateChangeRequest.tsx` and `AssetDetail.tsx` quick actions.
**Tech Stack:** OCI Python SDK (oci>=2.130.0), Python asyncio

---

### Task 1: Discovery executors — discover_buckets.py + discover_block_volumes.py

**Files:** Create: `backend/app/connectors/executors/oci/discover_buckets.py`, `backend/app/connectors/executors/oci/discover_block_volumes.py`

- [ ] Step 1: Create `discover_buckets.py`. Import `get_objectstorage_client` from `._client` (to be added in Task 7 to `_client.py`). Mock path returns 2 representative `storage_bucket` assets when `not creds`. Real path calls `objectstorage.get_namespace()` then `objectstorage.list_buckets(namespace_name=namespace, compartment_id=compartment_id)` via `loop.run_in_executor(None, ...)`. Each bucket entry maps to the asset shape below. Dedup key: `bucket_name`.

```python
import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_buckets",
        "assets": [
            {
                "id": "oci-bucket-mock-1",
                "name": "my-private-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-private-bucket",
                    "namespace": "mock-namespace",
                    "compartment_id": "ocid1.compartment.oc1..mock",
                    "region": "us-ashburn-1",
                    "provider": "oci",
                    "public_access_type": "NoPublicAccess",
                    "storage_tier": "Standard",
                    "versioning": "Disabled",
                },
                "tags": ["oci", "object-storage"],
            },
            {
                "id": "oci-bucket-mock-2",
                "name": "my-public-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-public-bucket",
                    "namespace": "mock-namespace",
                    "compartment_id": "ocid1.compartment.oc1..mock",
                    "region": "us-ashburn-1",
                    "provider": "oci",
                    "public_access_type": "ObjectRead",
                    "storage_tier": "Standard",
                    "versioning": "Disabled",
                },
                "tags": ["oci", "object-storage"],
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_objectstorage_client
    client = get_objectstorage_client(creds)
    compartment_id = creds.get("compartment_id", "")
    region = creds.get("region", "us-ashburn-1")
    loop = asyncio.get_running_loop()

    def _call():
        namespace = client.get_namespace().data
        buckets = client.list_buckets(namespace_name=namespace, compartment_id=compartment_id).data
        assets = []
        for b in buckets:
            assets.append({
                "id": f"oci-bucket-{b.name}",
                "name": b.name,
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": b.name,
                    "namespace": namespace,
                    "compartment_id": b.compartment_id,
                    "region": region,
                    "provider": "oci",
                    "public_access_type": getattr(b, "public_access_type", "NoPublicAccess") or "NoPublicAccess",
                    "storage_tier": getattr(b, "storage_tier", "Standard") or "Standard",
                    "versioning": getattr(b, "versioning", "Disabled") or "Disabled",
                },
                "tags": ["oci", "object-storage"],
            })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_buckets", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
```

- [ ] Step 2: Create `discover_block_volumes.py`. Import `get_blockstorage_client` from `._client`. Mock returns 2 `storage_bucket` assets tagged `oci-block-volume`. Real path calls `blockstorage.list_volumes(compartment_id=compartment_id)` via `run_in_executor`. Dedup key: `volume_id`.

```python
import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_block_volumes",
        "assets": [
            {
                "id": "ocid1.volume.oc1..mock1",
                "name": "mock-volume-1",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "volume_id": "ocid1.volume.oc1..mock1",
                    "compartment_id": "ocid1.compartment.oc1..mock",
                    "size_in_gbs": 50,
                    "lifecycle_state": "AVAILABLE",
                    "vpus_per_gb": 10,
                    "region": "us-ashburn-1",
                },
                "tags": ["oci", "block-volume"],
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_blockstorage_client
    client = get_blockstorage_client(creds)
    compartment_id = creds.get("compartment_id", "")
    region = creds.get("region", "us-ashburn-1")
    loop = asyncio.get_running_loop()

    def _call():
        volumes = client.list_volumes(compartment_id=compartment_id).data
        assets = []
        for v in volumes:
            assets.append({
                "id": v.id,
                "name": v.display_name,
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "volume_id": v.id,
                    "compartment_id": v.compartment_id,
                    "size_in_gbs": v.size_in_gbs,
                    "lifecycle_state": v.lifecycle_state,
                    "vpus_per_gb": getattr(v, "vpus_per_gb", 10),
                    "region": region,
                },
                "tags": ["oci", "block-volume"],
            })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_block_volumes", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
```

- [ ] Step 3: Add `get_objectstorage_client` to `backend/app/connectors/executors/oci/_client.py` (modify existing file from sub-project 1):

```python
def get_objectstorage_client(creds: dict):
    import oci
    config = _build_config(creds)
    return oci.object_storage.ObjectStorageClient(config)
```

Verify `get_blockstorage_client` already exists in `_client.py`; add it if missing:

```python
def get_blockstorage_client(creds: dict):
    import oci
    config = _build_config(creds)
    return oci.core.BlockstorageClient(config)
```

---

### Task 2: Object Storage create/delete executors — create_bucket.py + delete_bucket.py

**Files:** Create: `backend/app/connectors/executors/oci/create_bucket.py`, `backend/app/connectors/executors/oci/delete_bucket.py`

- [ ] Step 1: Create `create_bucket.py` implementing `oci_bucket_create`. Parameters: `compartment_id` (from target asset), `name` (default `"nexplane-bucket-<timestamp>"`), `storage_tier` (default `"Standard"`), `public_access_type` (default `"NoPublicAccess"`), `versioning` (default `"Disabled"`). Returns `_auto_asset` of type `storage_bucket`. Rollback: calls `delete_bucket.execute`.

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ts = int(datetime.now(timezone.utc).timestamp())
    compartment_id = parameters.get("compartment_id", creds.get("compartment_id", "") if creds else "ocid1.compartment.oc1..mock")
    name = parameters.get("name", f"nexplane-bucket-{ts}")
    storage_tier = parameters.get("storage_tier", "Standard")
    public_access_type = parameters.get("public_access_type", "NoPublicAccess")
    versioning = parameters.get("versioning", "Disabled")
    region = (creds.get("region", "us-ashburn-1") if creds else "us-ashburn-1")

    auto_asset = {
        "name": name,
        "asset_type": "storage_bucket",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "bucket_name": name,
            "namespace": "",
            "compartment_id": compartment_id,
            "region": region,
            "provider": "oci",
            "public_access_type": public_access_type,
            "storage_tier": storage_tier,
            "versioning": versioning,
        },
        "tags": ["oci", "object-storage", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_bucket",
            "bucket_name": name,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_objectstorage_client
    import oci
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        namespace = client.get_namespace().data
        details = oci.object_storage.models.CreateBucketDetails(
            name=name,
            compartment_id=compartment_id,
            storage_tier=storage_tier,
            public_access_type=public_access_type,
            versioning=versioning,
        )
        client.create_bucket(namespace_name=namespace, create_bucket_details=details)
        return namespace

    namespace = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["namespace"] = namespace

    return {
        "action": "create_bucket",
        "bucket_name": name,
        "namespace": namespace,
        "region": region,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_bucket import execute as delete
    return await delete(
        {
            "bucket_name": execution_result.get("bucket_name", parameters.get("name")),
            "namespace": execution_result.get("namespace", ""),
        },
        [],
        connector,
    )
```

- [ ] Step 2: Create `delete_bucket.py` implementing `oci_bucket_delete`. Parameters: `bucket_name` (from target asset metadata), `namespace` (from target asset metadata). Preflight: list objects; fail with clear message if bucket is non-empty. No rollback (destructive).

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters.get("bucket_name", "")
    namespace = parameters.get("namespace", "")

    if not creds:
        return {"action": "delete_bucket", "bucket_name": bucket_name, "mock": True}

    from ._client import get_objectstorage_client
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        ns = namespace or client.get_namespace().data
        # Preflight: ensure bucket is empty
        objects = client.list_objects(namespace_name=ns, bucket_name=bucket_name).data.objects
        if objects:
            raise ValueError(
                f"Bucket '{bucket_name}' is not empty ({len(objects)} object(s)). "
                "Empty the bucket before deleting."
            )
        client.delete_bucket(namespace_name=ns, bucket_name=bucket_name)
        return ns

    ns = await loop.run_in_executor(None, _call)
    return {
        "action": "delete_bucket",
        "bucket_name": bucket_name,
        "namespace": ns,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_bucket is destructive — no rollback"}
```

---

### Task 3: Object Storage policy executors — configure_bucket_lifecycle.py + block_bucket_public_access.py

**Files:** Create: `backend/app/connectors/executors/oci/configure_bucket_lifecycle.py`, `backend/app/connectors/executors/oci/block_bucket_public_access.py`

- [ ] Step 1: Create `configure_bucket_lifecycle.py` implementing `oci_bucket_lifecycle_set`. Parameters: `bucket_name`, `namespace` (both from target asset), `rules` (list, default one DELETE rule for 90 days). Calls `objectstorage.put_object_lifecycle_policy`. Rollback: clears lifecycle policy (empty rules).

```python
import asyncio
from datetime import datetime, timezone


_DEFAULT_RULES = [
    {
        "name": "expire-objects",
        "action": "DELETE",
        "time_amount": 90,
        "time_unit": "DAYS",
        "is_enabled": True,
    }
]


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters.get("bucket_name", "")
    namespace = parameters.get("namespace", "")
    rules = parameters.get("rules", _DEFAULT_RULES)

    if not creds:
        return {"action": "configure_bucket_lifecycle", "bucket_name": bucket_name, "rules": rules, "mock": True}

    from ._client import get_objectstorage_client
    import oci
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        ns = namespace or client.get_namespace().data
        lifecycle_rules = [
            oci.object_storage.models.ObjectLifecycleRule(
                name=r["name"],
                action=r["action"],
                time_amount=r["time_amount"],
                time_unit=r["time_unit"],
                is_enabled=r.get("is_enabled", True),
            )
            for r in rules
        ]
        policy = oci.object_storage.models.PutObjectLifecyclePolicyDetails(items=lifecycle_rules)
        client.put_object_lifecycle_policy(namespace_name=ns, bucket_name=bucket_name, put_object_lifecycle_policy_details=policy)
        return ns

    ns = await loop.run_in_executor(None, _call)
    return {
        "action": "configure_bucket_lifecycle",
        "bucket_name": bucket_name,
        "namespace": ns,
        "rules_applied": len(rules),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_objectstorage_client
    import oci
    creds = getattr(connector, "credentials", {})
    bucket_name = execution_result.get("bucket_name", parameters.get("bucket_name", ""))
    namespace = execution_result.get("namespace", parameters.get("namespace", ""))
    if not creds:
        return {"rolled_back": True, "mock": True}
    loop = asyncio.get_running_loop()
    client = get_objectstorage_client(creds)

    def _clear():
        ns = namespace or client.get_namespace().data
        policy = oci.object_storage.models.PutObjectLifecyclePolicyDetails(items=[])
        client.put_object_lifecycle_policy(namespace_name=ns, bucket_name=bucket_name, put_object_lifecycle_policy_details=policy)

    await loop.run_in_executor(None, _clear)
    return {"rolled_back": True, "bucket_name": bucket_name, "rules_cleared": True}
```

- [ ] Step 2: Create `block_bucket_public_access.py` implementing `oci_bucket_block_public`. Parameters: `bucket_name`, `namespace` (from target asset). Reads current `public_access_type`, stores in result, then sets `"NoPublicAccess"`. Rollback: restores previous value.

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters.get("bucket_name", "")
    namespace = parameters.get("namespace", "")

    if not creds:
        return {
            "action": "block_bucket_public_access",
            "bucket_name": bucket_name,
            "previous_public_access_type": "ObjectRead",
            "mock": True,
        }

    from ._client import get_objectstorage_client
    import oci
    client = get_objectstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        ns = namespace or client.get_namespace().data
        bucket = client.get_bucket(namespace_name=ns, bucket_name=bucket_name).data
        previous = bucket.public_access_type or "NoPublicAccess"
        update_details = oci.object_storage.models.UpdateBucketDetails(public_access_type="NoPublicAccess")
        client.update_bucket(namespace_name=ns, bucket_name=bucket_name, update_bucket_details=update_details)
        return ns, previous

    ns, previous = await loop.run_in_executor(None, _call)
    return {
        "action": "block_bucket_public_access",
        "bucket_name": bucket_name,
        "namespace": ns,
        "previous_public_access_type": previous,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_objectstorage_client
    import oci
    creds = getattr(connector, "credentials", {})
    bucket_name = execution_result.get("bucket_name", parameters.get("bucket_name", ""))
    namespace = execution_result.get("namespace", parameters.get("namespace", ""))
    previous = execution_result.get("previous_public_access_type", "ObjectRead")
    if not creds:
        return {"rolled_back": True, "mock": True}
    loop = asyncio.get_running_loop()
    client = get_objectstorage_client(creds)

    def _restore():
        ns = namespace or client.get_namespace().data
        details = oci.object_storage.models.UpdateBucketDetails(public_access_type=previous)
        client.update_bucket(namespace_name=ns, bucket_name=bucket_name, update_bucket_details=details)

    await loop.run_in_executor(None, _restore)
    return {"rolled_back": True, "bucket_name": bucket_name, "restored_public_access_type": previous}
```

---

### Task 4: Block Volume create/delete executors — create_block_volume.py + delete_block_volume.py

**Files:** Create: `backend/app/connectors/executors/oci/create_block_volume.py`, `backend/app/connectors/executors/oci/delete_block_volume.py`

- [ ] Step 1: Create `create_block_volume.py` implementing `oci_block_volume_create`. Parameters: `compartment_id`, `display_name` (default `"nexplane-volume"`), `size_in_gbs` (default `50`), `vpus_per_gb` (default `10`), `availability_domain` (resolved via `identity.list_availability_domains(compartment_id)[0].name` when not provided). Returns `_auto_asset` typed `storage_bucket` tagged `oci-block-volume`. Rollback: `delete_block_volume.execute`.

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", creds.get("compartment_id", "") if creds else "ocid1.compartment.oc1..mock")
    display_name = parameters.get("display_name", "nexplane-volume")
    size_in_gbs = parameters.get("size_in_gbs", 50)
    vpus_per_gb = parameters.get("vpus_per_gb", 10)
    region = (creds.get("region", "us-ashburn-1") if creds else "us-ashburn-1")

    auto_asset = {
        "name": display_name,
        "asset_type": "storage_bucket",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "volume_id": "",
            "compartment_id": compartment_id,
            "size_in_gbs": size_in_gbs,
            "lifecycle_state": "PROVISIONING",
            "vpus_per_gb": vpus_per_gb,
            "region": region,
        },
        "tags": ["oci", "block-volume", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["volume_id"] = "ocid1.volume.oc1..mock"
        return {
            "action": "create_block_volume",
            "volume_id": "ocid1.volume.oc1..mock",
            "display_name": display_name,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_blockstorage_client
    import oci
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        # Resolve availability domain if not provided
        availability_domain = parameters.get("availability_domain")
        if not availability_domain:
            identity_client = oci.identity.IdentityClient(oci.config.from_dict({
                "user": creds.get("user_ocid", ""),
                "fingerprint": creds.get("fingerprint", ""),
                "tenancy": creds.get("tenancy_ocid", ""),
                "region": creds.get("region", "us-ashburn-1"),
                "key_content": creds.get("private_key", ""),
            }))
            ads = identity_client.list_availability_domains(compartment_id=compartment_id).data
            availability_domain = ads[0].name if ads else "AD-1"

        details = oci.core.models.CreateVolumeDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            size_in_gbs=size_in_gbs,
            vpus_per_gb=vpus_per_gb,
            availability_domain=availability_domain,
        )
        volume = client.create_volume(create_volume_details=details).data
        return volume

    volume = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["volume_id"] = volume.id
    auto_asset["asset_metadata"]["lifecycle_state"] = volume.lifecycle_state

    return {
        "action": "create_block_volume",
        "volume_id": volume.id,
        "display_name": display_name,
        "size_in_gbs": size_in_gbs,
        "lifecycle_state": volume.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_block_volume import execute as delete
    return await delete({"volume_id": execution_result.get("volume_id", "")}, [], connector)
```

- [ ] Step 2: Create `delete_block_volume.py` implementing `oci_block_volume_delete`. Parameter: `volume_id` (from target asset). Preflight: call `compute.list_volume_attachments(compartment_id, volume_id=volume_id)` and fail if any attachment is in `ATTACHED` state. Then call `blockstorage.delete_volume(volume_id)`. No rollback.

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = parameters.get("volume_id", "")

    if not creds:
        return {"action": "delete_block_volume", "volume_id": volume_id, "mock": True}

    from ._client import get_blockstorage_client
    import oci
    client = get_blockstorage_client(creds)
    compartment_id = creds.get("compartment_id", "")
    loop = asyncio.get_running_loop()

    def _call():
        # Preflight: check not attached
        compute_client = oci.core.ComputeClient(oci.config.from_dict({
            "user": creds.get("user_ocid", ""),
            "fingerprint": creds.get("fingerprint", ""),
            "tenancy": creds.get("tenancy_ocid", ""),
            "region": creds.get("region", "us-ashburn-1"),
            "key_content": creds.get("private_key", ""),
        }))
        attachments = compute_client.list_volume_attachments(
            compartment_id=compartment_id, volume_id=volume_id
        ).data
        active = [a for a in attachments if a.lifecycle_state == "ATTACHED"]
        if active:
            raise ValueError(
                f"Volume '{volume_id}' is currently attached to instance '{active[0].instance_id}'. "
                "Detach the volume before deleting."
            )
        client.delete_volume(volume_id=volume_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_block_volume",
        "volume_id": volume_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_block_volume is destructive — no rollback"}
```

---

### Task 5: Block Volume attach/detach executors — attach_block_volume.py + detach_block_volume.py

**Files:** Create: `backend/app/connectors/executors/oci/attach_block_volume.py`, `backend/app/connectors/executors/oci/detach_block_volume.py`

- [ ] Step 1: Create `attach_block_volume.py` implementing `oci_block_volume_attach`. Parameters: `instance_id` (from target server asset), `volume_id` (from target block-volume asset), `display_name` (default `"nexplane-attachment"`), `type` (default `"paravirtualized"`), `is_read_only` (default `false`). Calls `compute.attach_volume`, then polls until `lifecycle_state == "ATTACHED"` (max 60 iterations, 5s sleep). Rollback: `detach_block_volume.execute`.

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    volume_id = parameters.get("volume_id", "")
    display_name = parameters.get("display_name", "nexplane-attachment")
    attach_type = parameters.get("type", "paravirtualized")
    is_read_only = parameters.get("is_read_only", False)

    if not creds:
        return {
            "action": "attach_block_volume",
            "instance_id": instance_id,
            "volume_id": volume_id,
            "volume_attachment_id": "ocid1.volumeattachment.oc1..mock",
            "mock": True,
        }

    import oci
    config = oci.config.from_dict({
        "user": creds.get("user_ocid", ""),
        "fingerprint": creds.get("fingerprint", ""),
        "tenancy": creds.get("tenancy_ocid", ""),
        "region": creds.get("region", "us-ashburn-1"),
        "key_content": creds.get("private_key", ""),
    })
    compute_client = oci.core.ComputeClient(config)
    compartment_id = creds.get("compartment_id", "")
    loop = asyncio.get_running_loop()

    def _attach():
        details = oci.core.models.AttachParavirtualizedVolumeDetails(
            instance_id=instance_id,
            volume_id=volume_id,
            display_name=display_name,
            is_read_only=is_read_only,
        )
        attachment = compute_client.attach_volume(attach_volume_details=details).data
        return attachment.id

    attachment_id = await loop.run_in_executor(None, _attach)

    # Poll until ATTACHED
    for _ in range(60):
        await asyncio.sleep(5)
        attachment = await loop.run_in_executor(
            None, lambda: compute_client.get_volume_attachment(volume_attachment_id=attachment_id).data
        )
        if attachment.lifecycle_state == "ATTACHED":
            break
        if attachment.lifecycle_state in ("DETACHED", "FAULTY"):
            raise RuntimeError(f"Attachment reached terminal state: {attachment.lifecycle_state}")

    return {
        "action": "attach_block_volume",
        "instance_id": instance_id,
        "volume_id": volume_id,
        "volume_attachment_id": attachment_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.detach_block_volume import execute as detach
    return await detach(
        {
            "instance_id": execution_result.get("instance_id", parameters.get("instance_id", "")),
            "volume_id": execution_result.get("volume_id", parameters.get("volume_id", "")),
        },
        [],
        connector,
    )
```

- [ ] Step 2: Create `detach_block_volume.py` implementing `oci_block_volume_detach`. Parameters: `instance_id`, `volume_id` (both from target assets). Resolves `volume_attachment_id` by calling `compute.list_volume_attachments(compartment_id, instance_id=instance_id)` filtered to `volume_id`. Calls `compute.detach_volume(volume_attachment_id)`, polls until `DETACHED`. Rollback: `attach_block_volume.execute` (re-attach).

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    volume_id = parameters.get("volume_id", "")

    if not creds:
        return {
            "action": "detach_block_volume",
            "instance_id": instance_id,
            "volume_id": volume_id,
            "mock": True,
        }

    import oci
    config = oci.config.from_dict({
        "user": creds.get("user_ocid", ""),
        "fingerprint": creds.get("fingerprint", ""),
        "tenancy": creds.get("tenancy_ocid", ""),
        "region": creds.get("region", "us-ashburn-1"),
        "key_content": creds.get("private_key", ""),
    })
    compute_client = oci.core.ComputeClient(config)
    compartment_id = creds.get("compartment_id", "")
    loop = asyncio.get_running_loop()

    def _find_and_detach():
        attachments = compute_client.list_volume_attachments(
            compartment_id=compartment_id, instance_id=instance_id
        ).data
        target = next((a for a in attachments if a.volume_id == volume_id and a.lifecycle_state == "ATTACHED"), None)
        if not target:
            raise ValueError(f"No ATTACHED volume attachment found for volume '{volume_id}' on instance '{instance_id}'")
        compute_client.detach_volume(volume_attachment_id=target.id)
        return target.id

    attachment_id = await loop.run_in_executor(None, _find_and_detach)

    # Poll until DETACHED
    for _ in range(60):
        await asyncio.sleep(5)
        attachment = await loop.run_in_executor(
            None, lambda: compute_client.get_volume_attachment(volume_attachment_id=attachment_id).data
        )
        if attachment.lifecycle_state == "DETACHED":
            break
        if attachment.lifecycle_state == "FAULTY":
            raise RuntimeError(f"Detachment reached FAULTY state")

    return {
        "action": "detach_block_volume",
        "instance_id": instance_id,
        "volume_id": volume_id,
        "volume_attachment_id": attachment_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.attach_block_volume import execute as attach
    return await attach(
        {
            "instance_id": execution_result.get("instance_id", parameters.get("instance_id", "")),
            "volume_id": execution_result.get("volume_id", parameters.get("volume_id", "")),
        },
        [],
        connector,
    )
```

---

### Task 6: Block Volume backup executor — create_block_volume_backup.py

**Files:** Create: `backend/app/connectors/executors/oci/create_block_volume_backup.py`

- [ ] Step 1: Create `create_block_volume_backup.py` implementing `oci_block_volume_backup`. Parameters: `volume_id` (from target asset), `display_name` (default `"nexplane-backup-<timestamp>"`), `type` (default `"INCREMENTAL"`). Calls `blockstorage.create_volume_backup`. Rollback: `blockstorage.delete_volume_backup(backup_id)`.

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ts = int(datetime.now(timezone.utc).timestamp())
    volume_id = parameters.get("volume_id", "")
    display_name = parameters.get("display_name", f"nexplane-backup-{ts}")
    backup_type = parameters.get("type", "INCREMENTAL")

    if not creds:
        return {
            "action": "create_block_volume_backup",
            "volume_id": volume_id,
            "backup_id": "ocid1.volumebackup.oc1..mock",
            "display_name": display_name,
            "mock": True,
        }

    from ._client import get_blockstorage_client
    import oci
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.core.models.CreateVolumeBackupDetails(
            volume_id=volume_id,
            display_name=display_name,
            type=backup_type,
        )
        backup = client.create_volume_backup(create_volume_backup_details=details).data
        return backup

    backup = await loop.run_in_executor(None, _call)
    return {
        "action": "create_block_volume_backup",
        "volume_id": volume_id,
        "backup_id": backup.id,
        "display_name": display_name,
        "lifecycle_state": backup.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    backup_id = execution_result.get("backup_id", "")
    if not creds or not backup_id:
        return {"rolled_back": False, "reason": "no backup_id or no credentials"}

    from ._client import get_blockstorage_client
    client = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: client.delete_volume_backup(volume_backup_id=backup_id))
    return {"rolled_back": True, "backup_id": backup_id}
```

---

### Task 7: Extend oci.json catalog — new ingest + change actions

**Files:** Modify: `backend/app/connectors/catalog/oci.json`

- [ ] Step 1: Add two ingest actions to the `"actions"` array:

```json
{
  "action_id": "discover_buckets",
  "action_type": "ingest",
  "display_name": "Discover Object Storage Buckets",
  "description": "Enumerate all OCI Object Storage buckets in the compartment.",
  "executor": "oci.discover_buckets",
  "generic_action": "discover_buckets",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "execution_tier": 1,
  "estimated_duration_seconds": 15
},
{
  "action_id": "discover_block_volumes",
  "action_type": "ingest",
  "display_name": "Discover Block Volumes",
  "description": "Enumerate all OCI Block Volumes in the compartment.",
  "executor": "oci.discover_block_volumes",
  "generic_action": "discover_block_volumes",
  "applicable_asset_types": ["cloud_account"],
  "parameters": [],
  "execution_tier": 1,
  "estimated_duration_seconds": 15
}
```

- [ ] Step 2: Add 9 change actions to `"actions"`:

```json
{
  "action_id": "oci_bucket_create",
  "action_type": "change",
  "display_name": "Create Object Storage Bucket",
  "description": "Create a new OCI Object Storage bucket.",
  "executor": "oci.create_bucket",
  "generic_action": "create_bucket",
  "applicable_asset_types": ["cloud_account"],
  "rollback_action": "oci_bucket_delete",
  "rollback_connector_type": "oci",
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "parameters": [
    {"name": "compartment_id", "type": "string", "required": true},
    {"name": "name", "type": "string", "required": false},
    {"name": "storage_tier", "type": "string", "required": false},
    {"name": "public_access_type", "type": "string", "required": false},
    {"name": "versioning", "type": "string", "required": false}
  ]
},
{
  "action_id": "oci_bucket_delete",
  "action_type": "change",
  "display_name": "Delete Object Storage Bucket",
  "description": "Delete an OCI Object Storage bucket (must be empty).",
  "executor": "oci.delete_bucket",
  "generic_action": "delete_bucket",
  "applicable_asset_types": ["storage_bucket"],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true},
    {"name": "namespace", "type": "string", "required": false}
  ]
},
{
  "action_id": "oci_bucket_lifecycle_set",
  "action_type": "change",
  "display_name": "Configure Bucket Lifecycle",
  "description": "Set lifecycle rules on an OCI Object Storage bucket.",
  "executor": "oci.configure_bucket_lifecycle",
  "generic_action": "configure_bucket_lifecycle",
  "applicable_asset_types": ["storage_bucket"],
  "rollback_action": "oci_bucket_lifecycle_set",
  "rollback_connector_type": "oci",
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true},
    {"name": "namespace", "type": "string", "required": false},
    {"name": "rules", "type": "json", "required": false}
  ]
},
{
  "action_id": "oci_bucket_block_public",
  "action_type": "change",
  "display_name": "Block Bucket Public Access",
  "description": "Set public_access_type to NoPublicAccess on an OCI bucket.",
  "executor": "oci.block_bucket_public_access",
  "generic_action": "block_bucket_public_access",
  "applicable_asset_types": ["storage_bucket"],
  "rollback_action": "oci_bucket_block_public",
  "rollback_connector_type": "oci",
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "parameters": [
    {"name": "bucket_name", "type": "string", "required": true},
    {"name": "namespace", "type": "string", "required": false}
  ]
},
{
  "action_id": "oci_block_volume_create",
  "action_type": "change",
  "display_name": "Create Block Volume",
  "description": "Create an OCI Block Volume.",
  "executor": "oci.create_block_volume",
  "generic_action": "create_block_volume",
  "applicable_asset_types": ["cloud_account"],
  "rollback_action": "oci_block_volume_delete",
  "rollback_connector_type": "oci",
  "execution_tier": 1,
  "estimated_duration_seconds": 30,
  "parameters": [
    {"name": "compartment_id", "type": "string", "required": true},
    {"name": "display_name", "type": "string", "required": false},
    {"name": "size_in_gbs", "type": "integer", "required": false},
    {"name": "vpus_per_gb", "type": "integer", "required": false},
    {"name": "availability_domain", "type": "string", "required": false}
  ]
},
{
  "action_id": "oci_block_volume_attach",
  "action_type": "change",
  "display_name": "Attach Block Volume",
  "description": "Attach an OCI Block Volume to a compute instance.",
  "executor": "oci.attach_block_volume",
  "generic_action": "attach_block_volume",
  "applicable_asset_types": ["server"],
  "rollback_action": "oci_block_volume_detach",
  "rollback_connector_type": "oci",
  "execution_tier": 2,
  "estimated_duration_seconds": 60,
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true},
    {"name": "volume_id", "type": "string", "required": true},
    {"name": "display_name", "type": "string", "required": false},
    {"name": "type", "type": "string", "required": false},
    {"name": "is_read_only", "type": "boolean", "required": false}
  ]
},
{
  "action_id": "oci_block_volume_detach",
  "action_type": "change",
  "display_name": "Detach Block Volume",
  "description": "Detach an OCI Block Volume from a compute instance.",
  "executor": "oci.detach_block_volume",
  "generic_action": "detach_block_volume",
  "applicable_asset_types": ["server"],
  "rollback_action": "oci_block_volume_attach",
  "rollback_connector_type": "oci",
  "execution_tier": 2,
  "estimated_duration_seconds": 60,
  "parameters": [
    {"name": "instance_id", "type": "string", "required": true},
    {"name": "volume_id", "type": "string", "required": true}
  ]
},
{
  "action_id": "oci_block_volume_delete",
  "action_type": "change",
  "display_name": "Delete Block Volume",
  "description": "Delete an OCI Block Volume (must be detached).",
  "executor": "oci.delete_block_volume",
  "generic_action": "delete_block_volume",
  "applicable_asset_types": ["storage_bucket"],
  "execution_tier": 1,
  "estimated_duration_seconds": 15,
  "parameters": [
    {"name": "volume_id", "type": "string", "required": true}
  ]
},
{
  "action_id": "oci_block_volume_backup",
  "action_type": "change",
  "display_name": "Create Block Volume Backup",
  "description": "Create an incremental backup of an OCI Block Volume.",
  "executor": "oci.create_block_volume_backup",
  "generic_action": "create_block_volume_backup",
  "applicable_asset_types": ["storage_bucket"],
  "rollback_action": "oci_block_volume_backup",
  "rollback_connector_type": "oci",
  "execution_tier": 1,
  "estimated_duration_seconds": 30,
  "parameters": [
    {"name": "volume_id", "type": "string", "required": true},
    {"name": "display_name", "type": "string", "required": false},
    {"name": "type", "type": "string", "required": false}
  ]
}
```

---

### Task 8: DB migration 041 + ChangeType enum + safety engine

**Files:**
- Create: `backend/alembic/versions/041_add_oci_storage_change_types.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`

- [ ] Step 1: Create `backend/alembic/versions/041_add_oci_storage_change_types.py`:

```python
"""add OCI storage change types

Revision ID: 041
Revises: 040
Create Date: 2026-05-10
"""
from alembic import op

revision = '041'
down_revision = '040'
branch_labels = None
depends_on = None


def upgrade():
    for t in [
        'oci_bucket_create',
        'oci_bucket_delete',
        'oci_bucket_lifecycle_set',
        'oci_bucket_block_public',
        'oci_block_volume_create',
        'oci_block_volume_attach',
        'oci_block_volume_detach',
        'oci_block_volume_delete',
        'oci_block_volume_backup',
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] Step 2: Add 9 new values to the `ChangeType` enum in `backend/app/models/change_request.py`. Add after the `ip_campaign` line (or after the last existing entry):

```python
    # OCI Sub-project 2 — Object Storage and Block Volumes
    oci_bucket_create = "oci_bucket_create"
    oci_bucket_delete = "oci_bucket_delete"
    oci_bucket_lifecycle_set = "oci_bucket_lifecycle_set"
    oci_bucket_block_public = "oci_bucket_block_public"
    oci_block_volume_create = "oci_block_volume_create"
    oci_block_volume_attach = "oci_block_volume_attach"
    oci_block_volume_detach = "oci_block_volume_detach"
    oci_block_volume_delete = "oci_block_volume_delete"
    oci_block_volume_backup = "oci_block_volume_backup"
```

- [ ] Step 3: Add all 9 to `_IMPLICIT_ROLLBACK_TYPES` in `backend/app/services/safety_engine.py`. Append after the last entry in the set:

```python
    "oci_bucket_create", "oci_bucket_delete",
    "oci_bucket_lifecycle_set", "oci_bucket_block_public",
    "oci_block_volume_create", "oci_block_volume_attach",
    "oci_block_volume_detach", "oci_block_volume_delete",
    "oci_block_volume_backup",
```

---

### Task 9: Frontend wiring — ChangeType + CreateChangeRequest + AssetDetail quick actions

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`
- Modify: `frontend/src/pages/AssetDetail.tsx`

- [ ] Step 1: Add 9 values to the `ChangeType` union in `frontend/src/types/api.ts`. Append before the closing `| "suppress";`:

```typescript
  | "oci_bucket_create"
  | "oci_bucket_delete"
  | "oci_bucket_lifecycle_set"
  | "oci_bucket_block_public"
  | "oci_block_volume_create"
  | "oci_block_volume_attach"
  | "oci_block_volume_detach"
  | "oci_block_volume_delete"
  | "oci_block_volume_backup"
```

- [ ] Step 2: Add 9 entries to `CHANGE_TYPE_META` in `frontend/src/pages/CreateChangeRequest.tsx`. Find the "Oracle Cloud" category section (from sub-project 1 — the OCI instance lifecycle entries) and add after the last OCI entry:

```typescript
  oci_bucket_create: {
    label: "Create Object Storage Bucket",
    description: "Create a new OCI Object Storage bucket.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "ocid1.compartment.oc1..aaaa",
      name: "nexplane-bucket",
      storage_tier: "Standard",
      public_access_type: "NoPublicAccess",
      versioning: "Disabled",
      rollback_strategy: "delete_bucket",
    }, null, 2),
  },
  oci_bucket_delete: {
    label: "Delete Object Storage Bucket",
    description: "Delete an OCI Object Storage bucket (must be empty).",
    outcomeTemplate: JSON.stringify({
      bucket_name: "",
      namespace: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_bucket_lifecycle_set: {
    label: "Configure Bucket Lifecycle",
    description: "Set lifecycle rules on an OCI Object Storage bucket.",
    outcomeTemplate: JSON.stringify({
      bucket_name: "",
      namespace: "",
      rules: [{ name: "expire-objects", action: "DELETE", time_amount: 90, time_unit: "DAYS", is_enabled: true }],
      rollback_strategy: "clear_lifecycle_rules",
    }, null, 2),
  },
  oci_bucket_block_public: {
    label: "Block Bucket Public Access",
    description: "Set public_access_type to NoPublicAccess on an OCI bucket.",
    outcomeTemplate: JSON.stringify({
      bucket_name: "",
      namespace: "",
      rollback_strategy: "restore_previous_access_type",
    }, null, 2),
  },
  oci_block_volume_create: {
    label: "Create Block Volume",
    description: "Create an OCI Block Volume.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "ocid1.compartment.oc1..aaaa",
      display_name: "nexplane-volume",
      size_in_gbs: 50,
      vpus_per_gb: 10,
      availability_domain: "",
      rollback_strategy: "delete_block_volume",
    }, null, 2),
  },
  oci_block_volume_attach: {
    label: "Attach Block Volume",
    description: "Attach an OCI Block Volume to a compute instance. Requires instance asset + block-volume asset as targets.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      volume_id: "",
      display_name: "nexplane-attachment",
      type: "paravirtualized",
      is_read_only: false,
      rollback_strategy: "detach_block_volume",
    }, null, 2),
  },
  oci_block_volume_detach: {
    label: "Detach Block Volume",
    description: "Detach an OCI Block Volume from a compute instance. Requires instance asset + block-volume asset as targets.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      volume_id: "",
      rollback_strategy: "reattach_block_volume",
    }, null, 2),
  },
  oci_block_volume_delete: {
    label: "Delete Block Volume",
    description: "Delete an OCI Block Volume (must be detached first).",
    outcomeTemplate: JSON.stringify({
      volume_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_block_volume_backup: {
    label: "Create Block Volume Backup",
    description: "Create an incremental backup of an OCI Block Volume.",
    outcomeTemplate: JSON.stringify({
      volume_id: "",
      display_name: "nexplane-backup",
      type: "INCREMENTAL",
      rollback_strategy: "delete_backup",
    }, null, 2),
  },
```

- [ ] Step 3: Locate the "Oracle Cloud" category array in the `CHANGE_TYPE_CATEGORIES` (or equivalent grouping structure) and add the 9 new keys:
`"oci_bucket_create"`, `"oci_bucket_delete"`, `"oci_bucket_lifecycle_set"`, `"oci_bucket_block_public"`, `"oci_block_volume_create"`, `"oci_block_volume_attach"`, `"oci_block_volume_detach"`, `"oci_block_volume_delete"`, `"oci_block_volume_backup"`.

- [ ] Step 4: Add quick actions to `frontend/src/pages/AssetDetail.tsx`. Find the section that renders quick-action buttons for `storage_bucket` assets. Add two conditional blocks:

  **For OCI Object Storage buckets** (tags include `"oci"` and `"object-storage"`, does NOT include `"block-volume"`):
  - "Configure Lifecycle" → navigate to `CreateChangeRequest` with `change_type=oci_bucket_lifecycle_set` and `asset_id=<asset.id>`
  - "Block Public Access" → navigate with `change_type=oci_bucket_block_public`
  - "Delete Bucket" → navigate with `change_type=oci_bucket_delete`

  **For OCI Block Volumes** (tags include `"oci-block-volume"` or `"block-volume"`):
  - "Attach Volume" → navigate with `change_type=oci_block_volume_attach`
  - "Detach Volume" → navigate with `change_type=oci_block_volume_detach`
  - "Backup Volume" → navigate with `change_type=oci_block_volume_backup`
  - "Delete Volume" → navigate with `change_type=oci_block_volume_delete`

  Follow the existing pattern for quick-action buttons already used for AWS/GCP `storage_bucket` assets in the file. Use `useNavigate` and pass query params `?change_type=X&asset_id=Y`.

---

### Task 10: Commit all

**Files:** All files created/modified in Tasks 1–9

- [ ] Step 1: Stage all new executor files:
```
git add backend/app/connectors/executors/oci/discover_buckets.py
git add backend/app/connectors/executors/oci/discover_block_volumes.py
git add backend/app/connectors/executors/oci/create_bucket.py
git add backend/app/connectors/executors/oci/delete_bucket.py
git add backend/app/connectors/executors/oci/configure_bucket_lifecycle.py
git add backend/app/connectors/executors/oci/block_bucket_public_access.py
git add backend/app/connectors/executors/oci/create_block_volume.py
git add backend/app/connectors/executors/oci/delete_block_volume.py
git add backend/app/connectors/executors/oci/attach_block_volume.py
git add backend/app/connectors/executors/oci/detach_block_volume.py
git add backend/app/connectors/executors/oci/create_block_volume_backup.py
```

- [ ] Step 2: Stage modified and new backend files:
```
git add backend/app/connectors/executors/oci/_client.py
git add backend/app/connectors/catalog/oci.json
git add backend/alembic/versions/041_add_oci_storage_change_types.py
git add backend/app/models/change_request.py
git add backend/app/services/safety_engine.py
```

- [ ] Step 3: Stage frontend files:
```
git add frontend/src/types/api.ts
git add frontend/src/pages/CreateChangeRequest.tsx
git add frontend/src/pages/AssetDetail.tsx
```

- [ ] Step 4: Commit:
```
git commit -m "feat(oci): sub-project 2 — Object Storage and Block Volume executors"
```

- [ ] Step 5: Restart the frontend container to pick up the frontend changes:
```
docker compose stop frontend && docker compose up frontend -d
```

---

## Smoke Test Reference

### Phase OCI_F — Object Storage
1. Fire `oci_bucket_create` against a cloud_account asset → assert `storage_bucket` asset created in inventory with `bucket_name` in metadata
2. Fire `oci_bucket_lifecycle_set` against that bucket → OCI SDK verify `client.get_object_lifecycle_policy(ns, bucket_name)` returns the configured rules
3. Fire `oci_bucket_block_public` → OCI SDK verify `client.get_bucket(ns, bucket_name).data.public_access_type == "NoPublicAccess"`
4. Rollback via Nexplane (`oci_bucket_delete`) → verify asset removed from inventory

### Phase OCI_G — Block Volumes
1. Fire `oci_block_volume_create` → OCI SDK verify `blockstorage.get_volume(volume_id).data.lifecycle_state == "AVAILABLE"`, asset in inventory
2. Fire `oci_block_volume_attach` (target: Phase OCI_B instance + new volume) → OCI SDK verify attachment ATTACHED
3. Fire `oci_block_volume_detach` → OCI SDK verify DETACHED
4. Fire `oci_block_volume_backup` → OCI SDK verify backup lifecycle_state reaches AVAILABLE
5. Fire `oci_block_volume_delete` → OCI SDK verify volume lifecycle_state TERMINATED; asset removed from inventory

> All smoke test cleanup must use Nexplane rollback as primary mechanism; direct OCI SDK deletion is safety net only.
