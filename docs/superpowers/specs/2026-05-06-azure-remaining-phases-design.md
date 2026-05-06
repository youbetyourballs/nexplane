# Azure Remaining Phases (U, X, Y, Z) Design

**Date:** 2026-05-06  
**Status:** Approved for implementation

---

## Goals

Implement the four remaining stub smoke test phases for Azure, each adding new executors, change types, and live smoke test coverage:

- **Phase U** — VNet lifecycle (create VNet + subnet, delete)
- **Phase X** — DNS lifecycle (create zone + A record, delete)
- **Phase Y** — SQL Database lifecycle (create server + database, delete)
- **Phase Z** — Monitor alerts (create metric alert, delete)

---

## Current State

- Most recent migration: `033_add_azure_iam_types.py` (down_revision='032')
- `_client.py` has: `get_network_client`, `get_compute_client`, `get_storage_client`, `get_msi_client`, `get_authorization_client`
- `azure-mgmt-network>=27.0.0` already in requirements — Phase U needs no new package
- New packages needed: `azure-mgmt-dns`, `azure-mgmt-sql`, `azure-mgmt-monitor`
- Stub functions `run_phase_u_stub`, `run_phase_x_stub`, `run_phase_y_stub`, `run_phase_z_stub` in `test_azure_live.py` to be replaced
- Phase Z depends on Phase N VM — must check `azure_phase_result` is not None before running

---

## Shared Pattern (all phases)

All executors:
- `async def execute(parameters, asset_ids, connector)` — mock path when `not creds`, real path via `asyncio.get_running_loop()` + `loop.run_in_executor(None, lambda: ...)`
- `async def rollback(parameters, execution_result, connector)` — create executors delegate to delete; delete executors return `{"rolled_back": False, "reason": "..."}`
- Import SDK client from `._client` inside the real path

All CT definitions:
- `steps: [{"generic_action": "<executor_name>", "purpose": "execute", "required": true}]`
- `preflight_checks: ["connector_reachable"]`, `verification_methods: ["api_check"]`
- Create variants: `rollback_action` + `rollback_connector_type: "azure"`
- Delete variants: no rollback fields

All migrations: `ALTER TYPE change_type ADD VALUE IF NOT EXISTS` loop.

---

## Phase U: VNet Lifecycle

### New `_client.py` additions

None needed — `get_network_client` already exists.

### New executors

**`create_vnet.py`**

Input: `vnet_name`, `resource_group`, `location` (default `"eastus"`), `address_prefix` (default `"10.100.0.0/16"`), `subnet_prefix` (default `"10.100.0.0/24"`).

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

**`delete_vnet.py`**

Input: `vnet_name`, `resource_group`.

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

### Change types

- `azure_vnet_create` → `create_vnet`, rollback_action: `delete_vnet`
- `azure_vnet_delete` → `delete_vnet`, no rollback

### Migration 034

```python
new_types = ['azure_vnet_create', 'azure_vnet_delete']
```

### Phase U smoke test

```python
def run_phase_u(client, cloud_account_id, azure_resource_group):
    """Phase U: Azure VNet lifecycle — create VNet + subnet, verify, delete via rollback."""
    import secrets as _secrets
    vnet_name = f"nexplane-smoke-vnet-{_secrets.token_hex(4)}"
    rollback_stack = []
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
            assert vnet.name == vnet_name
            assert len(vnet.subnets) > 0
            log(f"VNet verified: {vnet_name} ({vnet.address_space.address_prefixes[0]})")

        log("Phase U complete")
    except Exception as e:
        print(f"\n❌ Phase U failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if network:
            try:
                network.virtual_networks.begin_delete(azure_resource_group, vnet_name).result()
                print(f"  Safety net: deleted VNet {vnet_name}")
            except Exception:
                pass
```

New smoke_helpers helper: `_get_azure_network_client()` — same pattern as `_get_azure_storage_client()` but returns `NetworkManagementClient`.

---

## Phase X: DNS Lifecycle

### New `_client.py` addition

```python
def get_dns_client(creds: dict):
    from azure.mgmt.dns import DnsManagementClient
    return DnsManagementClient(get_credential(creds), creds['subscription_id'])
```

New package: `azure-mgmt-dns>=8.0.0`

### New executors

**`create_dns_zone.py`**

Input: `zone_name` (e.g. `"nexplane-smoke.example.com"`), `resource_group`.

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

**`delete_dns_zone.py`**

Input: `zone_name`, `resource_group`. Calls `dns.zones.begin_delete(rg, zone_name).result()`.

**`create_dns_record.py`**

Input: `zone_name`, `record_name` (relative, e.g. `"smoke"`), `ip_address` (default `"1.2.3.4"`), `ttl` (default `300`), `resource_group`.

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

**`delete_dns_record.py`**

Input: `zone_name`, `record_name`, `resource_group`. Calls `dns.record_sets.delete(rg, zone_name, record_name, "A")`.

### Change types

- `azure_dns_zone_create` → `create_dns_zone`, rollback: `delete_dns_zone`
- `azure_dns_zone_delete` → `delete_dns_zone`
- `azure_dns_record_create` → `create_dns_record`, rollback: `delete_dns_record`
- `azure_dns_record_delete` → `delete_dns_record`

### Migration 035

```python
new_types = ['azure_dns_zone_create', 'azure_dns_zone_delete', 'azure_dns_record_create', 'azure_dns_record_delete']
```

### Phase X smoke test

```python
def run_phase_x(client, cloud_account_id, azure_resource_group):
    """Phase X: Azure DNS — create zone + A record, verify, explicit record delete, rollback zone."""
    import secrets as _secrets
    zone_name = f"nexplane-smoke-{_secrets.token_hex(4)}.example.com"
    record_name = "smoke"
    rollback_stack = []
    dns = _get_azure_dns_client()

    try:
        cr = client.run_cr(
            "[Phase X] create DNS zone", "azure_dns_zone_create", cloud_account_id,
            {"zone_name": zone_name, "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_dns_zone_create"))

        if dns:
            zone = dns.zones.get(azure_resource_group, zone_name)
            assert zone.name == zone_name
            log(f"DNS zone verified: {zone_name}")

        cr = client.run_cr(
            "[Phase X] create A record", "azure_dns_record_create", cloud_account_id,
            {"zone_name": zone_name, "record_name": record_name,
             "ip_address": "10.0.0.1", "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_dns_record_create"))

        if dns:
            rs = dns.record_sets.get(azure_resource_group, zone_name, record_name, "A")
            assert rs.a_records[0].ipv4_address == "10.0.0.1"
            log(f"A record verified: {record_name}.{zone_name}")

        # Explicit record delete
        client.run_cr(
            "[Phase X] delete A record", "azure_dns_record_delete", cloud_account_id,
            {"zone_name": zone_name, "record_name": record_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.pop()  # record already deleted

        if dns:
            records = list(dns.record_sets.list_by_dns_zone(azure_resource_group, zone_name))
            assert not any(r.name == record_name for r in records)
            log("A record deleted and verified gone")

        log("Phase X complete")
    except Exception as e:
        print(f"\n❌ Phase X failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if dns:
            try:
                dns.zones.begin_delete(azure_resource_group, zone_name).result()
                print(f"  Safety net: deleted DNS zone {zone_name}")
            except Exception:
                pass
```

New smoke_helpers helper: `_get_azure_dns_client()`.

---

## Phase Y: SQL Database Lifecycle

### New `_client.py` addition

```python
def get_sql_client(creds: dict):
    from azure.mgmt.sql import SqlManagementClient
    return SqlManagementClient(get_credential(creds), creds['subscription_id'])
```

New package: `azure-mgmt-sql>=4.0.0`

### New executors

**`create_sql_server.py`**

Input: `server_name`, `resource_group`, `location` (default `"eastus"`), `admin_login` (default `"nexplaneadmin"`), `admin_password`.

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

**`delete_sql_server.py`**

Input: `server_name`, `resource_group`. Calls `sql.servers.begin_delete(rg, server_name).result()`.

**`create_sql_database.py`**

Input: `server_name`, `database_name`, `resource_group`, `location` (default `"eastus"`), `sku_name` (default `"Basic"`).

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

**`delete_sql_database.py`**

Input: `server_name`, `database_name`, `resource_group`. Calls `sql.databases.begin_delete(rg, server_name, db_name).result()`.

### Change types

- `azure_sql_server_create` → `create_sql_server`, rollback: `delete_sql_server`
- `azure_sql_server_delete` → `delete_sql_server`
- `azure_sql_database_create` → `create_sql_database`, rollback: `delete_sql_database`
- `azure_sql_database_delete` → `delete_sql_database`

### Migration 036

```python
new_types = ['azure_sql_server_create', 'azure_sql_server_delete', 'azure_sql_database_create', 'azure_sql_database_delete']
```

### Phase Y smoke test (~10 min)

```python
def run_phase_y(client, cloud_account_id, azure_resource_group):
    """Phase Y: Azure SQL Database lifecycle — create server + database, verify, delete explicitly, rollback server."""
    import secrets as _secrets
    server_name = f"nexplane-smoke-sql-{_secrets.token_hex(4)}"
    db_name = "nexplane-smoke-db"
    admin_password = f"NxP!{_secrets.token_hex(8)}"  # meets Azure complexity requirements
    rollback_stack = []
    sql = _get_azure_sql_client()

    try:
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
            assert server.name == server_name
            log(f"SQL server verified: {server_name}")

        cr = client._run_cr_with_timeout(
            "[Phase Y] create SQL database", "azure_sql_database_create", cloud_account_id,
            {"server_name": server_name, "database_name": db_name,
             "resource_group": azure_resource_group, "location": "eastus", "sku_name": "Basic"},
            timeout=300,
        )
        rollback_stack.append((cr["id"], "azure_sql_database_create"))

        if sql:
            db = sql.databases.get(azure_resource_group, server_name, db_name)
            assert db.name == db_name
            log(f"SQL database verified: {db_name}")

        # Explicit database delete
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
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if sql:
            try:
                sql.servers.begin_delete(azure_resource_group, server_name).result()
                print(f"  Safety net: deleted SQL server {server_name}")
            except Exception:
                pass
```

New smoke_helpers helper: `_get_azure_sql_client()`.

---

## Phase Z: Monitor Metric Alert

### New `_client.py` addition

```python
def get_monitor_client(creds: dict):
    from azure.mgmt.monitor import MonitorManagementClient
    return MonitorManagementClient(get_credential(creds), creds['subscription_id'])
```

New package: `azure-mgmt-monitor>=6.0.0`

### New executors

**`create_metric_alert.py`**

Input: `alert_name`, `resource_group`, `target_resource_id` (ARM resource ID to monitor), `metric_name` (default `"Percentage CPU"`), `threshold` (default `90`), `location` (default `"global"`).

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
        MetricAlertResource, MetricAlertSingleResourceMultipleMetricCriteria,
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

**`delete_metric_alert.py`**

Input: `alert_name`, `resource_group`. Calls `monitor.metric_alerts.delete(rg, alert_name)`.

### Change types

- `azure_metric_alert_create` → `create_metric_alert`, rollback: `delete_metric_alert`
- `azure_metric_alert_delete` → `delete_metric_alert`

### Migration 037

```python
new_types = ['azure_metric_alert_create', 'azure_metric_alert_delete']
```

### Phase Z smoke test

Phase Z requires Phase N VM (needs `azure_phase_result`). If Phase N hasn't run, skip with a warning.

```python
def run_phase_z(client, cloud_account_id, azure_resource_group, azure_phase_result=None):
    """Phase Z: Azure Monitor — create metric alert on Phase N VM, verify, rollback."""
    if not azure_phase_result or not azure_phase_result.get("vm_asset"):
        print("\n[Phase Z] Skipping — requires Phase N VM (run with N,Z)")
        return

    import secrets as _secrets
    alert_name = f"nexplane-smoke-alert-{_secrets.token_hex(4)}"
    rollback_stack = []

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
            assert alert.name == alert_name
            log(f"Metric alert verified: {alert_name}")

        log("Phase Z complete")
    except Exception as e:
        print(f"\n❌ Phase Z failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if monitor:
            try:
                monitor.metric_alerts.delete(azure_resource_group, alert_name)
                print(f"  Safety net: deleted metric alert {alert_name}")
            except Exception:
                pass
```

New smoke_helpers helpers: `_get_azure_network_client()`, `_get_azure_dns_client()`, `_get_azure_sql_client()`, `_get_azure_monitor_client()`.

---

## Packages to Add to requirements.txt

```
azure-mgmt-dns>=8.0.0
azure-mgmt-sql>=4.0.0
azure-mgmt-monitor>=6.0.0
```

`azure-mgmt-network` is already present — no addition needed for Phase U.

## _client.py additions

```python
def get_dns_client(creds: dict):
    from azure.mgmt.dns import DnsManagementClient
    return DnsManagementClient(get_credential(creds), creds['subscription_id'])

def get_sql_client(creds: dict):
    from azure.mgmt.sql import SqlManagementClient
    return SqlManagementClient(get_credential(creds), creds['subscription_id'])

def get_monitor_client(creds: dict):
    from azure.mgmt.monitor import MonitorManagementClient
    return MonitorManagementClient(get_credential(creds), creds['subscription_id'])
```

## main() wiring in test_azure_live.py

Replace stubs in main():
```python
if "U" in phases:
    run_phase_u(client, cloud_account_id, args.azure_resource_group)
if "X" in phases:
    run_phase_x(client, cloud_account_id, args.azure_resource_group)
if "Y" in phases:
    run_phase_y(client, cloud_account_id, args.azure_resource_group)
if "Z" in phases:
    run_phase_z(client, cloud_account_id, args.azure_resource_group, azure_phase_result)
```

Update `--phases` help and docstring to document U, X, Y, Z.

---

## Execution Order

1. **Sub-project U** (migration 034, 2 executors, Phase U) — no new packages
2. **Sub-project X** (migration 035, 4 executors, Phase X) — needs azure-mgmt-dns
3. **Sub-project Y** (migration 036, 4 executors, Phase Y) — needs azure-mgmt-sql
4. **Sub-project Z** (migration 037, 2 executors, Phase Z) — needs azure-mgmt-monitor

---

## Out of Scope

- VNet peering, private endpoints (requires two VNets, too complex for smoke test)
- SQL Managed Instance (4+ hour provisioning)
- Azure Monitor diagnostic settings (requires Log Analytics workspace)
- Log Analytics workspace management
