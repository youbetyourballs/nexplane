# OCI Connector — Sub-project 1: Foundation + Compute Design

## Scope

This spec covers the first independently shippable OCI connector sub-project: authentication, compartment discovery, compute instance lifecycle, VCN/subnet setup, and smoke tests. It does not cover storage, identity, database, or observability (separate sub-projects).

---

## Architecture

The OCI connector follows the identical pattern as the existing GCP and Azure connectors:

- `_client.py` — SDK auth factory, returns typed OCI clients
- Executor modules — one file per action, async-wrapped blocking SDK calls
- `oci.json` catalog — credential field definitions + action-to-executor mappings
- DB migration — adds `oci` to the `connector_type` enum and 8 new `change_type` values
- Frontend wiring — `ConnectorType`, `ChangeType`, `CreateChangeRequest`, `AssetDetail`
- Smoke test track — 5 phases in `test_multicloud_live.py`

OCI credentials are already seeded in the DB for the `nexplane` tenancy in `us-ashburn-1`. The OCI Python SDK (`oci>=2.130.0`) is already added to `requirements.txt`.

---

## Compartment Model

OCI organizes all resources under compartments (nested within the root tenancy). Each discovered compartment — including the root tenancy itself — becomes a `cloud_account` asset in Nexplane inventory. CRs target a compartment asset the same way AWS CRs target a cloud_account asset.

**Why `cloud_account`:** No schema change required. The existing `cloud_account` asset type, detail panel, and ingest dedup logic all apply directly.

**Compartment asset shape:**
```json
{
  "name": "OCI · <compartment_name>",
  "asset_type": "cloud_account",
  "asset_metadata": {
    "compartment_id": "ocid1.compartment.oc1...",
    "tenancy_id": "ocid1.tenancy.oc1...",
    "region": "us-ashburn-1",
    "provider": "oci",
    "lifecycle_state": "ACTIVE"
  },
  "tags": ["oci", "compartment"]
}
```

**Dedup key:** `compartment_id` — added to the ingest service's meta-key list alongside `instance_id`, `bucket_name`, etc.

---

## File Structure

```
backend/app/connectors/
  catalog/oci.json
  executors/oci/
    __init__.py
    _client.py
    discover_compartments.py
    discover_instances.py
    discover_vcns.py
    launch_instance.py
    stop_instance.py
    start_instance.py
    reboot_instance.py
    terminate_instance.py
    create_volume_snapshot.py
    create_vcn.py
    create_subnet.py
    wait_instance_state.py

backend/alembic/versions/040_add_oci_change_types.py

frontend/src/types/api.ts               (add "oci" + 8 change types)
frontend/src/pages/CreateChangeRequest.tsx  (Oracle Cloud category)
```

---

## Authentication (`_client.py`)

Builds an OCI SDK config dict from stored credentials and returns typed clients. All SDK calls are blocking — wrapped in `asyncio.run_in_executor(None, fn)`.

```python
def get_oci_config(creds: dict) -> dict:
    return {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds["region"],
    }

def get_identity_client(creds: dict) -> oci.identity.IdentityClient
def get_compute_client(creds: dict) -> oci.core.ComputeClient
def get_network_client(creds: dict) -> oci.core.VirtualNetworkClient
def get_blockstorage_client(creds: dict) -> oci.core.BlockstorageClient
```

Mock path (no creds): all executors return a realistic mock dict with `"mock": True`.

---

## Catalog (`oci.json`)

**Credential fields:**
- `tenancy` — Tenancy OCID (`ocid1.tenancy.oc1..`)
- `user` — User OCID (`ocid1.user.oc1..`)
- `fingerprint` — API key fingerprint (`xx:xx:xx:...`)
- `region` — Home region (`us-ashburn-1`)
- `private_key` — PEM private key (password field, multiline)

**Actions (ingest):**
- `discover_compartments` — maps to `oci.discover_compartments`
- `discover_instances` — maps to `oci.discover_instances`
- `discover_vcns` — maps to `oci.discover_vcns`

**Actions (change):**
- `oci_instance_create` → `oci.launch_instance`
- `oci_instance_stop` → `oci.stop_instance`
- `oci_instance_start` → `oci.start_instance`
- `oci_instance_reboot` → `oci.reboot_instance`
- `oci_instance_delete` → `oci.terminate_instance`
- `oci_block_volume_snapshot` → `oci.create_volume_snapshot`
- `oci_vcn_create` → `oci.create_vcn`
- `oci_subnet_create` → `oci.create_subnet`

---

## Discovery Executors

### `discover_compartments.py`
- Calls `identity.list_compartments(tenancy_id, compartment_id_in_subtree=True)`
- Includes root tenancy as one compartment (name: tenancy name, compartment_id = tenancy_id)
- Returns `assets` list (one per compartment, all `cloud_account` type)
- Root tenancy also returned as `_auto_asset`

### `discover_instances.py`
- Called per compartment — accepts `compartment_id` parameter
- Calls `compute.list_instances(compartment_id)`
- Fetches private IP via `network.list_vnic_attachments()` + `network.get_vnic()`
- Each instance → `server` asset:
  ```json
  {
    "name": "<display_name or ocid-suffix>",
    "asset_type": "server",
    "asset_metadata": {
      "instance_id": "ocid1.instance.oc1...",
      "shape": "VM.Standard.E2.1.Micro",
      "region": "us-ashburn-1",
      "compartment_id": "ocid1.compartment...",
      "lifecycle_state": "RUNNING",
      "private_ip": "10.0.0.x",
      "image_id": "ocid1.image..."
    },
    "tags": ["oci", "compute"]
  }
  ```
- Dedup key: `instance_id`

### `discover_vcns.py`
- Calls `network.list_vcns(compartment_id)` + `network.list_subnets(compartment_id)`
- VCNs → `application` assets tagged `oci-vcn`; subnets → `application` assets tagged `oci-subnet`
- Metadata includes `vcn_id`, `cidr_block`, `subnet_id` (for subnet assets), `vcn_id` reference
- Dedup keys: `vcn_id` and `subnet_id` respectively

---

## Compute Lifecycle Executors

### `launch_instance.py` (`oci_instance_create`)

**Mode: quick** (default) — resolves `VM.Standard.E2.1.Micro` shape + latest Oracle Linux or Ubuntu platform image for the region automatically via `compute.list_images()`.

**Mode: spec** — accepts explicit `shape`, `image_id`, `ocpus`, `memory_in_gbs`.

**Parameters (all with pre-populated defaults):**
```
compartment_id       resolved from target asset metadata
subnet_id            resolved from oci-subnet assets in same compartment
key_pair_id          Nexplane key_pair asset ID (public key injected via metadata)
mode                 "quick"
name                 "nexplane-oci-instance"
os                   "oracle_linux"   (quick mode: oracle_linux | ubuntu)
shape                "VM.Standard.E2.1.Micro"
ocpus                1
memory_in_gbs        1
```

**Preflight check:** verify `subnet_id` exists and lifecycle state is AVAILABLE. If no subnet found, error message: "No available subnet in this compartment. Run oci_vcn_create → oci_subnet_create first."

**SSH key injection:** reads `key_pair` asset metadata for the public key, passes as `metadata.ssh_authorized_keys` in the `LaunchInstanceDetails`.

**Returns `_auto_asset`** (server) + `instance_id`, `shape`, `private_ip`.

**Rollback:** `oci_instance_delete`.

### `stop_instance.py` / `start_instance.py` / `reboot_instance.py`
- Single required parameter: `instance_id` (OCID, auto-populated from target asset metadata)
- Calls `compute.instance_action(instance_id, action)` where action = `STOP` / `START` / `SOFTRESET`
- Polls `wait_instance_state.py` for target state (`STOPPED` / `RUNNING` / `RUNNING`)
- Rollback: stop→start, start→stop, reboot→none

### `terminate_instance.py` (`oci_instance_delete`)
- Parameters: `instance_id` (auto-populated), `preserve_boot_volume: false`
- Calls `compute.terminate_instance(instance_id, preserve_boot_volume=False)`
- Polls until `TERMINATED`
- Rollback: none (destructive)

### `create_volume_snapshot.py` (`oci_block_volume_snapshot`)
- Resolves boot volume OCID from `instance_id` via `compute.list_boot_volume_attachments()`
- Calls `blockstorage.create_boot_volume_backup()`
- Parameters (all pre-populated):
  ```
  instance_id      auto-populated from target asset
  display_name     "nexplane-snapshot-<timestamp>"
  backup_type      "INCREMENTAL"
  ```
- Rollback: `blockstorage.delete_boot_volume_backup()`

### `wait_instance_state.py` (internal utility)
- Polls `compute.get_instance()` every 10s up to 10 minutes
- Returns when lifecycle_state matches target or raises on timeout

---

## VCN + Subnet Executors

### `create_vcn.py` (`oci_vcn_create`)

**Parameters (all pre-populated):**
```
compartment_id   resolved from target asset
display_name     "nexplane-vcn"
cidr_block       "10.0.0.0/16"
dns_label        "nexplanevnc"
```

Also creates: Internet Gateway + Route Table rule (`0.0.0.0/0` → gateway). This gives instances outbound internet access for agent download + Tailscale without extra steps.

Returns `_auto_asset` (application, tagged `oci-vcn`) with `vcn_id`, `cidr_block`, `internet_gateway_id` in metadata.

**Rollback:** deletes subnets + internet gateway first (in order), then VCN.

### `create_subnet.py` (`oci_subnet_create`)

**Parameters (all pre-populated):**
```
compartment_id              resolved from target asset
vcn_id                      resolved from oci-vcn asset in same compartment (first AVAILABLE; override with explicit OCID if multiple VCNs exist)
display_name                "nexplane-subnet"
cidr_block                  "10.0.0.0/24"
dns_label                   "nexplanesubnet"
prohibit_public_ip_on_vnic  false
```

Also creates a **Security List** with:
- Inbound: SSH (port 22, TCP, 0.0.0.0/0), ICMP type 3+4
- Outbound: all traffic (0.0.0.0/0)

Returns `_auto_asset` (application, tagged `oci-subnet`) with `subnet_id`, `vcn_id`, `cidr_block`, `security_list_id` in metadata.

**Rollback:** delete subnet.

---

## Safety Engine

All 8 change types added to `_IMPLICIT_ROLLBACK_TYPES`:
```python
"oci_instance_create", "oci_instance_stop", "oci_instance_start",
"oci_instance_reboot", "oci_instance_delete", "oci_block_volume_snapshot",
"oci_vcn_create", "oci_subnet_create",
```

---

## Frontend Wiring

### `api.ts`
- `"oci"` added to `ConnectorType` union
- 8 values added to `ChangeType` union: `oci_instance_create`, `oci_instance_stop`, `oci_instance_start`, `oci_instance_reboot`, `oci_instance_delete`, `oci_block_volume_snapshot`, `oci_vcn_create`, `oci_subnet_create`

### `CreateChangeRequest.tsx`
New **"Oracle Cloud"** category group. Every change type has:
- Label, description, AI badge (on `oci_instance_create`)
- `outcomeTemplate` with **all parameters pre-filled with sensible defaults** — users unfamiliar with OCI should be able to create a working CR without editing anything

General principle applied here (and to be retrofitted to all change types): all CR outcome templates must pre-populate every parameter with a working default so users never need to look up platform-specific values.

### `AssetDetail.tsx`
OCI assets already handled by existing metadata panels:
- Compartments → `cloud_account` panel (`compartment_id`, `region`, `provider`)
- Instances → `server` panel (`instance_id`, `shape`, `private_ip`, `lifecycle_state`)
- VCNs/subnets → `application` panel (`vcn_id`/`subnet_id`, `cidr_block`)

Quick actions for OCI server assets: stop, start, reboot, snapshot, terminate.

### Connector UI
`"oci"` added to the Add Connector dropdown with the 5 credential fields from `oci.json`.

---

## DB Migration (040)

```sql
ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'oci';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_stop';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_start';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_reboot';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_instance_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_block_volume_snapshot';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_vcn_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_subnet_create';
```

Python model: 8 new `ChangeType` enum values.

---

## Smoke Test

New OCI track in `backend/tests/smoke/test_multicloud_live.py`, running in parallel with existing AWS/GCP/Azure tracks.

### Phases

**OCI_A — Foundation**
1. Trigger `discover_compartments` ingest → verify root tenancy appears as `cloud_account` asset
2. Fire `oci_vcn_create` CR targeting compartment asset → verify `oci-vcn` application asset in inventory
3. Fire `oci_subnet_create` CR → verify `oci-subnet` application asset in inventory
4. OCI SDK: verify VCN + subnet exist and are AVAILABLE

**OCI_B — Instance Launch + Connectivity**
1. Fire `oci_instance_create` (quick mode, Oracle Linux, VM.Standard.E2.1.Micro)
2. Poll until RUNNING (up to 10 min)
3. Verify `server` asset in inventory with correct `instance_id` + `private_ip`
4. Fire `tailscale_join` CR → verify Tailscale connectivity
5. Fire `deploy_nexplane_agent` CR → wait for agent to register as server asset in inventory

**OCI_C — Instance Lifecycle**
1. Fire `oci_instance_stop` → OCI SDK verify STOPPED
2. Fire `oci_instance_start` → OCI SDK verify RUNNING
3. Fire `oci_instance_reboot` → OCI SDK verify RUNNING post-reboot

**OCI_D — Snapshot**
1. Fire `oci_block_volume_snapshot` CR
2. Poll OCI SDK `blockstorage.get_boot_volume_backup()` until AVAILABLE
3. Verify backup OCID in CR result

**OCI_E — Teardown**
1. Fire `oci_instance_delete` CR → OCI SDK verify TERMINATED
2. Fire `oci_subnet_create` rollback → verify subnet deleted
3. Fire `oci_vcn_create` rollback → verify VCN deleted
4. Clean inventory: remove OCI server + application + cloud_account assets for this run

### Credential Sourcing
Reads `oci` connector credentials from DB via `SecretsService` (same pattern as AWS track). Uses them for both Nexplane CRs and OCI SDK verification calls.

### `--phases` help string
Adds `OCI_A,OCI_B,OCI_C,OCI_D,OCI_E` to the phases list in `test_multicloud_live.py`.

---

## What's Not In This Sub-project

Intentionally deferred to later sub-projects:
- **Sub-project 2:** Object Storage buckets, Block Volumes (non-boot)
- **Sub-project 3:** VCN security lists (beyond initial defaults), NSGs, OCI LBaaS, DNS zones
- **Sub-project 4:** IAM users/groups/policies, Vault secrets, compartment management CRs
- **Sub-project 5:** Autonomous Database, MySQL HeatWave, OCI Monitoring alarms, Logging
- **Sub-project 6:** Full smoke test suite matching AWS/GCP/Azure coverage depth
