# Azure Remaining Phases (U, X, Y, Z) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Azure smoke test phases U (VNet), X (DNS), Y (SQL Database), and Z (Monitor alerts) by adding executors, change types, migrations, and replacing stub functions with real implementations.

**Architecture:** Four independent sub-projects each adding 2-4 executors in `backend/app/connectors/executors/azure/`, matching CT definition JSONs, ChangeType enum values, an Alembic migration, and a live smoke test phase in `test_azure_live.py`. Follows the exact pattern of the recently completed Phases V and W. `_client.py` gets one new factory per sub-project (X/Y/Z). `smoke_helpers.py` gets four new `_get_azure_*_client()` helpers. The stubs `run_phase_u_stub`, `run_phase_x_stub`, `run_phase_y_stub`, `run_phase_z_stub` are replaced in-place.

**Tech Stack:** Python 3.12, azure-mgmt-network (existing), azure-mgmt-dns, azure-mgmt-sql, azure-mgmt-monitor, FastAPI, Alembic

---

## Files

**Sub-project U — Create:**
- `backend/app/connectors/executors/azure/create_vnet.py`
- `backend/app/connectors/executors/azure/delete_vnet.py`
- `backend/app/connectors/change_type_definitions/azure_vnet_create.json`
- `backend/app/connectors/change_type_definitions/azure_vnet_delete.json`
- `backend/alembic/versions/034_add_azure_vnet_types.py`

**Sub-project U — Modify:**
- `backend/app/models/change_request.py` — 2 new enum values
- `backend/tests/smoke/smoke_helpers.py` — add `_get_azure_network_client()`
- `backend/tests/smoke/test_azure_live.py` — replace `run_phase_u_stub`, update main/docstring

**Sub-project X — Create:**
- `backend/app/connectors/executors/azure/create_dns_zone.py`
- `backend/app/connectors/executors/azure/delete_dns_zone.py`
- `backend/app/connectors/executors/azure/create_dns_record.py`
- `backend/app/connectors/executors/azure/delete_dns_record.py`
- `backend/app/connectors/change_type_definitions/azure_dns_zone_create.json`
- `backend/app/connectors/change_type_definitions/azure_dns_zone_delete.json`
- `backend/app/connectors/change_type_definitions/azure_dns_record_create.json`
- `backend/app/connectors/change_type_definitions/azure_dns_record_delete.json`
- `backend/alembic/versions/035_add_azure_dns_types.py`

**Sub-project X — Modify:**
- `backend/requirements.txt` — add azure-mgmt-dns
- `backend/app/connectors/executors/azure/_client.py` — add `get_dns_client`
- `backend/app/models/change_request.py` — 4 more enum values
- `backend/tests/smoke/smoke_helpers.py` — add `_get_azure_dns_client()`
- `backend/tests/smoke/test_azure_live.py` — replace `run_phase_x_stub`

**Sub-project Y — Create:**
- `backend/app/connectors/executors/azure/create_sql_server.py`
- `backend/app/connectors/executors/azure/delete_sql_server.py`
- `backend/app/connectors/executors/azure/create_sql_database.py`
- `backend/app/connectors/executors/azure/delete_sql_database.py`
- `backend/app/connectors/change_type_definitions/azure_sql_server_create.json`
- `backend/app/connectors/change_type_definitions/azure_sql_server_delete.json`
- `backend/app/connectors/change_type_definitions/azure_sql_database_create.json`
- `backend/app/connectors/change_type_definitions/azure_sql_database_delete.json`
- `backend/alembic/versions/036_add_azure_sql_types.py`

**Sub-project Y — Modify:**
- `backend/requirements.txt` — add azure-mgmt-sql
- `backend/app/connectors/executors/azure/_client.py` — add `get_sql_client`
- `backend/app/models/change_request.py` — 4 more enum values
- `backend/tests/smoke/smoke_helpers.py` — add `_get_azure_sql_client()`
- `backend/tests/smoke/test_azure_live.py` — replace `run_phase_y_stub`

**Sub-project Z — Create:**
- `backend/app/connectors/executors/azure/create_metric_alert.py`
- `backend/app/connectors/executors/azure/delete_metric_alert.py`
- `backend/app/connectors/change_type_definitions/azure_metric_alert_create.json`
- `backend/app/connectors/change_type_definitions/azure_metric_alert_delete.json`
- `backend/alembic/versions/037_add_azure_monitor_types.py`

**Sub-project Z — Modify:**
- `backend/requirements.txt` — add azure-mgmt-monitor
- `backend/app/connectors/executors/azure/_client.py` — add `get_monitor_client`
- `backend/app/models/change_request.py` — 2 more enum values
- `backend/tests/smoke/smoke_helpers.py` — add `_get_azure_monitor_client()`
- `backend/tests/smoke/test_azure_live.py` — replace `run_phase_z_stub`, update main/docstring for all 4

---

## Sub-project U: VNet Lifecycle

---

### Task 1: VNet executors + change types + migration 034

**Files:**
- Create: `backend/app/connectors/executors/azure/create_vnet.py`
- Create: `backend/app/connectors/executors/azure/delete_vnet.py`
- Create: `backend/app/connectors/change_type_definitions/azure_vnet_create.json`
- Create: `backend/app/connectors/change_type_definitions/azure_vnet_delete.json`
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/034_add_azure_vnet_types.py`

- [ ] **Step 1: Create `create_vnet.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vnet_name = parameters.get("vnet_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    address_prefix = parameters.get("address_prefix", "10.100.0.0/16")
    subnet_prefix = parameters.get("subnet_prefix", "10.100.0.0/24")

    if not creds:
        return {
            "action": "create_vnet",
            "vnet_name": vnet_name,
            "resource_group": rg,
            "location": location,
            "mock": True,
        }

    from ._client import get_network_client
    from azure.mgmt.network.models import VirtualNetwork, AddressSpace, Subnet
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: network.virtual_networks.begin_create_or_update(
            rg, vnet_name,
            VirtualNetwork(
                location=location,
                address_space=AddressSpace(address_prefixes=[address_prefix]),
                subnets=[Subnet(name="default", address_prefix=subnet_prefix)],
            ),
        ).result(),
    )
    return {
        "action": "create_vnet",
        "vnet_name": vnet_name,
        "resource_group": rg,
        "location": location,
        "address_prefix": address_prefix,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_vnet import execute as delete
    return await delete(
        {
            "vnet_name": execution_result.get("vnet_name", parameters.get("vnet_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 2: Create `delete_vnet.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vnet_name = parameters.get("vnet_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_vnet", "vnet_name": vnet_name, "resource_group": rg, "mock": True}

    from ._client import get_network_client
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: network.virtual_networks.begin_delete(rg, vnet_name).result())
    return {
        "action": "delete_vnet",
        "vnet_name": vnet_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "VNet deletion cannot be reversed automatically"}
```

- [ ] **Step 3: Create CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/azure_vnet_create.json`:
```json
{
  "change_type": "azure_vnet_create",
  "display_name": "Create Azure Virtual Network",
  "steps": [{"generic_action": "create_vnet", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_vnet",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_vnet_delete.json`:
```json
{
  "change_type": "azure_vnet_delete",
  "display_name": "Delete Azure Virtual Network",
  "steps": [{"generic_action": "delete_vnet", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 4: Add 2 enum values to `change_request.py`**

Read the file. Find `azure_role_assignment_delete = "azure_role_assignment_delete"` and add after:
```python
    azure_vnet_create = "azure_vnet_create"
    azure_vnet_delete = "azure_vnet_delete"
```

- [ ] **Step 5: Create migration 034**

Create `backend/alembic/versions/034_add_azure_vnet_types.py`:
```python
"""add Azure VNet change types

Revision ID: 034
Revises: 033
Create Date: 2026-05-06
"""
from alembic import op

revision = '034'
down_revision = '033'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_vnet_create', 'azure_vnet_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 6: Run migration and tests**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -3
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: `Running upgrade 033 -> 034` and all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/azure/create_vnet.py \
        backend/app/connectors/executors/azure/delete_vnet.py \
        backend/app/connectors/change_type_definitions/azure_vnet_create.json \
        backend/app/connectors/change_type_definitions/azure_vnet_delete.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/034_add_azure_vnet_types.py
git commit -m "feat(azure): add VNet create/delete executors, change types, migration 034"
```

---

### Task 2: Phase U smoke test

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `_get_azure_network_client()` to `smoke_helpers.py`**

Read `backend/tests/smoke/smoke_helpers.py`. After `_get_azure_storage_client()` (or the last `_get_azure_*` helper), add:

```python
def _get_azure_network_client():
    """Return an Azure NetworkManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.network import NetworkManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return NetworkManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 2: Replace `run_phase_u_stub` with `run_phase_u` in `test_azure_live.py`**

Read the file. Update the import line to include `_get_azure_network_client`. Replace the entire `run_phase_u_stub` function with:

```python
def run_phase_u(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase U: Azure VNet lifecycle — create VNet + subnet, verify, delete via rollback."""
    print("\n[Phase U] Azure VNet Lifecycle")

    if not azure_resource_group:
        fail("Phase U requires --azure-resource-group")

    import secrets as _secrets
    vnet_name = f"nexplane-smoke-vnet-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []
    network = _get_azure_network_client()

    try:
        cr = client.run_cr(
            "[Phase U] create VNet", "azure_vnet_create", cloud_account_id,
            {"vnet_name": vnet_name, "resource_group": azure_resource_group,
             "location": "eastus", "address_prefix": "10.100.0.0/16",
             "subnet_prefix": "10.100.0.0/24"},
        )
        rollback_stack.append((cr["id"], "azure_vnet_create"))

        if network:
            vnet = network.virtual_networks.get(azure_resource_group, vnet_name)
            assert vnet.name == vnet_name, f"VNet name mismatch: {vnet.name}"
            assert len(vnet.subnets) > 0, "VNet has no subnets"
            log(f"VNet verified: {vnet_name} ({vnet.address_space.address_prefixes[0]})")
        else:
            log("VNet created (SDK verification skipped — no credentials)")

        log("Phase U complete")

    except Exception as e:
        print(f"\n❌ Phase U failed: {e}")
        raise
    finally:
        print("  [Phase U cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if network:
            try:
                network.virtual_networks.begin_delete(azure_resource_group, vnet_name).result()
                print(f"  Safety net: deleted VNet {vnet_name}")
            except Exception:
                pass
```

- [ ] **Step 3: Update `main()` to call `run_phase_u` instead of `run_phase_u_stub`**

Find: `run_phase_u_stub(client, cloud_account_id, args.azure_resource_group)`
Replace with: `run_phase_u(client, cloud_account_id, args.azure_resource_group)`

- [ ] **Step 4: Run tests and verify parse**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
docker exec nexplane-backend-1 python -c "import sys; sys.path.insert(0, '/app/tests/smoke'); import test_azure_live; print('Phase U:', test_azure_live.run_phase_u); print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py backend/tests/smoke/test_azure_live.py
git commit -m "feat(azure): implement Phase U — VNet lifecycle smoke test"
```

---

## Sub-project X: DNS Lifecycle

---

### Task 3: DNS executors + change types + migration 035

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/connectors/executors/azure/_client.py`
- Create: 4 executor files
- Create: 4 CT definition JSONs
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/035_add_azure_dns_types.py`

- [ ] **Step 1: Add `azure-mgmt-dns` to requirements and install**

In `backend/requirements.txt`, after `azure-mgmt-authorization>=4.0.0`, add:
```
azure-mgmt-dns>=8.0.0
```

```bash
docker exec nexplane-backend-1 pip install "azure-mgmt-dns>=8.0.0" 2>&1 | tail -3
```

- [ ] **Step 2: Add `get_dns_client` to `_client.py`**

In `backend/app/connectors/executors/azure/_client.py`, after `get_monitor_client` (or at the end), add:
```python
def get_dns_client(creds: dict):
    from azure.mgmt.dns import DnsManagementClient
    return DnsManagementClient(get_credential(creds), creds['subscription_id'])
```

- [ ] **Step 3: Create `create_dns_zone.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters.get("zone_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "create_dns_zone", "zone_name": zone_name, "resource_group": rg, "mock": True}

    from ._client import get_dns_client
    from azure.mgmt.dns.models import Zone
    dns = get_dns_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: dns.zones.create_or_update(rg, zone_name, Zone(location="global")),
    )
    return {
        "action": "create_dns_zone",
        "zone_name": zone_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_dns_zone import execute as delete
    return await delete(
        {
            "zone_name": execution_result.get("zone_name", parameters.get("zone_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 4: Create `delete_dns_zone.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters.get("zone_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_dns_zone", "zone_name": zone_name, "resource_group": rg, "mock": True}

    from ._client import get_dns_client
    dns = get_dns_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: dns.zones.begin_delete(rg, zone_name).result())
    return {
        "action": "delete_dns_zone",
        "zone_name": zone_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS zone deletion cannot be reversed automatically"}
```

- [ ] **Step 5: Create `create_dns_record.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters.get("zone_name", "")
    record_name = parameters.get("record_name", "smoke")
    ip_address = parameters.get("ip_address", "1.2.3.4")
    ttl = parameters.get("ttl", 300)
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "create_dns_record",
            "zone_name": zone_name,
            "record_name": record_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_dns_client
    from azure.mgmt.dns.models import RecordSet, ARecord
    dns = get_dns_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: dns.record_sets.create_or_update(
            rg, zone_name, record_name, "A",
            RecordSet(ttl=ttl, a_records=[ARecord(ipv4_address=ip_address)]),
        ),
    )
    return {
        "action": "create_dns_record",
        "zone_name": zone_name,
        "record_name": record_name,
        "ip_address": ip_address,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_dns_record import execute as delete
    return await delete(
        {
            "zone_name": execution_result.get("zone_name", parameters.get("zone_name")),
            "record_name": execution_result.get("record_name", parameters.get("record_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 6: Create `delete_dns_record.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters.get("zone_name", "")
    record_name = parameters.get("record_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "delete_dns_record",
            "zone_name": zone_name,
            "record_name": record_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_dns_client
    dns = get_dns_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: dns.record_sets.delete(rg, zone_name, record_name, "A"))
    return {
        "action": "delete_dns_record",
        "zone_name": zone_name,
        "record_name": record_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS record deletion cannot be reversed automatically"}
```

- [ ] **Step 7: Create 4 CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/azure_dns_zone_create.json`:
```json
{
  "change_type": "azure_dns_zone_create",
  "display_name": "Create Azure DNS Zone",
  "steps": [{"generic_action": "create_dns_zone", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_dns_zone",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_dns_zone_delete.json`:
```json
{
  "change_type": "azure_dns_zone_delete",
  "display_name": "Delete Azure DNS Zone",
  "steps": [{"generic_action": "delete_dns_zone", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Create `backend/app/connectors/change_type_definitions/azure_dns_record_create.json`:
```json
{
  "change_type": "azure_dns_record_create",
  "display_name": "Create Azure DNS Record",
  "steps": [{"generic_action": "create_dns_record", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_dns_record",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_dns_record_delete.json`:
```json
{
  "change_type": "azure_dns_record_delete",
  "display_name": "Delete Azure DNS Record",
  "steps": [{"generic_action": "delete_dns_record", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 8: Add 4 enum values to `change_request.py`**

After `azure_vnet_delete = "azure_vnet_delete"`, add:
```python
    azure_dns_zone_create = "azure_dns_zone_create"
    azure_dns_zone_delete = "azure_dns_zone_delete"
    azure_dns_record_create = "azure_dns_record_create"
    azure_dns_record_delete = "azure_dns_record_delete"
```

- [ ] **Step 9: Create migration 035**

```python
"""add Azure DNS change types

Revision ID: 035
Revises: 034
Create Date: 2026-05-06
"""
from alembic import op

revision = '035'
down_revision = '034'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_dns_zone_create', 'azure_dns_zone_delete',
              'azure_dns_record_create', 'azure_dns_record_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 10: Run migration and tests**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -3
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: `Running upgrade 034 -> 035` and all tests pass.

- [ ] **Step 11: Commit**

```bash
git add backend/requirements.txt \
        backend/app/connectors/executors/azure/_client.py \
        backend/app/connectors/executors/azure/create_dns_zone.py \
        backend/app/connectors/executors/azure/delete_dns_zone.py \
        backend/app/connectors/executors/azure/create_dns_record.py \
        backend/app/connectors/executors/azure/delete_dns_record.py \
        backend/app/connectors/change_type_definitions/azure_dns_zone_create.json \
        backend/app/connectors/change_type_definitions/azure_dns_zone_delete.json \
        backend/app/connectors/change_type_definitions/azure_dns_record_create.json \
        backend/app/connectors/change_type_definitions/azure_dns_record_delete.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/035_add_azure_dns_types.py
git commit -m "feat(azure): add DNS zone/record executors, change types, migration 035"
```

---

### Task 4: Phase X smoke test

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `_get_azure_dns_client()` to `smoke_helpers.py`**

After `_get_azure_network_client()`, add:

```python
def _get_azure_dns_client():
    """Return an Azure DnsManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.dns import DnsManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return DnsManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 2: Replace `run_phase_x_stub` with `run_phase_x` in `test_azure_live.py`**

Update the import to include `_get_azure_dns_client`. Replace `run_phase_x_stub` with:

```python
def run_phase_x(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase X: Azure DNS — create zone + A record, verify, explicit record delete, rollback zone."""
    print("\n[Phase X] Azure DNS Lifecycle")

    if not azure_resource_group:
        fail("Phase X requires --azure-resource-group")

    import secrets as _secrets
    zone_name = f"nexplane-smoke-{_secrets.token_hex(4)}.example.com"
    record_name = "smoke"
    rollback_stack: list[tuple[str, str]] = []
    dns = _get_azure_dns_client()

    try:
        # 1. Create DNS zone
        cr = client.run_cr(
            "[Phase X] create DNS zone", "azure_dns_zone_create", cloud_account_id,
            {"zone_name": zone_name, "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_dns_zone_create"))

        if dns:
            zone = dns.zones.get(azure_resource_group, zone_name)
            assert zone.name == zone_name, f"Zone name mismatch: {zone.name}"
            log(f"DNS zone verified: {zone_name}")
        else:
            log("DNS zone created (SDK verification skipped — no credentials)")

        # 2. Create A record
        cr = client.run_cr(
            "[Phase X] create A record", "azure_dns_record_create", cloud_account_id,
            {"zone_name": zone_name, "record_name": record_name,
             "ip_address": "10.0.0.1", "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_dns_record_create"))

        if dns:
            rs = dns.record_sets.get(azure_resource_group, zone_name, record_name, "A")
            assert rs.a_records[0].ipv4_address == "10.0.0.1", \
                f"IP mismatch: {rs.a_records[0].ipv4_address}"
            log(f"A record verified: {record_name}.{zone_name} -> 10.0.0.1")

        # 3. Explicit record delete
        client.run_cr(
            "[Phase X] delete A record", "azure_dns_record_delete", cloud_account_id,
            {"zone_name": zone_name, "record_name": record_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.pop()  # record already deleted

        if dns:
            records = list(dns.record_sets.list_by_dns_zone(azure_resource_group, zone_name))
            assert not any(r.name == record_name for r in records), \
                f"Record {record_name} still exists after delete"
            log("A record deleted and verified gone")

        log("Phase X complete")

    except Exception as e:
        print(f"\n❌ Phase X failed: {e}")
        raise
    finally:
        print("  [Phase X cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if dns:
            try:
                dns.zones.begin_delete(azure_resource_group, zone_name).result()
                print(f"  Safety net: deleted DNS zone {zone_name}")
            except Exception:
                pass
```

- [ ] **Step 3: Update `main()` — replace `run_phase_x_stub` call**

Find: `run_phase_x_stub(client, cloud_account_id, args.azure_resource_group)`
Replace: `run_phase_x(client, cloud_account_id, args.azure_resource_group)`

- [ ] **Step 4: Run tests and verify parse**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
docker exec nexplane-backend-1 python -c "import sys; sys.path.insert(0, '/app/tests/smoke'); import test_azure_live; print('Phase X:', test_azure_live.run_phase_x); print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py backend/tests/smoke/test_azure_live.py
git commit -m "feat(azure): implement Phase X — DNS zone + record lifecycle smoke test"
```

---

## Sub-project Y: SQL Database Lifecycle

---

### Task 5: SQL executors + change types + migration 036

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/connectors/executors/azure/_client.py`
- Create: 4 executor files
- Create: 4 CT definition JSONs
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/036_add_azure_sql_types.py`

- [ ] **Step 1: Add `azure-mgmt-sql` to requirements and install**

After `azure-mgmt-dns>=8.0.0`, add:
```
azure-mgmt-sql>=4.0.0
```

```bash
docker exec nexplane-backend-1 pip install "azure-mgmt-sql>=4.0.0" 2>&1 | tail -3
```

- [ ] **Step 2: Add `get_sql_client` to `_client.py`**

```python
def get_sql_client(creds: dict):
    from azure.mgmt.sql import SqlManagementClient
    return SqlManagementClient(get_credential(creds), creds['subscription_id'])
```

- [ ] **Step 3: Create `create_sql_server.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    admin_login = parameters.get("admin_login", "nexplaneadmin")
    admin_password = parameters.get("admin_password", "")

    if not creds:
        return {
            "action": "create_sql_server",
            "server_name": server_name,
            "resource_group": rg,
            "location": location,
            "mock": True,
        }

    from ._client import get_sql_client
    from azure.mgmt.sql.models import Server
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: sql.servers.begin_create_or_update(
            rg, server_name,
            Server(
                location=location,
                administrator_login=admin_login,
                administrator_login_password=admin_password,
            ),
        ).result(),
    )
    return {
        "action": "create_sql_server",
        "server_name": server_name,
        "resource_group": rg,
        "location": location,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_sql_server import execute as delete
    return await delete(
        {
            "server_name": execution_result.get("server_name", parameters.get("server_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 4: Create `delete_sql_server.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_sql_server", "server_name": server_name, "resource_group": rg, "mock": True}

    from ._client import get_sql_client
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: sql.servers.begin_delete(rg, server_name).result())
    return {
        "action": "delete_sql_server",
        "server_name": server_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "SQL server deletion cannot be reversed automatically"}
```

- [ ] **Step 5: Create `create_sql_database.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    db_name = parameters.get("database_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    sku_name = parameters.get("sku_name", "Basic")

    if not creds:
        return {
            "action": "create_sql_database",
            "server_name": server_name,
            "database_name": db_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_sql_client
    from azure.mgmt.sql.models import Database, Sku
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: sql.databases.begin_create_or_update(
            rg, server_name, db_name,
            Database(location=location, sku=Sku(name=sku_name)),
        ).result(),
    )
    return {
        "action": "create_sql_database",
        "server_name": server_name,
        "database_name": db_name,
        "resource_group": rg,
        "location": location,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_sql_database import execute as delete
    return await delete(
        {
            "server_name": execution_result.get("server_name", parameters.get("server_name")),
            "database_name": execution_result.get("database_name", parameters.get("database_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 6: Create `delete_sql_database.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    server_name = parameters.get("server_name", "")
    db_name = parameters.get("database_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "delete_sql_database",
            "server_name": server_name,
            "database_name": db_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_sql_client
    sql = get_sql_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: sql.databases.begin_delete(rg, server_name, db_name).result())
    return {
        "action": "delete_sql_database",
        "server_name": server_name,
        "database_name": db_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "SQL database deletion cannot be reversed automatically"}
```

- [ ] **Step 7: Create 4 CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/azure_sql_server_create.json`:
```json
{
  "change_type": "azure_sql_server_create",
  "display_name": "Create Azure SQL Server",
  "steps": [{"generic_action": "create_sql_server", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_sql_server",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_sql_server_delete.json`:
```json
{
  "change_type": "azure_sql_server_delete",
  "display_name": "Delete Azure SQL Server",
  "steps": [{"generic_action": "delete_sql_server", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Create `backend/app/connectors/change_type_definitions/azure_sql_database_create.json`:
```json
{
  "change_type": "azure_sql_database_create",
  "display_name": "Create Azure SQL Database",
  "steps": [{"generic_action": "create_sql_database", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_sql_database",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_sql_database_delete.json`:
```json
{
  "change_type": "azure_sql_database_delete",
  "display_name": "Delete Azure SQL Database",
  "steps": [{"generic_action": "delete_sql_database", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 8: Add 4 enum values to `change_request.py`**

After `azure_dns_record_delete = "azure_dns_record_delete"`, add:
```python
    azure_sql_server_create = "azure_sql_server_create"
    azure_sql_server_delete = "azure_sql_server_delete"
    azure_sql_database_create = "azure_sql_database_create"
    azure_sql_database_delete = "azure_sql_database_delete"
```

- [ ] **Step 9: Create migration 036**

```python
"""add Azure SQL change types

Revision ID: 036
Revises: 035
Create Date: 2026-05-06
"""
from alembic import op

revision = '036'
down_revision = '035'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_sql_server_create', 'azure_sql_server_delete',
              'azure_sql_database_create', 'azure_sql_database_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 10: Run migration and tests**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -3
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: `Running upgrade 035 -> 036` and all tests pass.

- [ ] **Step 11: Commit**

```bash
git add backend/requirements.txt \
        backend/app/connectors/executors/azure/_client.py \
        backend/app/connectors/executors/azure/create_sql_server.py \
        backend/app/connectors/executors/azure/delete_sql_server.py \
        backend/app/connectors/executors/azure/create_sql_database.py \
        backend/app/connectors/executors/azure/delete_sql_database.py \
        backend/app/connectors/change_type_definitions/azure_sql_server_create.json \
        backend/app/connectors/change_type_definitions/azure_sql_server_delete.json \
        backend/app/connectors/change_type_definitions/azure_sql_database_create.json \
        backend/app/connectors/change_type_definitions/azure_sql_database_delete.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/036_add_azure_sql_types.py
git commit -m "feat(azure): add SQL server/database executors, change types, migration 036"
```

---

### Task 6: Phase Y smoke test

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `_get_azure_sql_client()` to `smoke_helpers.py`**

After `_get_azure_dns_client()`, add:

```python
def _get_azure_sql_client():
    """Return an Azure SqlManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.sql import SqlManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return SqlManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 2: Replace `run_phase_y_stub` with `run_phase_y` in `test_azure_live.py`**

Update the import to include `_get_azure_sql_client`. Replace `run_phase_y_stub` with:

```python
def run_phase_y(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase Y: Azure SQL Database lifecycle — create server + database, verify, delete, rollback server. (~10 min)"""
    print("\n[Phase Y] Azure SQL Database Lifecycle (~10 min)")

    if not azure_resource_group:
        fail("Phase Y requires --azure-resource-group")

    import secrets as _secrets
    server_name = f"nexplane-smoke-sql-{_secrets.token_hex(4)}"
    db_name = "nexplane-smoke-db"
    # Azure password complexity: uppercase + lowercase + digit + special, min 8 chars
    admin_password = f"NxP!{_secrets.token_hex(8)}"
    rollback_stack: list[tuple[str, str]] = []
    sql = _get_azure_sql_client()

    try:
        # 1. Create SQL server (~5 min)
        cr = client._run_cr_with_timeout(
            "[Phase Y] create SQL server", "azure_sql_server_create", cloud_account_id,
            {"server_name": server_name, "resource_group": azure_resource_group,
             "location": "eastus", "admin_login": "nexplaneadmin",
             "admin_password": admin_password},
            timeout=600,
        )
        rollback_stack.append((cr["id"], "azure_sql_server_create"))

        if sql:
            server = sql.servers.get(azure_resource_group, server_name)
            assert server.name == server_name, f"Server name mismatch: {server.name}"
            log(f"SQL server verified: {server_name}")
        else:
            log("SQL server created (SDK verification skipped — no credentials)")

        # 2. Create SQL database (~2 min)
        cr = client._run_cr_with_timeout(
            "[Phase Y] create SQL database", "azure_sql_database_create", cloud_account_id,
            {"server_name": server_name, "database_name": db_name,
             "resource_group": azure_resource_group, "location": "eastus",
             "sku_name": "Basic"},
            timeout=300,
        )
        rollback_stack.append((cr["id"], "azure_sql_database_create"))

        if sql:
            db = sql.databases.get(azure_resource_group, server_name, db_name)
            assert db.name == db_name, f"Database name mismatch: {db.name}"
            log(f"SQL database verified: {db_name}")

        # 3. Explicit database delete
        client._run_cr_with_timeout(
            "[Phase Y] delete SQL database", "azure_sql_database_delete", cloud_account_id,
            {"server_name": server_name, "database_name": db_name,
             "resource_group": azure_resource_group},
            timeout=120,
        )
        rollback_stack.pop()  # database already deleted
        log("SQL database deleted")

        log("Phase Y complete")

    except Exception as e:
        print(f"\n❌ Phase Y failed: {e}")
        raise
    finally:
        print("  [Phase Y cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if sql:
            try:
                sql.servers.begin_delete(azure_resource_group, server_name).result()
                print(f"  Safety net: deleted SQL server {server_name}")
            except Exception:
                pass
```

- [ ] **Step 3: Update `main()` — replace `run_phase_y_stub` call**

Find: `run_phase_y_stub(client, cloud_account_id, args.azure_resource_group)`
Replace: `run_phase_y(client, cloud_account_id, args.azure_resource_group)`

- [ ] **Step 4: Run tests and verify parse**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
docker exec nexplane-backend-1 python -c "import sys; sys.path.insert(0, '/app/tests/smoke'); import test_azure_live; print('Phase Y:', test_azure_live.run_phase_y); print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py backend/tests/smoke/test_azure_live.py
git commit -m "feat(azure): implement Phase Y — SQL server + database lifecycle smoke test"
```

---

## Sub-project Z: Monitor Metric Alert

---

### Task 7: Monitor executors + change types + migration 037

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/connectors/executors/azure/_client.py`
- Create: 2 executor files
- Create: 2 CT definition JSONs
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/037_add_azure_monitor_types.py`

- [ ] **Step 1: Add `azure-mgmt-monitor` to requirements and install**

After `azure-mgmt-sql>=4.0.0`, add:
```
azure-mgmt-monitor>=6.0.0
```

```bash
docker exec nexplane-backend-1 pip install "azure-mgmt-monitor>=6.0.0" 2>&1 | tail -3
```

- [ ] **Step 2: Add `get_monitor_client` to `_client.py`**

```python
def get_monitor_client(creds: dict):
    from azure.mgmt.monitor import MonitorManagementClient
    return MonitorManagementClient(get_credential(creds), creds['subscription_id'])
```

- [ ] **Step 3: Create `create_metric_alert.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    alert_name = parameters.get("alert_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    target_resource_id = parameters.get("target_resource_id", "")
    metric_name = parameters.get("metric_name", "Percentage CPU")
    threshold = parameters.get("threshold", 90)
    location = parameters.get("location", "global")

    if not creds:
        return {
            "action": "create_metric_alert",
            "alert_name": alert_name,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_monitor_client
    from azure.mgmt.monitor.models import (
        MetricAlertResource,
        MetricAlertSingleResourceMultipleMetricCriteria,
        MetricCriteria,
    )
    monitor = get_monitor_client(creds)
    loop = asyncio.get_running_loop()
    criteria = MetricAlertSingleResourceMultipleMetricCriteria(
        all_of=[
            MetricCriteria(
                name="HighCPU",
                metric_name=metric_name,
                metric_namespace="Microsoft.Compute/virtualMachines",
                operator="GreaterThan",
                threshold=threshold,
                time_aggregation="Average",
                criterion_type="StaticThresholdCriterion",
            )
        ]
    )
    await loop.run_in_executor(
        None,
        lambda: monitor.metric_alerts.create_or_update(
            rg, alert_name,
            MetricAlertResource(
                location=location,
                description="Nexplane smoke test alert",
                severity=3,
                enabled=True,
                scopes=[target_resource_id],
                evaluation_frequency="PT1M",
                window_size="PT5M",
                criteria=criteria,
            ),
        ),
    )
    return {
        "action": "create_metric_alert",
        "alert_name": alert_name,
        "resource_group": rg,
        "target_resource_id": target_resource_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_metric_alert import execute as delete
    return await delete(
        {
            "alert_name": execution_result.get("alert_name", parameters.get("alert_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 4: Create `delete_metric_alert.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    alert_name = parameters.get("alert_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_metric_alert", "alert_name": alert_name, "resource_group": rg, "mock": True}

    from ._client import get_monitor_client
    monitor = get_monitor_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: monitor.metric_alerts.delete(rg, alert_name))
    return {
        "action": "delete_metric_alert",
        "alert_name": alert_name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "metric alert deletion cannot be reversed automatically"}
```

- [ ] **Step 5: Create 2 CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/azure_metric_alert_create.json`:
```json
{
  "change_type": "azure_metric_alert_create",
  "display_name": "Create Azure Metric Alert",
  "steps": [{"generic_action": "create_metric_alert", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_metric_alert",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_metric_alert_delete.json`:
```json
{
  "change_type": "azure_metric_alert_delete",
  "display_name": "Delete Azure Metric Alert",
  "steps": [{"generic_action": "delete_metric_alert", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 6: Add 2 enum values to `change_request.py`**

After `azure_sql_database_delete = "azure_sql_database_delete"`, add:
```python
    azure_metric_alert_create = "azure_metric_alert_create"
    azure_metric_alert_delete = "azure_metric_alert_delete"
```

- [ ] **Step 7: Create migration 037**

```python
"""add Azure Monitor change types

Revision ID: 037
Revises: 036
Create Date: 2026-05-06
"""
from alembic import op

revision = '037'
down_revision = '036'
branch_labels = None
depends_on = None


def upgrade():
    for t in ['azure_metric_alert_create', 'azure_metric_alert_delete']:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 8: Run migration and tests**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -3
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: `Running upgrade 036 -> 037` and all tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/requirements.txt \
        backend/app/connectors/executors/azure/_client.py \
        backend/app/connectors/executors/azure/create_metric_alert.py \
        backend/app/connectors/executors/azure/delete_metric_alert.py \
        backend/app/connectors/change_type_definitions/azure_metric_alert_create.json \
        backend/app/connectors/change_type_definitions/azure_metric_alert_delete.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/037_add_azure_monitor_types.py
git commit -m "feat(azure): add metric alert executors, change types, migration 037"
```

---

### Task 8: Phase Z smoke test + final docstring/help update

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `_get_azure_monitor_client()` to `smoke_helpers.py`**

After `_get_azure_sql_client()`, add:

```python
def _get_azure_monitor_client():
    """Return an Azure MonitorManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.monitor import MonitorManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return MonitorManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 2: Replace `run_phase_z_stub` with `run_phase_z` in `test_azure_live.py`**

Update the import to include `_get_azure_monitor_client`. Replace `run_phase_z_stub` with:

```python
def run_phase_z(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str,
                azure_phase_result: Optional[dict] = None) -> None:
    """Phase Z: Azure Monitor — create metric alert on Phase N VM, verify, delete via rollback."""
    print("\n[Phase Z] Azure Monitor Metric Alert")

    if not azure_phase_result or not azure_phase_result.get("vm_asset"):
        print("  ⚠️  Phase Z requires Phase N VM — skipping (run with N,Z to enable)")
        return

    if not azure_resource_group:
        fail("Phase Z requires --azure-resource-group")

    import secrets as _secrets
    alert_name = f"nexplane-smoke-alert-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    vm_asset = azure_phase_result["vm_asset"]
    vm_name = vm_asset.get("asset_metadata", {}).get("vm_name", "")
    creds = _get_azure_creds()
    subscription_id = creds.get("subscription_id", "")
    target_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{azure_resource_group}"
        f"/providers/Microsoft.Compute/virtualMachines/{vm_name}"
    )

    monitor = _get_azure_monitor_client()

    try:
        cr = client.run_cr(
            "[Phase Z] create metric alert", "azure_metric_alert_create", cloud_account_id,
            {"alert_name": alert_name, "resource_group": azure_resource_group,
             "target_resource_id": target_resource_id,
             "metric_name": "Percentage CPU", "threshold": 90},
        )
        rollback_stack.append((cr["id"], "azure_metric_alert_create"))

        if monitor:
            alert = monitor.metric_alerts.get(azure_resource_group, alert_name)
            assert alert.name == alert_name, f"Alert name mismatch: {alert.name}"
            log(f"Metric alert verified: {alert_name}")
        else:
            log("Metric alert created (SDK verification skipped — no credentials)")

        log("Phase Z complete")

    except Exception as e:
        print(f"\n❌ Phase Z failed: {e}")
        raise
    finally:
        print("  [Phase Z cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if monitor:
            try:
                monitor.metric_alerts.delete(azure_resource_group, alert_name)
                print(f"  Safety net: deleted metric alert {alert_name}")
            except Exception:
                pass
```

- [ ] **Step 3: Update `main()` — replace `run_phase_z_stub` and update help text**

Find: `run_phase_z_stub(client, cloud_account_id, args.azure_resource_group)`
Replace with: `run_phase_z(client, cloud_account_id, args.azure_resource_group, azure_phase_result)`

Update the `--phases` help string to:
```python
        help=(
            "Comma-separated phases to run. "
            "N-O: VM lifecycle. P=NSG, Q=Storage, R=Tagging, S=Terraform, T=Ansible. "
            "U=VNet, V=Storage-CRUD, W=IAM-RBAC, X=DNS, Y=SQL, Z=Monitor. Default: N,O."
        ),
```

- [ ] **Step 4: Update module docstring**

Find the docstring section listing phases. Add/update to include U, X, Y, Z:
```
    U  Azure VNet lifecycle: create VNet + subnet, verify, delete via rollback
    X  Azure DNS: create zone + A record, verify, explicit record delete, rollback zone
    Y  Azure SQL Database: create server + database, verify, delete, rollback server (~10 min)
    Z  Azure Monitor: create metric alert on Phase N VM, verify, delete via rollback
```

- [ ] **Step 5: Run tests and verify all four phases parse**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
docker exec nexplane-backend-1 python -c "
import sys
sys.path.insert(0, '/app/tests/smoke')
import test_azure_live
print('U:', test_azure_live.run_phase_u)
print('X:', test_azure_live.run_phase_x)
print('Y:', test_azure_live.run_phase_y)
print('Z:', test_azure_live.run_phase_z)
print('OK')
"
```

Expected: all four function references printed, then OK.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py backend/tests/smoke/test_azure_live.py
git commit -m "feat(azure): implement Phase Z — metric alert smoke test; complete U/X/Y/Z docstring and help text"
```

---

## Self-review

**Spec coverage:**
- ✅ Phase U: `create_vnet.py` + `delete_vnet.py` + 2 CT defs + migration 034 — Task 1
- ✅ Phase U smoke test: VNet create → SDK verify → rollback — Task 2
- ✅ `_get_azure_network_client()` in smoke_helpers — Task 2
- ✅ Phase X: 4 DNS executors + 4 CT defs + migration 035 + `get_dns_client` in _client.py — Task 3
- ✅ Phase X smoke test: zone + record create → verify → explicit delete → rollback — Task 4
- ✅ `_get_azure_dns_client()` in smoke_helpers — Task 4
- ✅ Phase Y: 4 SQL executors + 4 CT defs + migration 036 + `get_sql_client` in _client.py — Task 5
- ✅ Phase Y smoke test: server + database create → verify → explicit delete → rollback — Task 6
- ✅ `_get_azure_sql_client()` in smoke_helpers — Task 6
- ✅ Phase Z: 2 monitor executors + 2 CT defs + migration 037 + `get_monitor_client` in _client.py — Task 7
- ✅ Phase Z smoke test: metric alert on Phase N VM → verify → rollback — Task 8
- ✅ `_get_azure_monitor_client()` in smoke_helpers — Task 8
- ✅ All stubs replaced in main() — Tasks 2, 4, 6, 8
- ✅ Module docstring + --phases help text updated — Task 8
- ✅ New packages: azure-mgmt-dns, azure-mgmt-sql, azure-mgmt-monitor — Tasks 3, 5, 7

**Placeholder scan:** None found. All steps have complete code.

**Type consistency:**
- `server_name` flows from `create_sql_server` result → Phase Y smoke test → `create_sql_database` desired_outcome — consistent
- `alert_name` flows from `create_metric_alert` result → Phase Z rollback — consistent
- `rollback_stack: list[tuple[str, str]]` — consistent with all existing phases
- All executors use `asyncio.get_running_loop()` — consistent with existing pattern
