# OCI Connector Sub-project 1: Foundation + Compute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the OCI connector foundation — auth client, compartment/instance/VCN discovery, compute lifecycle executors, VCN+subnet creation, catalog, DB migration, ChangeType enum additions, safety engine wiring, and frontend type/UI wiring — so Oracle Cloud Infrastructure resources are fully manageable via Nexplane change requests.

**Architecture:** The OCI connector follows the identical pattern as GCP and Azure: a `_client.py` auth factory builds typed OCI SDK clients from stored credentials; individual executor modules wrap blocking SDK calls with `asyncio.run_in_executor`; an `oci.json` catalog maps action IDs to executors; discovery executors return assets using existing `cloud_account`, `server`, and `application` asset types (no schema change needed).

**Tech Stack:** OCI Python SDK (oci>=2.130.0), Python asyncio, FastAPI, React/TypeScript

**Pre-conditions:**
- OCI connector already seeded in DB with `connector_id = 0b3cf029-5ca0-4794-b982-6f494aaca372`
- `oci` enum value already added to `connector_type` in DB
- `oci>=2.130.0` already in `requirements.txt`

---

### Task 1: `_client.py` + `__init__.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/__init__.py`
- Create: `backend/app/connectors/executors/oci/_client.py`

- [ ] Step 1: Create the `__init__.py` (empty package marker):

```python
# backend/app/connectors/executors/oci/__init__.py
```

- [ ] Step 2: Create `_client.py` with the auth factory pattern:

```python
# backend/app/connectors/executors/oci/_client.py


def get_oci_config(creds: dict) -> dict:
    """Build OCI SDK config dict from stored connector credentials."""
    return {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds["region"],
    }


def get_identity_client(creds: dict):
    """Return an OCI IdentityClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.identity.IdentityClient(config)


def get_compute_client(creds: dict):
    """Return an OCI ComputeClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.core.ComputeClient(config)


def get_network_client(creds: dict):
    """Return an OCI VirtualNetworkClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.core.VirtualNetworkClient(config)


def get_blockstorage_client(creds: dict):
    """Return an OCI BlockstorageClient authenticated with stored credentials."""
    import oci
    config = get_oci_config(creds)
    return oci.core.BlockstorageClient(config)
```

- [ ] Step 3: Verify the module imports cleanly:

```bash
cd backend && python -c "from app.connectors.executors.oci._client import get_oci_config, get_identity_client, get_compute_client, get_network_client, get_blockstorage_client; print('OK')"
```

---

### Task 2: `discover_compartments.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/discover_compartments.py`

- [ ] Step 1: Create the executor:

```python
# backend/app/connectors/executors/oci/discover_compartments.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    if not creds:
        root_asset = {
            "name": "OCI · mock-tenancy",
            "asset_type": "cloud_account",
            "asset_metadata": {
                "compartment_id": "ocid1.tenancy.oc1..mock",
                "tenancy_id": "ocid1.tenancy.oc1..mock",
                "region": "us-ashburn-1",
                "provider": "oci",
                "lifecycle_state": "ACTIVE",
            },
            "tags": ["oci", "compartment"],
        }
        return {
            "action": "discover_compartments",
            "assets": [root_asset],
            "count": 1,
            "mock": True,
            "_auto_asset": root_asset,
        }

    from ._client import get_oci_config, get_identity_client

    identity = get_identity_client(creds)
    tenancy_id = creds["tenancy"]
    region = creds["region"]
    loop = asyncio.get_running_loop()

    # Fetch tenancy name for root compartment label
    tenancy = await loop.run_in_executor(
        None, lambda: identity.get_tenancy(tenancy_id).data
    )
    tenancy_name = tenancy.name

    # List all compartments recursively
    compartments_response = await loop.run_in_executor(
        None,
        lambda: identity.list_compartments(
            tenancy_id,
            compartment_id_in_subtree=True,
            lifecycle_state="ACTIVE",
        ).data,
    )

    assets = []

    # Root tenancy compartment
    root_asset = {
        "name": f"OCI · {tenancy_name}",
        "asset_type": "cloud_account",
        "asset_metadata": {
            "compartment_id": tenancy_id,
            "tenancy_id": tenancy_id,
            "region": region,
            "provider": "oci",
            "lifecycle_state": "ACTIVE",
        },
        "tags": ["oci", "compartment"],
    }
    assets.append(root_asset)

    # Child compartments
    for comp in compartments_response:
        assets.append({
            "name": f"OCI · {comp.name}",
            "asset_type": "cloud_account",
            "asset_metadata": {
                "compartment_id": comp.id,
                "tenancy_id": tenancy_id,
                "region": region,
                "provider": "oci",
                "lifecycle_state": comp.lifecycle_state,
            },
            "tags": ["oci", "compartment"],
        })

    return {
        "action": "discover_compartments",
        "assets": assets,
        "count": len(assets),
        "_auto_asset": root_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] Step 2: Verify import:

```bash
cd backend && python -c "from app.connectors.executors.oci.discover_compartments import execute; print('OK')"
```

---

### Task 3: `discover_instances.py` + `discover_vcns.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/discover_instances.py`
- Create: `backend/app/connectors/executors/oci/discover_vcns.py`

- [ ] Step 1: Create `discover_instances.py`:

```python
# backend/app/connectors/executors/oci/discover_instances.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_instances",
            "assets": [
                {
                    "name": "mock-oci-instance",
                    "asset_type": "server",
                    "asset_metadata": {
                        "instance_id": "ocid1.instance.oc1..mock",
                        "shape": "VM.Standard.E2.1.Micro",
                        "region": "us-ashburn-1",
                        "compartment_id": compartment_id or "ocid1.tenancy.oc1..mock",
                        "lifecycle_state": "RUNNING",
                        "private_ip": "10.0.0.10",
                        "image_id": "ocid1.image.oc1..mock",
                    },
                    "tags": ["oci", "compute"],
                }
            ],
            "count": 1,
            "mock": True,
        }

    from ._client import get_compute_client, get_network_client

    region = creds["region"]
    tenancy_id = creds["tenancy"]

    # If compartment_id not supplied, use tenancy root
    if not compartment_id:
        compartment_id = tenancy_id

    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    instances_data = await loop.run_in_executor(
        None,
        lambda: compute.list_instances(compartment_id).data,
    )

    assets = []
    for inst in instances_data:
        if inst.lifecycle_state == "TERMINATED":
            continue

        # Fetch private IP via VNIC attachments
        private_ip = None
        try:
            vnic_attachments = await loop.run_in_executor(
                None,
                lambda i=inst: compute.list_vnic_attachments(
                    compartment_id=compartment_id, instance_id=i.id
                ).data,
            )
            if vnic_attachments:
                vnic = await loop.run_in_executor(
                    None,
                    lambda va=vnic_attachments[0]: network.get_vnic(va.vnic_id).data,
                )
                private_ip = vnic.private_ip
        except Exception:
            pass

        display_name = inst.display_name or inst.id.split(".")[-1]
        assets.append({
            "name": display_name,
            "asset_type": "server",
            "asset_metadata": {
                "instance_id": inst.id,
                "shape": inst.shape,
                "region": region,
                "compartment_id": compartment_id,
                "lifecycle_state": inst.lifecycle_state,
                "private_ip": private_ip,
                "image_id": inst.image_id,
            },
            "tags": ["oci", "compute"],
        })

    return {
        "action": "discover_instances",
        "assets": assets,
        "count": len(assets),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] Step 2: Create `discover_vcns.py`:

```python
# backend/app/connectors/executors/oci/discover_vcns.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_vcns",
            "assets": [
                {
                    "name": "mock-vcn",
                    "asset_type": "application",
                    "asset_metadata": {
                        "vcn_id": "ocid1.vcn.oc1..mock",
                        "cidr_block": "10.0.0.0/16",
                        "compartment_id": compartment_id or "ocid1.tenancy.oc1..mock",
                        "lifecycle_state": "AVAILABLE",
                        "provider": "oci",
                    },
                    "tags": ["oci", "oci-vcn"],
                },
                {
                    "name": "mock-subnet",
                    "asset_type": "application",
                    "asset_metadata": {
                        "subnet_id": "ocid1.subnet.oc1..mock",
                        "vcn_id": "ocid1.vcn.oc1..mock",
                        "cidr_block": "10.0.0.0/24",
                        "compartment_id": compartment_id or "ocid1.tenancy.oc1..mock",
                        "lifecycle_state": "AVAILABLE",
                        "provider": "oci",
                    },
                    "tags": ["oci", "oci-subnet"],
                },
            ],
            "count": 2,
            "mock": True,
        }

    from ._client import get_network_client

    region = creds["region"]
    tenancy_id = creds["tenancy"]
    if not compartment_id:
        compartment_id = tenancy_id

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    vcns_data = await loop.run_in_executor(
        None,
        lambda: network.list_vcns(compartment_id).data,
    )
    subnets_data = await loop.run_in_executor(
        None,
        lambda: network.list_subnets(compartment_id).data,
    )

    assets = []

    for vcn in vcns_data:
        if vcn.lifecycle_state == "TERMINATED":
            continue
        assets.append({
            "name": vcn.display_name or vcn.id.split(".")[-1],
            "asset_type": "application",
            "asset_metadata": {
                "vcn_id": vcn.id,
                "cidr_block": vcn.cidr_block,
                "compartment_id": compartment_id,
                "region": region,
                "lifecycle_state": vcn.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-vcn"],
        })

    for subnet in subnets_data:
        if subnet.lifecycle_state == "TERMINATED":
            continue
        assets.append({
            "name": subnet.display_name or subnet.id.split(".")[-1],
            "asset_type": "application",
            "asset_metadata": {
                "subnet_id": subnet.id,
                "vcn_id": subnet.vcn_id,
                "cidr_block": subnet.cidr_block,
                "compartment_id": compartment_id,
                "region": region,
                "lifecycle_state": subnet.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-subnet"],
        })

    return {
        "action": "discover_vcns",
        "assets": assets,
        "count": len(assets),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] Step 3: Verify imports:

```bash
cd backend && python -c "
from app.connectors.executors.oci.discover_instances import execute
from app.connectors.executors.oci.discover_vcns import execute as execute2
print('OK')
"
```

---

### Task 4: `launch_instance.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/launch_instance.py`

- [ ] Step 1: Create `launch_instance.py`:

```python
# backend/app/connectors/executors/oci/launch_instance.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    # Read parameters (all with defaults for quick mode)
    compartment_id = parameters.get("compartment_id", "")
    subnet_id = parameters.get("subnet_id", "")
    name = parameters.get("name", "nexplane-oci-instance")
    mode = parameters.get("mode", "quick")
    os_choice = parameters.get("os", "oracle_linux")
    shape = parameters.get("shape", "VM.Standard.E2.1.Micro")
    ocpus = parameters.get("ocpus", 1)
    memory_in_gbs = parameters.get("memory_in_gbs", 1)
    image_id = parameters.get("image_id", "")
    key_pair_id = parameters.get("key_pair_id", "")
    ssh_public_key = parameters.get("ssh_public_key", "")

    auto_asset = {
        "name": name,
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "instance_id": "",
            "shape": shape,
            "region": creds.get("region", "us-ashburn-1") if creds else "us-ashburn-1",
            "compartment_id": compartment_id,
            "lifecycle_state": "PROVISIONING",
            "provider": "oci",
        },
        "tags": ["oci", "compute", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["instance_id"] = "ocid1.instance.oc1..mock"
        auto_asset["asset_metadata"]["private_ip"] = "10.0.0.10"
        return {
            "action": "launch_instance",
            "instance_id": "ocid1.instance.oc1..mock",
            "shape": shape,
            "private_ip": "10.0.0.10",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_compute_client, get_network_client
    import oci

    region = creds["region"]
    tenancy_id = creds["tenancy"]

    if not compartment_id:
        compartment_id = tenancy_id

    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # --- Resolve subnet_id ---
    if not subnet_id:
        subnets = await loop.run_in_executor(
            None,
            lambda: network.list_subnets(compartment_id).data,
        )
        available = [s for s in subnets if s.lifecycle_state == "AVAILABLE"]
        if not available:
            raise ValueError(
                "No available subnet in this compartment. "
                "Run oci_vcn_create → oci_subnet_create first."
            )
        subnet_id = available[0].id

    # --- Resolve image_id ---
    if not image_id or mode == "quick":
        # Choose OS keyword for list_images filter
        os_map = {
            "oracle_linux": "Oracle Linux",
            "ubuntu": "Canonical Ubuntu",
        }
        os_filter = os_map.get(os_choice, "Oracle Linux")

        images = await loop.run_in_executor(
            None,
            lambda: compute.list_images(
                compartment_id,
                operating_system=os_filter,
                shape=shape,
                sort_by="TIMECREATED",
                sort_order="DESC",
            ).data,
        )
        platform_images = [img for img in images if img.base_image_id is None]
        if not platform_images:
            raise ValueError(
                f"No platform image found for OS '{os_filter}' and shape '{shape}' in region '{region}'."
            )
        image_id = platform_images[0].id

    # --- Resolve SSH public key ---
    if not ssh_public_key and key_pair_id:
        # Attempt to load public key from key_pair asset metadata
        try:
            from app.services.assets_service import get_asset_by_id
            from app.database import get_db_session
            async with get_db_session() as db:
                kp_asset = await get_asset_by_id(db, key_pair_id)
                if kp_asset:
                    ssh_public_key = kp_asset.asset_metadata.get("public_key", "")
        except Exception:
            pass

    # --- Build LaunchInstanceDetails ---
    metadata = {}
    if ssh_public_key:
        metadata["ssh_authorized_keys"] = ssh_public_key

    shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
        ocpus=float(ocpus),
        memory_in_gbs=float(memory_in_gbs),
    )

    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment_id,
        display_name=name,
        shape=shape,
        shape_config=shape_config,
        image_id=image_id,
        subnet_id=subnet_id,
        metadata=metadata,
        freeform_tags={"managed-by": "nexplane"},
    )

    response = await loop.run_in_executor(
        None,
        lambda: compute.launch_instance(details).data,
    )
    instance_id = response.id

    # --- Wait for RUNNING ---
    from .wait_instance_state import execute as wait
    await wait(
        {"instance_id": instance_id, "target_state": "RUNNING"},
        [],
        connector,
    )

    # --- Fetch private IP ---
    private_ip = None
    try:
        vnic_attachments = await loop.run_in_executor(
            None,
            lambda: compute.list_vnic_attachments(
                compartment_id=compartment_id, instance_id=instance_id
            ).data,
        )
        if vnic_attachments:
            from ._client import get_network_client as _net
            net2 = _net(creds)
            vnic = await loop.run_in_executor(
                None,
                lambda va=vnic_attachments[0]: net2.get_vnic(va.vnic_id).data,
            )
            private_ip = vnic.private_ip
    except Exception:
        pass

    auto_asset["asset_metadata"]["instance_id"] = instance_id
    auto_asset["asset_metadata"]["private_ip"] = private_ip
    auto_asset["asset_metadata"]["lifecycle_state"] = "RUNNING"

    return {
        "action": "launch_instance",
        "instance_id": instance_id,
        "shape": shape,
        "private_ip": private_ip,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .terminate_instance import execute as terminate
    instance_id = execution_result.get("instance_id") or parameters.get("instance_id", "")
    return await terminate(
        {"instance_id": instance_id, "preserve_boot_volume": False},
        [],
        connector,
    )
```

- [ ] Step 2: Verify import:

```bash
cd backend && python -c "from app.connectors.executors.oci.launch_instance import execute; print('OK')"
```

---

### Task 5: `stop_instance.py` + `start_instance.py` + `reboot_instance.py` + `terminate_instance.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/stop_instance.py`
- Create: `backend/app/connectors/executors/oci/start_instance.py`
- Create: `backend/app/connectors/executors/oci/reboot_instance.py`
- Create: `backend/app/connectors/executors/oci/terminate_instance.py`

- [ ] Step 1: Create `stop_instance.py`:

```python
# backend/app/connectors/executors/oci/stop_instance.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")

    if not creds:
        return {
            "action": "stop_instance",
            "instance_id": instance_id,
            "lifecycle_state": "STOPPED",
            "mock": True,
        }

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(
        None,
        lambda: compute.instance_action(instance_id, "STOP"),
    )

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "STOPPED"}, [], connector)

    return {
        "action": "stop_instance",
        "instance_id": instance_id,
        "lifecycle_state": "STOPPED",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .start_instance import execute as start
    return await start(
        {"instance_id": execution_result.get("instance_id") or parameters.get("instance_id", "")},
        [],
        connector,
    )
```

- [ ] Step 2: Create `start_instance.py`:

```python
# backend/app/connectors/executors/oci/start_instance.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")

    if not creds:
        return {
            "action": "start_instance",
            "instance_id": instance_id,
            "lifecycle_state": "RUNNING",
            "mock": True,
        }

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(
        None,
        lambda: compute.instance_action(instance_id, "START"),
    )

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "RUNNING"}, [], connector)

    return {
        "action": "start_instance",
        "instance_id": instance_id,
        "lifecycle_state": "RUNNING",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .stop_instance import execute as stop
    return await stop(
        {"instance_id": execution_result.get("instance_id") or parameters.get("instance_id", "")},
        [],
        connector,
    )
```

- [ ] Step 3: Create `reboot_instance.py`:

```python
# backend/app/connectors/executors/oci/reboot_instance.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")

    if not creds:
        return {
            "action": "reboot_instance",
            "instance_id": instance_id,
            "lifecycle_state": "RUNNING",
            "mock": True,
        }

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    # SOFTRESET = graceful reboot
    await loop.run_in_executor(
        None,
        lambda: compute.instance_action(instance_id, "SOFTRESET"),
    )

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "RUNNING"}, [], connector)

    return {
        "action": "reboot_instance",
        "instance_id": instance_id,
        "lifecycle_state": "RUNNING",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot has no rollback"}
```

- [ ] Step 4: Create `terminate_instance.py`:

```python
# backend/app/connectors/executors/oci/terminate_instance.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    preserve_boot_volume = parameters.get("preserve_boot_volume", False)

    if not creds:
        return {
            "action": "terminate_instance",
            "instance_id": instance_id,
            "lifecycle_state": "TERMINATED",
            "mock": True,
        }

    from ._client import get_compute_client
    import oci

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(
        None,
        lambda: compute.terminate_instance(
            instance_id,
            preserve_boot_volume=preserve_boot_volume,
        ),
    )

    from .wait_instance_state import execute as wait
    await wait({"instance_id": instance_id, "target_state": "TERMINATED"}, [], connector)

    return {
        "action": "terminate_instance",
        "instance_id": instance_id,
        "lifecycle_state": "TERMINATED",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terminate is destructive — no rollback"}
```

- [ ] Step 5: Verify imports:

```bash
cd backend && python -c "
from app.connectors.executors.oci.stop_instance import execute
from app.connectors.executors.oci.start_instance import execute as s2
from app.connectors.executors.oci.reboot_instance import execute as s3
from app.connectors.executors.oci.terminate_instance import execute as s4
print('OK')
"
```

---

### Task 6: `create_volume_snapshot.py` + `wait_instance_state.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/wait_instance_state.py`
- Create: `backend/app/connectors/executors/oci/create_volume_snapshot.py`

- [ ] Step 1: Create `wait_instance_state.py` (internal utility — must exist before Task 4/5 executors run):

```python
# backend/app/connectors/executors/oci/wait_instance_state.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Poll compute.get_instance() every 10s up to 10 minutes until target_state is reached."""
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    target_state = parameters.get("target_state", "RUNNING")

    if not creds:
        return {
            "action": "wait_instance_state",
            "instance_id": instance_id,
            "target_state": target_state,
            "reached": True,
            "mock": True,
        }

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    max_polls = 60  # 60 × 10s = 10 minutes
    for attempt in range(max_polls):
        instance = await loop.run_in_executor(
            None,
            lambda: compute.get_instance(instance_id).data,
        )
        current_state = instance.lifecycle_state
        if current_state == target_state:
            return {
                "action": "wait_instance_state",
                "instance_id": instance_id,
                "target_state": target_state,
                "current_state": current_state,
                "reached": True,
                "polls": attempt + 1,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        # TERMINATED is a terminal state — stop waiting if we were not targeting it
        if current_state == "TERMINATED" and target_state != "TERMINATED":
            raise RuntimeError(
                f"Instance {instance_id} reached TERMINATED while waiting for {target_state}."
            )
        await asyncio.sleep(10)

    raise TimeoutError(
        f"Instance {instance_id} did not reach state '{target_state}' within 10 minutes."
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait has no rollback"}
```

- [ ] Step 2: Create `create_volume_snapshot.py`:

```python
# backend/app/connectors/executors/oci/create_volume_snapshot.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    display_name = parameters.get(
        "display_name",
        f"nexplane-snapshot-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
    )
    backup_type = parameters.get("backup_type", "INCREMENTAL")

    if not creds:
        return {
            "action": "create_volume_snapshot",
            "instance_id": instance_id,
            "backup_id": "ocid1.bootvolume_backup.oc1..mock",
            "display_name": display_name,
            "backup_type": backup_type,
            "lifecycle_state": "AVAILABLE",
            "mock": True,
        }

    from ._client import get_compute_client, get_blockstorage_client
    import oci

    region = creds["region"]
    compute = get_compute_client(creds)
    blockstorage = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()

    # Resolve compartment_id from instance
    instance = await loop.run_in_executor(
        None,
        lambda: compute.get_instance(instance_id).data,
    )
    compartment_id = instance.compartment_id

    # Find boot volume attachment
    bv_attachments = await loop.run_in_executor(
        None,
        lambda: compute.list_boot_volume_attachments(
            availability_domain=instance.availability_domain,
            compartment_id=compartment_id,
            instance_id=instance_id,
        ).data,
    )
    if not bv_attachments:
        raise ValueError(f"No boot volume attachment found for instance {instance_id}.")

    boot_volume_id = bv_attachments[0].boot_volume_id

    # Create boot volume backup
    details = oci.core.models.CreateBootVolumeBackupDetails(
        boot_volume_id=boot_volume_id,
        display_name=display_name,
        type=backup_type,
    )
    backup = await loop.run_in_executor(
        None,
        lambda: blockstorage.create_boot_volume_backup(details).data,
    )

    return {
        "action": "create_volume_snapshot",
        "instance_id": instance_id,
        "boot_volume_id": boot_volume_id,
        "backup_id": backup.id,
        "display_name": display_name,
        "backup_type": backup_type,
        "lifecycle_state": backup.lifecycle_state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    backup_id = execution_result.get("backup_id", "")

    if not backup_id or not creds:
        return {"rolled_back": False, "reason": "no backup_id to delete"}

    from ._client import get_blockstorage_client
    import asyncio

    blockstorage = get_blockstorage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: blockstorage.delete_boot_volume_backup(backup_id),
    )
    return {"rolled_back": True, "backup_id": backup_id}
```

- [ ] Step 3: Verify imports:

```bash
cd backend && python -c "
from app.connectors.executors.oci.wait_instance_state import execute
from app.connectors.executors.oci.create_volume_snapshot import execute as s2
print('OK')
"
```

---

### Task 7: `create_vcn.py` + `create_subnet.py`
**Files:**
- Create: `backend/app/connectors/executors/oci/create_vcn.py`
- Create: `backend/app/connectors/executors/oci/create_subnet.py`

- [ ] Step 1: Create `create_vcn.py`:

```python
# backend/app/connectors/executors/oci/create_vcn.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-vcn")
    cidr_block = parameters.get("cidr_block", "10.0.0.0/16")
    dns_label = parameters.get("dns_label", "nexplanevcn")

    auto_asset = {
        "name": display_name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "vcn_id": "",
            "cidr_block": cidr_block,
            "compartment_id": compartment_id,
            "internet_gateway_id": "",
            "provider": "oci",
            "lifecycle_state": "AVAILABLE",
        },
        "tags": ["oci", "oci-vcn", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["vcn_id"] = "ocid1.vcn.oc1..mock"
        auto_asset["asset_metadata"]["internet_gateway_id"] = "ocid1.internetgateway.oc1..mock"
        return {
            "action": "create_vcn",
            "vcn_id": "ocid1.vcn.oc1..mock",
            "internet_gateway_id": "ocid1.internetgateway.oc1..mock",
            "cidr_block": cidr_block,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_network_client
    import oci

    tenancy_id = creds["tenancy"]
    if not compartment_id:
        compartment_id = tenancy_id

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Create VCN
    vcn_details = oci.core.models.CreateVcnDetails(
        compartment_id=compartment_id,
        display_name=display_name,
        cidr_block=cidr_block,
        dns_label=dns_label,
        freeform_tags={"managed-by": "nexplane"},
    )
    vcn = await loop.run_in_executor(
        None,
        lambda: network.create_vcn(vcn_details).data,
    )
    vcn_id = vcn.id

    # Create Internet Gateway
    ig_details = oci.core.models.CreateInternetGatewayDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name=f"{display_name}-igw",
        is_enabled=True,
        freeform_tags={"managed-by": "nexplane"},
    )
    igw = await loop.run_in_executor(
        None,
        lambda: network.create_internet_gateway(ig_details).data,
    )
    igw_id = igw.id

    # Update default route table: 0.0.0.0/0 → internet gateway
    route_rules = [
        oci.core.models.RouteRule(
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
            network_entity_id=igw_id,
        )
    ]
    update_rt_details = oci.core.models.UpdateRouteTableDetails(route_rules=route_rules)
    await loop.run_in_executor(
        None,
        lambda: network.update_route_table(vcn.default_route_table_id, update_rt_details),
    )

    auto_asset["asset_metadata"]["vcn_id"] = vcn_id
    auto_asset["asset_metadata"]["internet_gateway_id"] = igw_id

    return {
        "action": "create_vcn",
        "vcn_id": vcn_id,
        "internet_gateway_id": igw_id,
        "cidr_block": cidr_block,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete internet gateway, then VCN (subnets must have been deleted first)."""
    creds = getattr(connector, "credentials", {})
    vcn_id = execution_result.get("vcn_id", "")
    igw_id = execution_result.get("internet_gateway_id", "")

    if not vcn_id or not creds:
        return {"rolled_back": False, "reason": "no vcn_id to delete"}

    from ._client import get_network_client
    import asyncio

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Detach and delete internet gateway first
    if igw_id:
        # Clear route table rules referencing the IGW
        try:
            vcn = await loop.run_in_executor(None, lambda: network.get_vcn(vcn_id).data)
            import oci
            await loop.run_in_executor(
                None,
                lambda: network.update_route_table(
                    vcn.default_route_table_id,
                    oci.core.models.UpdateRouteTableDetails(route_rules=[]),
                ),
            )
        except Exception:
            pass
        await loop.run_in_executor(None, lambda: network.delete_internet_gateway(igw_id))

    await loop.run_in_executor(None, lambda: network.delete_vcn(vcn_id))
    return {"rolled_back": True, "vcn_id": vcn_id}
```

- [ ] Step 2: Create `create_subnet.py`:

```python
# backend/app/connectors/executors/oci/create_subnet.py
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    compartment_id = parameters.get("compartment_id", "")
    vcn_id = parameters.get("vcn_id", "")
    display_name = parameters.get("display_name", "nexplane-subnet")
    cidr_block = parameters.get("cidr_block", "10.0.0.0/24")
    dns_label = parameters.get("dns_label", "nexplanesubnet")
    prohibit_public_ip = parameters.get("prohibit_public_ip_on_vnic", False)

    auto_asset = {
        "name": display_name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "subnet_id": "",
            "vcn_id": vcn_id,
            "cidr_block": cidr_block,
            "compartment_id": compartment_id,
            "security_list_id": "",
            "provider": "oci",
            "lifecycle_state": "AVAILABLE",
        },
        "tags": ["oci", "oci-subnet", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["subnet_id"] = "ocid1.subnet.oc1..mock"
        auto_asset["asset_metadata"]["security_list_id"] = "ocid1.securitylist.oc1..mock"
        return {
            "action": "create_subnet",
            "subnet_id": "ocid1.subnet.oc1..mock",
            "vcn_id": vcn_id,
            "security_list_id": "ocid1.securitylist.oc1..mock",
            "cidr_block": cidr_block,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_network_client
    import oci

    tenancy_id = creds["tenancy"]
    region = creds["region"]

    if not compartment_id:
        compartment_id = tenancy_id

    # Resolve vcn_id if not supplied
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    if not vcn_id:
        vcns = await loop.run_in_executor(
            None,
            lambda: network.list_vcns(compartment_id).data,
        )
        available_vcns = [v for v in vcns if v.lifecycle_state == "AVAILABLE"]
        if not available_vcns:
            raise ValueError(
                "No available VCN in this compartment. Run oci_vcn_create first."
            )
        vcn_id = available_vcns[0].id

    # Create Security List with SSH ingress + all egress
    tcp = "6"
    icmp = "1"
    ingress_rules = [
        oci.core.models.IngressSecurityRule(
            protocol=tcp,
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            tcp_options=oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(min=22, max=22)
            ),
        ),
        oci.core.models.IngressSecurityRule(
            protocol=icmp,
            source="0.0.0.0/0",
            source_type="CIDR_BLOCK",
            icmp_options=oci.core.models.IcmpOptions(type=3, code=4),
        ),
    ]
    egress_rules = [
        oci.core.models.EgressSecurityRule(
            protocol="all",
            destination="0.0.0.0/0",
            destination_type="CIDR_BLOCK",
        )
    ]
    sl_details = oci.core.models.CreateSecurityListDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name=f"{display_name}-seclist",
        ingress_security_rules=ingress_rules,
        egress_security_rules=egress_rules,
        freeform_tags={"managed-by": "nexplane"},
    )
    security_list = await loop.run_in_executor(
        None,
        lambda: network.create_security_list(sl_details).data,
    )
    security_list_id = security_list.id

    # Create Subnet
    subnet_details = oci.core.models.CreateSubnetDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name=display_name,
        cidr_block=cidr_block,
        dns_label=dns_label,
        prohibit_public_ip_on_vnic=prohibit_public_ip,
        security_list_ids=[security_list_id],
        freeform_tags={"managed-by": "nexplane"},
    )
    subnet = await loop.run_in_executor(
        None,
        lambda: network.create_subnet(subnet_details).data,
    )
    subnet_id = subnet.id

    auto_asset["asset_metadata"]["subnet_id"] = subnet_id
    auto_asset["asset_metadata"]["vcn_id"] = vcn_id
    auto_asset["asset_metadata"]["security_list_id"] = security_list_id

    return {
        "action": "create_subnet",
        "subnet_id": subnet_id,
        "vcn_id": vcn_id,
        "security_list_id": security_list_id,
        "cidr_block": cidr_block,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    subnet_id = execution_result.get("subnet_id", "")
    security_list_id = execution_result.get("security_list_id", "")

    if not subnet_id or not creds:
        return {"rolled_back": False, "reason": "no subnet_id to delete"}

    from ._client import get_network_client
    import asyncio

    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, lambda: network.delete_subnet(subnet_id))
    if security_list_id:
        try:
            await loop.run_in_executor(
                None, lambda: network.delete_security_list(security_list_id)
            )
        except Exception:
            pass
    return {"rolled_back": True, "subnet_id": subnet_id}
```

- [ ] Step 3: Verify imports:

```bash
cd backend && python -c "
from app.connectors.executors.oci.create_vcn import execute
from app.connectors.executors.oci.create_subnet import execute as s2
print('OK')
"
```

---

### Task 8: `oci.json` catalog
**Files:**
- Create: `backend/app/connectors/catalog/oci.json`

- [ ] Step 1: Create the catalog file:

```json
{
  "connector_type": "oci",
  "display_name": "Oracle Cloud Infrastructure",
  "credential_fields": [
    {"name": "tenancy", "label": "Tenancy OCID", "type": "string", "required": true, "placeholder": "ocid1.tenancy.oc1.."},
    {"name": "user", "label": "User OCID", "type": "string", "required": true, "placeholder": "ocid1.user.oc1.."},
    {"name": "fingerprint", "label": "API Key Fingerprint", "type": "string", "required": true, "placeholder": "xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx"},
    {"name": "region", "label": "Home Region", "type": "string", "required": true, "placeholder": "us-ashburn-1"},
    {"name": "private_key", "label": "PEM Private Key", "type": "password", "required": true, "placeholder": "-----BEGIN RSA PRIVATE KEY-----\n..."}
  ],
  "actions": [
    {
      "action_id": "discover_compartments",
      "generic_action": "discover",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Discover Compartments",
      "description": "Discover all OCI compartments (including root tenancy). Each becomes a cloud_account asset.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [],
      "executor": "oci.discover_compartments",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "discover_instances",
      "generic_action": "discover",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Discover Instances",
      "description": "Discover OCI compute instances in a compartment. Each becomes a server asset.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "compartment_id", "type": "string", "required": false, "description": "OCID of compartment; defaults to root tenancy"}
      ],
      "executor": "oci.discover_instances",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "discover_vcns",
      "generic_action": "discover",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Discover VCNs & Subnets",
      "description": "Discover OCI Virtual Cloud Networks and subnets in a compartment.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "compartment_id", "type": "string", "required": false, "description": "OCID of compartment; defaults to root tenancy"}
      ],
      "executor": "oci.discover_vcns",
      "estimated_duration_seconds": 20
    },
    {
      "action_id": "oci_instance_create",
      "generic_action": "launch_instance",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Launch OCI Instance",
      "description": "Create a new OCI Compute instance. Defaults to VM.Standard.E2.1.Micro (always-free eligible). Rollback: terminate.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "compartment_id", "type": "string", "required": false, "description": "OCID of target compartment; resolved from asset metadata"},
        {"name": "subnet_id", "type": "string", "required": false, "description": "OCID of subnet; auto-resolved from compartment if omitted"},
        {"name": "name", "type": "string", "required": false, "default": "nexplane-oci-instance"},
        {"name": "mode", "type": "string", "required": false, "default": "quick", "description": "quick | spec"},
        {"name": "os", "type": "string", "required": false, "default": "oracle_linux", "description": "oracle_linux | ubuntu"},
        {"name": "shape", "type": "string", "required": false, "default": "VM.Standard.E2.1.Micro"},
        {"name": "ocpus", "type": "number", "required": false, "default": 1},
        {"name": "memory_in_gbs", "type": "number", "required": false, "default": 1},
        {"name": "image_id", "type": "string", "required": false, "description": "Explicit image OCID (spec mode); auto-resolved in quick mode"},
        {"name": "key_pair_id", "type": "string", "required": false, "description": "Nexplane key_pair asset ID; public key injected via metadata"},
        {"name": "ssh_public_key", "type": "string", "required": false, "description": "Raw SSH public key string (alternative to key_pair_id)"}
      ],
      "executor": "oci.launch_instance",
      "rollback_action": "oci_instance_delete",
      "estimated_duration_seconds": 300,
      "blast_radius_hint": "new_resource"
    },
    {
      "action_id": "oci_instance_stop",
      "generic_action": "stop_instance",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Stop OCI Instance",
      "description": "Gracefully stop a running OCI compute instance. Rollback: start.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true, "description": "OCID of the instance; auto-populated from target asset metadata"}
      ],
      "executor": "oci.stop_instance",
      "rollback_action": "oci_instance_start",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "oci_instance_start",
      "generic_action": "start_instance",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Start OCI Instance",
      "description": "Start a stopped OCI compute instance. Rollback: stop.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true, "description": "OCID of the instance; auto-populated from target asset metadata"}
      ],
      "executor": "oci.start_instance",
      "rollback_action": "oci_instance_stop",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "oci_instance_reboot",
      "generic_action": "reboot_instance",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Reboot OCI Instance",
      "description": "Graceful reboot (SOFTRESET) of a running OCI compute instance.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true, "description": "OCID of the instance; auto-populated from target asset metadata"}
      ],
      "executor": "oci.reboot_instance",
      "estimated_duration_seconds": 120
    },
    {
      "action_id": "oci_instance_delete",
      "generic_action": "terminate_instance",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Terminate OCI Instance",
      "description": "Permanently terminate an OCI compute instance.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true, "description": "OCID of the instance; auto-populated from target asset metadata"},
        {"name": "preserve_boot_volume", "type": "boolean", "required": false, "default": false}
      ],
      "executor": "oci.terminate_instance",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "destructive"
    },
    {
      "action_id": "oci_block_volume_snapshot",
      "generic_action": "create_disk_snapshot",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Create OCI Boot Volume Snapshot",
      "description": "Create an incremental boot volume backup for an OCI compute instance. Rollback: delete backup.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true, "description": "OCID of the instance; auto-populated from target asset metadata"},
        {"name": "display_name", "type": "string", "required": false, "description": "Backup display name; defaults to nexplane-snapshot-<timestamp>"},
        {"name": "backup_type", "type": "string", "required": false, "default": "INCREMENTAL", "description": "INCREMENTAL | FULL"}
      ],
      "executor": "oci.create_volume_snapshot",
      "rollback_action": "delete_boot_volume_backup",
      "estimated_duration_seconds": 120
    },
    {
      "action_id": "oci_vcn_create",
      "generic_action": "create_network",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Create OCI VCN",
      "description": "Create an OCI Virtual Cloud Network with internet gateway and default route. Rollback: delete VCN.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "compartment_id", "type": "string", "required": false, "description": "OCID of compartment; resolved from asset metadata"},
        {"name": "display_name", "type": "string", "required": false, "default": "nexplane-vcn"},
        {"name": "cidr_block", "type": "string", "required": false, "default": "10.0.0.0/16"},
        {"name": "dns_label", "type": "string", "required": false, "default": "nexplanevcn"}
      ],
      "executor": "oci.create_vcn",
      "rollback_action": "delete_vcn",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "new_resource"
    },
    {
      "action_id": "oci_subnet_create",
      "generic_action": "create_subnet",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Create OCI Subnet",
      "description": "Create an OCI subnet with SSH + ICMP security list. Rollback: delete subnet.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "compartment_id", "type": "string", "required": false, "description": "OCID of compartment; resolved from asset metadata"},
        {"name": "vcn_id", "type": "string", "required": false, "description": "OCID of VCN; auto-resolved from compartment if omitted"},
        {"name": "display_name", "type": "string", "required": false, "default": "nexplane-subnet"},
        {"name": "cidr_block", "type": "string", "required": false, "default": "10.0.0.0/24"},
        {"name": "dns_label", "type": "string", "required": false, "default": "nexplanesubnet"},
        {"name": "prohibit_public_ip_on_vnic", "type": "boolean", "required": false, "default": false}
      ],
      "executor": "oci.create_subnet",
      "rollback_action": "delete_subnet",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "new_resource"
    }
  ]
}
```

- [ ] Step 2: Verify the catalog JSON parses:

```bash
cd backend && python -c "import json; d=json.load(open('app/connectors/catalog/oci.json')); print(f\"OK — {len(d['actions'])} actions\")"
```

---

### Task 9: DB Migration 040
**Files:**
- Create: `backend/alembic/versions/040_add_oci_change_types.py`

- [ ] Step 1: Create the migration:

```python
"""add oci change_type values

Revision ID: 040
Revises: 039
Create Date: 2026-05-10
"""
from alembic import op

revision = '040'
down_revision = '039'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # connector_type 'oci' is already seeded in the DB — skip ALTER TYPE connector_type
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_stop'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_start'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_reboot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_delete'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_snapshot'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_vcn_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_subnet_create'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
```

- [ ] Step 2: Run the migration:

```bash
cd backend && alembic upgrade 040
```

- [ ] Step 3: Verify migration was applied:

```bash
cd backend && alembic current
```

Expected output includes `040`.

---

### Task 10: `ChangeType` enum + safety engine
**Files:**
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`

- [ ] Step 1: Add 8 OCI values to the `ChangeType` enum in `change_request.py`. Locate the Azure Plans 4 block (last group before agent commands) and add after it:

```python
    # OCI Compute — Sub-project 1
    oci_instance_create = "oci_instance_create"
    oci_instance_stop = "oci_instance_stop"
    oci_instance_start = "oci_instance_start"
    oci_instance_reboot = "oci_instance_reboot"
    oci_instance_delete = "oci_instance_delete"
    oci_block_volume_snapshot = "oci_block_volume_snapshot"
    oci_vcn_create = "oci_vcn_create"
    oci_subnet_create = "oci_subnet_create"
```

Insert these lines immediately after the `azure_metric_alert_delete = "azure_metric_alert_delete"` line (before `# Agent command group`).

- [ ] Step 2: Add OCI types to `_IMPLICIT_ROLLBACK_TYPES` in `safety_engine.py`. Find the string-based entries near the bottom of the set and add:

```python
    "oci_instance_create", "oci_instance_stop", "oci_instance_start",
    "oci_instance_reboot", "oci_instance_delete", "oci_block_volume_snapshot",
    "oci_vcn_create", "oci_subnet_create",
```

Add these lines inside the `_IMPLICIT_ROLLBACK_TYPES` set, after the existing `"azure_storage_account_create", "azure_storage_account_delete",` line.

- [ ] Step 3: Verify the model imports cleanly:

```bash
cd backend && python -c "from app.models.change_request import ChangeType; print(ChangeType.oci_instance_create, ChangeType.oci_subnet_create, 'OK')"
```

- [ ] Step 4: Verify safety engine imports cleanly:

```bash
cd backend && python -c "from app.services.safety_engine import _IMPLICIT_ROLLBACK_TYPES; assert 'oci_instance_create' in _IMPLICIT_ROLLBACK_TYPES; print('OK')"
```

---

### Task 11: Frontend `api.ts` + `CreateChangeRequest.tsx`
**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] Step 1: In `frontend/src/types/api.ts`, add `"oci"` to `ConnectorType`. Find the `| "ansible_local"` line (last entry) and add after it:

```typescript
  | "oci";
```

Replace the closing semicolon on `"ansible_local"` line so the union looks like:

```typescript
  | "ansible_local"
  | "oci";
```

- [ ] Step 2: In `frontend/src/types/api.ts`, add 8 OCI values to the `ChangeType` union. Find the `| "ip_campaign"` line and add after it:

```typescript
  | "oci_instance_create"
  | "oci_instance_stop"
  | "oci_instance_start"
  | "oci_instance_reboot"
  | "oci_instance_delete"
  | "oci_block_volume_snapshot"
  | "oci_vcn_create"
  | "oci_subnet_create"
```

- [ ] Step 3: In `frontend/src/pages/CreateChangeRequest.tsx`, add the OCI entries to `CHANGE_TYPE_META`. Find the closing brace of `agent_containerize_auto` entry (before the `};` that closes `CHANGE_TYPE_META`) and add:

```typescript
  oci_instance_create: {
    label: "Launch OCI Instance ✨ AI",
    description: "Create a new Oracle Cloud compute instance (VM.Standard.E2.1.Micro, always-free eligible). Auto-resolves subnet. Rollback: terminate.",
    outcomeTemplate: JSON.stringify({
      name: "nexplane-oci-instance",
      mode: "quick",
      os: "oracle_linux",
      shape: "VM.Standard.E2.1.Micro",
      ocpus: 1,
      memory_in_gbs: 1,
      compartment_id: "",
      subnet_id: "",
      ssh_public_key: "",
      rollback_strategy: "terminate_instance",
    }, null, 2),
  },
  oci_instance_stop: {
    label: "Stop OCI Instance",
    description: "Gracefully stop a running OCI compute instance. Rollback: start.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      rollback_strategy: "start_instance",
    }, null, 2),
  },
  oci_instance_start: {
    label: "Start OCI Instance",
    description: "Start a stopped OCI compute instance. Rollback: stop.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      rollback_strategy: "stop_instance",
    }, null, 2),
  },
  oci_instance_reboot: {
    label: "Reboot OCI Instance",
    description: "Graceful reboot (SOFTRESET) of a running OCI compute instance.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_instance_delete: {
    label: "Terminate OCI Instance",
    description: "Permanently terminate an OCI compute instance. This is destructive.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      preserve_boot_volume: false,
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_block_volume_snapshot: {
    label: "Create OCI Boot Volume Snapshot",
    description: "Create an incremental boot volume backup for an OCI instance. Rollback: delete backup.",
    outcomeTemplate: JSON.stringify({
      instance_id: "",
      display_name: "nexplane-snapshot",
      backup_type: "INCREMENTAL",
      rollback_strategy: "delete_boot_volume_backup",
    }, null, 2),
  },
  oci_vcn_create: {
    label: "Create OCI VCN",
    description: "Create an OCI Virtual Cloud Network with internet gateway and route table. Rollback: delete VCN.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-vcn",
      cidr_block: "10.0.0.0/16",
      dns_label: "nexplanevcn",
      rollback_strategy: "delete_vcn",
    }, null, 2),
  },
  oci_subnet_create: {
    label: "Create OCI Subnet",
    description: "Create an OCI subnet with SSH + ICMP security list. Rollback: delete subnet.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      vcn_id: "",
      display_name: "nexplane-subnet",
      cidr_block: "10.0.0.0/24",
      dns_label: "nexplanesubnet",
      prohibit_public_ip_on_vnic: false,
      rollback_strategy: "delete_subnet",
    }, null, 2),
  },
```

- [ ] Step 4: In `CreateChangeRequest.tsx`, add an **Oracle Cloud** group to `CHANGE_TYPE_GROUPS`. Find the `{ label: "GCE Instances", ...` entry and add after the Azure VMs group:

```typescript
  {
    label: "Oracle Cloud",
    types: [
      "oci_instance_create",
      "oci_instance_stop",
      "oci_instance_start",
      "oci_instance_reboot",
      "oci_instance_delete",
      "oci_block_volume_snapshot",
      "oci_vcn_create",
      "oci_subnet_create",
    ],
  },
```

- [ ] Step 5: In `CreateChangeRequest.tsx`, add OCI entries to `CHANGE_TYPE_ASSET_FILTER` (find the `gce_instance_create: "cloud_account"` area and add):

```typescript
  oci_instance_create: "cloud_account",
  oci_vcn_create: "cloud_account",
  oci_subnet_create: "cloud_account",
  oci_instance_stop: "server",
  oci_instance_start: "server",
  oci_instance_reboot: "server",
  oci_instance_delete: "server",
  oci_block_volume_snapshot: "server",
```

- [ ] Step 6: Restart the frontend container and verify no TypeScript errors:

```bash
docker compose stop frontend && docker compose up frontend -d
docker compose logs frontend --tail=30
```

---

### Task 12: Commit all
**Files:** All files created/modified in Tasks 1–11

- [ ] Step 1: Stage all OCI files:

```bash
git add \
  backend/app/connectors/executors/oci/__init__.py \
  backend/app/connectors/executors/oci/_client.py \
  backend/app/connectors/executors/oci/discover_compartments.py \
  backend/app/connectors/executors/oci/discover_instances.py \
  backend/app/connectors/executors/oci/discover_vcns.py \
  backend/app/connectors/executors/oci/launch_instance.py \
  backend/app/connectors/executors/oci/stop_instance.py \
  backend/app/connectors/executors/oci/start_instance.py \
  backend/app/connectors/executors/oci/reboot_instance.py \
  backend/app/connectors/executors/oci/terminate_instance.py \
  backend/app/connectors/executors/oci/create_volume_snapshot.py \
  backend/app/connectors/executors/oci/wait_instance_state.py \
  backend/app/connectors/executors/oci/create_vcn.py \
  backend/app/connectors/executors/oci/create_subnet.py \
  backend/app/connectors/catalog/oci.json \
  backend/alembic/versions/040_add_oci_change_types.py \
  backend/app/models/change_request.py \
  backend/app/services/safety_engine.py \
  frontend/src/types/api.ts \
  frontend/src/pages/CreateChangeRequest.tsx
```

- [ ] Step 2: Verify everything staged looks correct:

```bash
git status
git diff --cached --stat
```

- [ ] Step 3: Commit:

```bash
git commit -m "$(cat <<'EOF'
feat: OCI connector sub-project 1 — foundation + compute

Adds the complete OCI connector foundation:
- _client.py auth factory (identity, compute, network, blockstorage clients)
- discover_compartments / discover_instances / discover_vcns executors
- launch_instance with quick/spec mode, image auto-resolution, subnet auto-resolution
- stop / start / reboot / terminate instance executors (with rollbacks)
- create_volume_snapshot with boot volume backup + rollback
- wait_instance_state polling utility (10s interval, 10 min timeout)
- create_vcn (+ internet gateway + route table) and create_subnet (+ security list)
- oci.json catalog with 3 ingest + 8 change actions
- Alembic migration 040: 8 new oci_* change_type enum values
- ChangeType enum + _IMPLICIT_ROLLBACK_TYPES updated for all 8 OCI types
- Frontend: ConnectorType + ChangeType unions, Oracle Cloud group in CreateChangeRequest

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] Step 4: Confirm commit:

```bash
git log --oneline -3
```
