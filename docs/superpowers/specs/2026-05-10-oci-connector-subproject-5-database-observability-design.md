# OCI Connector — Sub-project 5: Database + Observability Design

## Scope

Autonomous Database (ADB), MySQL HeatWave, OCI Monitoring alarms, and OCI Logging. Follows patterns from sub-projects 1-4.

**Key design decisions:**
- ADB and MySQL → `database` asset type (exact fit)
- OCI Monitoring alarms → no dedicated asset type; alarm state tracked in CR results and asset metadata on the `cloud_account` (compartment) asset (same approach as AWS CloudWatch alarms)
- OCI Logging → enable/disable logging on compartments; tracked as metadata on the compartment `cloud_account` asset
- ADB is tenancy-level DB in Always Free tier (1 ADB, 20 GB); MySQL is compartment-level
- ADB stop/start included even though Always Free ADB auto-starts — useful for paid tiers

---

## New Change Types (12)

```
oci_adb_create              Create an Autonomous Database
oci_adb_stop                Stop an ADB instance
oci_adb_start               Start an ADB instance
oci_adb_delete              Delete an ADB instance
oci_adb_backup              Create a manual ADB backup
oci_mysql_create            Create a MySQL HeatWave DB System
oci_mysql_stop              Stop a MySQL DB System
oci_mysql_start             Start a MySQL DB System
oci_mysql_delete            Delete a MySQL DB System
oci_alarm_create            Create a Monitoring alarm
oci_alarm_delete            Delete a Monitoring alarm
oci_logging_enable          Enable logging for a compartment/resource
```

All 12 added to `_IMPLICIT_ROLLBACK_TYPES`.

---

## New Files

```
backend/app/connectors/executors/oci/
  discover_adb.py
  discover_mysql.py
  discover_alarms.py
  create_adb.py
  stop_adb.py
  start_adb.py
  delete_adb.py
  backup_adb.py
  create_mysql.py
  stop_mysql.py
  start_mysql.py
  delete_mysql.py
  create_alarm.py
  delete_alarm.py
  enable_logging.py

backend/alembic/versions/044_add_oci_database_change_types.py
```

New OCI SDK clients in `_client.py`:
```python
def get_database_client(creds) -> oci.database.DatabaseClient
def get_mysql_client(creds) -> oci.mysql.DbSystemClient
def get_monitoring_client(creds) -> oci.monitoring.MonitoringClient
def get_logging_client(creds) -> oci.logging.LoggingManagementClient
```

---

## Discovery

### `discover_adb.py`
- Calls `database.list_autonomous_databases(compartment_id)`
- Each ADB → `database` asset:
  ```json
  {
    "name": "<display_name>",
    "asset_type": "database",
    "asset_metadata": {
      "db_id": "ocid1.autonomousdatabase...",
      "db_name": "<db_name>",
      "db_workload": "OLTP",
      "lifecycle_state": "AVAILABLE",
      "cpu_core_count": 1,
      "data_storage_size_in_tbs": 1,
      "is_free_tier": true,
      "connection_strings": {"high": "...", "medium": "...", "low": "..."},
      "compartment_id": "..."
    },
    "tags": ["oci", "autonomous-database"]
  }
  ```
- Dedup key: `db_id`

### `discover_mysql.py`
- Calls `mysql.list_db_systems(compartment_id)`
- Each DB System → `database` asset tagged `oci-mysql`:
  ```json
  {
    "name": "<display_name>",
    "asset_type": "database",
    "asset_metadata": {
      "db_system_id": "ocid1.mysqldbsystem...",
      "mysql_version": "8.0.36",
      "shape_name": "MySQL.VM.Standard.E4.1.8GB",
      "lifecycle_state": "ACTIVE",
      "endpoint_hostname": "<hostname>",
      "port": 3306,
      "data_storage_size_in_gbs": 50,
      "compartment_id": "..."
    },
    "tags": ["oci", "mysql"]
  }
  ```
- Dedup key: `db_system_id`

### `discover_alarms.py`
- Calls `monitoring.list_alarms(compartment_id)`
- Returns raw list in discovery result; alarms surfaced via compartment asset metadata updates
- No separate asset record (alarms are ephemeral, same as AWS CloudWatch)

---

## Autonomous Database Executors

### `create_adb.py` (`oci_adb_create`)
**Parameters (all pre-populated):**
```
compartment_id          resolved from target compartment asset
display_name            "nexplane-adb"
db_name                 "nexplaneadb"   (no spaces, max 14 chars)
admin_password          "Nexplane1234!"  (must meet OCI complexity requirements)
db_workload             "OLTP"
cpu_core_count          1
data_storage_size_in_tbs  1
is_auto_scaling_enabled   false
is_free_tier            true
license_model           "LICENSE_INCLUDED"
```
Polls until lifecycle = AVAILABLE (up to 15 min).
Returns `_auto_asset` (`database`, tagged `oci autonomous-database`).
Returns `connection_strings` in result (high/medium/low connection profiles).
Rollback: `oci_adb_delete`.

### `stop_adb.py` (`oci_adb_stop`)
- Calls `database.stop_autonomous_database(db_id)`, polls until STOPPED
- Parameters: `db_id` (auto-populated from target database asset)
- Rollback: `oci_adb_start`

### `start_adb.py` (`oci_adb_start`)
- Calls `database.start_autonomous_database(db_id)`, polls until AVAILABLE
- Rollback: `oci_adb_stop`

### `delete_adb.py` (`oci_adb_delete`)
- Calls `database.delete_autonomous_database(db_id)`, polls until TERMINATED
- Parameters: `db_id` (auto-populated)
- Rollback: none (destructive)

### `backup_adb.py` (`oci_adb_backup`)
**Parameters (all pre-populated):**
```
db_id            auto-populated from target database asset
display_name     "nexplane-backup-<timestamp>"
```
Calls `database.create_autonomous_database_backup()`.
Polls until backup lifecycle = ACTIVE.
Rollback: delete backup.

---

## MySQL HeatWave Executors

### `create_mysql.py` (`oci_mysql_create`)
**Parameters (all pre-populated):**
```
compartment_id          resolved from target compartment
display_name            "nexplane-mysql"
admin_username          "nexplane"
admin_password          "Nexplane1234!"
shape_name              "MySQL.VM.Standard.E4.1.8GB"
mysql_version           "8.0.36"
subnet_id               resolved from oci-subnet asset in compartment
data_storage_size_in_gbs  50
availability_domain     resolved from compartment's first AD
```
Polls until lifecycle = ACTIVE (up to 20 min — MySQL provisioning is slow).
Returns `_auto_asset` (`database`, tagged `oci-mysql`).
Rollback: `oci_mysql_delete`.

### `stop_mysql.py` / `start_mysql.py`
- Calls `mysql.stop_db_system()` / `mysql.start_db_system()`
- Polls until INACTIVE / ACTIVE
- Rollback: start→stop, stop→start

### `delete_mysql.py` (`oci_mysql_delete`)
- Calls `mysql.delete_db_system(db_system_id)`, polls until DELETED
- Rollback: none (destructive)

---

## Monitoring Executors

### `create_alarm.py` (`oci_alarm_create`)
**Parameters (all pre-populated):**
```
compartment_id    resolved from target compartment
display_name      "nexplane-alarm"
namespace         "oci_computeagent"
query             "CpuUtilization[1m].mean() > 80"
severity          "CRITICAL"
body              "CPU utilization exceeded 80%"
destinations      []   (OCI Notification topic OCIDs; empty = log only)
is_enabled        true
```
Returns alarm OCID in result. Rollback: `oci_alarm_delete`.

### `delete_alarm.py` (`oci_alarm_delete`)
- Calls `monitoring.delete_alarm(alarm_id)`
- Parameters: `alarm_id` (from execution result or target asset metadata)
- Rollback: none

---

## Logging Executor

### `enable_logging.py` (`oci_logging_enable`)
**Parameters (all pre-populated):**
```
compartment_id     resolved from target compartment
log_group_name     "nexplane-logs"
log_name           "nexplane-audit-log"
log_type           "AUDIT"
is_enabled         true
retention_duration  30
```
Creates a Log Group + Log in OCI Logging service.
Rollback: disable/delete log and log group.

---

## Frontend Wiring

### `api.ts`
12 new `ChangeType` values.

### `CreateChangeRequest.tsx`
"Oracle Cloud" category extended. Database CRs grouped within category. All outcome templates pre-populated with sensible defaults including working passwords that meet OCI complexity requirements (users can change them).

### `AssetDetail.tsx`
- `database` assets tagged `oci autonomous-database`: quick actions stop, start, backup, delete
- `database` assets tagged `oci-mysql`: quick actions stop, start, delete

---

## Smoke Test Phases

### OCI_M — Autonomous Database
1. Fire `oci_adb_create` (is_free_tier=true) → poll until AVAILABLE, verify `database` asset in inventory with connection_strings in metadata
2. Fire `oci_adb_stop` → OCI SDK verify STOPPED
3. Fire `oci_adb_start` → OCI SDK verify AVAILABLE
4. Fire `oci_adb_backup` → OCI SDK verify backup ACTIVE
5. Fire `oci_adb_delete` → OCI SDK verify TERMINATED

### OCI_N — Monitoring + Logging
1. Fire `oci_alarm_create` → OCI SDK verify alarm exists and is ACTIVE
2. Fire `oci_alarm_delete` → OCI SDK verify deleted
3. Fire `oci_logging_enable` → OCI SDK verify log group + log created

Note: MySQL smoke test is skipped by default (slow provisioning, costly on paid tiers). Can be enabled with `--include-slow` flag alongside Phase J (RDS).

---

## DB Migration (044)

```sql
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_adb_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_adb_stop';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_adb_start';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_adb_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_adb_backup';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_mysql_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_mysql_stop';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_mysql_start';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_mysql_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_alarm_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_alarm_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_logging_enable';
```
