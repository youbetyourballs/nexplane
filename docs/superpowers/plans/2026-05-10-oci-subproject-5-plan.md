# OCI Connector Sub-project 5: Database + Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add Autonomous Database (ADB), MySQL HeatWave, OCI Monitoring alarms, and OCI Logging executors to the OCI connector, with full frontend wiring, DB migration, and smoke test coverage.

**Architecture:** All executors follow the established pattern: `async def execute(parameters, asset_ids, connector)` with a mock-path guard on missing credentials, `loop.run_in_executor(None, ...)` for all blocking OCI SDK calls, and long timeouts to accommodate slow DB provisioning (ADB up to 20 min, MySQL up to 25 min). New OCI SDK client factories are added to the existing `_client.py` from sub-projects 1-4. Alarm and logging assets are tracked as metadata on the compartment `cloud_account` asset rather than as standalone asset records, matching the AWS CloudWatch pattern.

**Tech Stack:** OCI Python SDK (`oci` package — `oci.database`, `oci.mysql`, `oci.monitoring`, `oci.logging`), Python asyncio, SQLAlchemy Alembic for DB migration, React + TypeScript for frontend.

---

### Task 1: Discovery executors — discover_adb.py, discover_mysql.py, discover_alarms.py

**Files:**
- Create: `backend/app/connectors/executors/oci/discover_adb.py`
- Create: `backend/app/connectors/executors/oci/discover_mysql.py`
- Create: `backend/app/connectors/executors/oci/discover_alarms.py`
- Modify: `backend/app/connectors/executors/oci/_client.py` — add four new client factories

- [ ] Step 1: In `_client.py`, add four new factory functions below the existing ones:
  ```python
  def get_database_client(creds: dict):
      import oci
      config = _build_config(creds)
      return oci.database.DatabaseClient(config)

  def get_mysql_client(creds: dict):
      import oci
      config = _build_config(creds)
      return oci.mysql.DbSystemClient(config)

  def get_monitoring_client(creds: dict):
      import oci
      config = _build_config(creds)
      return oci.monitoring.MonitoringClient(config)

  def get_logging_client(creds: dict):
      import oci
      config = _build_config(creds)
      return oci.logging.LoggingManagementClient(config)
  ```
  Confirm `_build_config` already exists in `_client.py` and reuse it; if it is named differently, match the existing helper name.

- [ ] Step 2: Create `discover_adb.py`:
  ```python
  import asyncio
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")

      if not creds:
          return {"assets": [], "mock": True}

      from ._client import get_database_client
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()

      def _list():
          return db_client.list_autonomous_databases(compartment_id=compartment_id).data

      adbs = await loop.run_in_executor(None, _list)
      assets = []
      for adb in adbs:
          assets.append({
              "name": adb.display_name,
              "asset_type": "database",
              "environment": "prod",
              "criticality": "high",
              "asset_metadata": {
                  "db_id": adb.id,
                  "db_name": adb.db_name,
                  "db_workload": adb.db_workload,
                  "lifecycle_state": adb.lifecycle_state,
                  "cpu_core_count": adb.cpu_core_count,
                  "data_storage_size_in_tbs": adb.data_storage_size_in_tbs,
                  "is_free_tier": adb.is_free_tier,
                  "connection_strings": (
                      {
                          "high": adb.connection_strings.high if adb.connection_strings else None,
                          "medium": adb.connection_strings.medium if adb.connection_strings else None,
                          "low": adb.connection_strings.low if adb.connection_strings else None,
                      }
                      if adb.connection_strings else {}
                  ),
                  "compartment_id": adb.compartment_id,
                  "provider": "oci",
              },
              "tags": ["oci", "autonomous-database"],
              "_dedup_key": adb.id,
          })

      return {
          "assets": assets,
          "count": len(assets),
          "discovered_at": datetime.now(timezone.utc).isoformat(),
      }
  ```

- [ ] Step 3: Create `discover_mysql.py`:
  ```python
  import asyncio
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")

      if not creds:
          return {"assets": [], "mock": True}

      from ._client import get_mysql_client
      mysql_client = get_mysql_client(creds)
      loop = asyncio.get_running_loop()

      def _list():
          return mysql_client.list_db_systems(compartment_id=compartment_id).data

      systems = await loop.run_in_executor(None, _list)
      assets = []
      for sys in systems:
          endpoint_hostname = ""
          port = 3306
          if sys.endpoints:
              endpoint_hostname = sys.endpoints[0].hostname or ""
              port = sys.endpoints[0].port or 3306
          assets.append({
              "name": sys.display_name,
              "asset_type": "database",
              "environment": "prod",
              "criticality": "high",
              "asset_metadata": {
                  "db_system_id": sys.id,
                  "mysql_version": sys.mysql_version,
                  "shape_name": sys.shape_name,
                  "lifecycle_state": sys.lifecycle_state,
                  "endpoint_hostname": endpoint_hostname,
                  "port": port,
                  "data_storage_size_in_gbs": sys.data_storage_size_in_gbs,
                  "compartment_id": sys.compartment_id,
                  "provider": "oci",
              },
              "tags": ["oci", "mysql"],
              "_dedup_key": sys.id,
          })

      return {
          "assets": assets,
          "count": len(assets),
          "discovered_at": datetime.now(timezone.utc).isoformat(),
      }
  ```

- [ ] Step 4: Create `discover_alarms.py`:
  ```python
  import asyncio
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      """Discovers OCI Monitoring alarms in a compartment.
      Alarms are surfaced via compartment asset metadata updates, not as
      standalone asset records (same approach as AWS CloudWatch alarms).
      """
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")

      if not creds:
          return {"alarms": [], "mock": True}

      from ._client import get_monitoring_client
      mon_client = get_monitoring_client(creds)
      loop = asyncio.get_running_loop()

      def _list():
          return mon_client.list_alarms(compartment_id=compartment_id).data

      alarms = await loop.run_in_executor(None, _list)
      alarm_list = [
          {
              "alarm_id": a.id,
              "display_name": a.display_name,
              "namespace": a.namespace,
              "query": a.query,
              "severity": a.severity,
              "lifecycle_state": a.lifecycle_state,
              "is_enabled": a.is_enabled,
          }
          for a in alarms
      ]

      return {
          "alarms": alarm_list,
          "count": len(alarm_list),
          "discovered_at": datetime.now(timezone.utc).isoformat(),
      }
  ```

---

### Task 2: ADB create, stop, start executors

**Files:**
- Create: `backend/app/connectors/executors/oci/create_adb.py`
- Create: `backend/app/connectors/executors/oci/stop_adb.py`
- Create: `backend/app/connectors/executors/oci/start_adb.py`

- [ ] Step 1: Create `create_adb.py` — timeout 20 min (1200 s), polls every 30 s for up to 40 attempts:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")
      display_name = parameters.get("display_name", "nexplane-adb")
      db_name = parameters.get("db_name", "nexplaneadb")
      admin_password = parameters.get("admin_password", "Nexplane1234!")
      db_workload = parameters.get("db_workload", "OLTP")
      cpu_core_count = parameters.get("cpu_core_count", 1)
      data_storage_size_in_tbs = parameters.get("data_storage_size_in_tbs", 1)
      is_auto_scaling_enabled = parameters.get("is_auto_scaling_enabled", False)
      is_free_tier = parameters.get("is_free_tier", True)
      license_model = parameters.get("license_model", "LICENSE_INCLUDED")

      auto_asset = {
          "name": display_name,
          "asset_type": "database",
          "environment": "prod",
          "criticality": "high",
          "asset_metadata": {
              "db_name": db_name,
              "db_workload": db_workload,
              "compartment_id": compartment_id,
              "provider": "oci",
              "is_free_tier": is_free_tier,
          },
          "tags": ["oci", "autonomous-database"],
      }

      if not creds:
          return {
              "action": "create_adb",
              "display_name": display_name,
              "db_name": db_name,
              "status": "AVAILABLE",
              "mock": True,
              "_auto_asset": auto_asset,
          }

      import oci
      from ._client import get_database_client
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()

      def _create():
          details = oci.database.models.CreateAutonomousDatabaseDetails(
              compartment_id=compartment_id,
              display_name=display_name,
              db_name=db_name,
              admin_password=admin_password,
              db_workload=db_workload,
              cpu_core_count=cpu_core_count,
              data_storage_size_in_tbs=data_storage_size_in_tbs,
              is_auto_scaling_enabled=is_auto_scaling_enabled,
              is_free_tier=is_free_tier,
              license_model=license_model,
          )
          return db_client.create_autonomous_database(
              create_autonomous_database_details=details
          ).data

      adb = await loop.run_in_executor(None, _create)
      db_id = adb.id

      # Poll until AVAILABLE — up to 40 x 30s = 20 min
      def _poll():
          for _ in range(40):
              info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
              if info.lifecycle_state == "AVAILABLE":
                  return info
              if info.lifecycle_state in ("TERMINATED", "TERMINATING", "FAILED"):
                  raise RuntimeError(f"ADB entered terminal state: {info.lifecycle_state}")
              time.sleep(30)
          raise TimeoutError("ADB did not become AVAILABLE within 20 minutes")

      final = await loop.run_in_executor(None, _poll)
      connection_strings = {}
      if final.connection_strings:
          connection_strings = {
              "high": final.connection_strings.high,
              "medium": final.connection_strings.medium,
              "low": final.connection_strings.low,
          }

      auto_asset["asset_metadata"]["db_id"] = db_id
      auto_asset["asset_metadata"]["lifecycle_state"] = final.lifecycle_state
      auto_asset["asset_metadata"]["connection_strings"] = connection_strings
      auto_asset["asset_metadata"]["cpu_core_count"] = cpu_core_count
      auto_asset["asset_metadata"]["data_storage_size_in_tbs"] = data_storage_size_in_tbs

      return {
          "action": "create_adb",
          "display_name": display_name,
          "db_id": db_id,
          "db_name": db_name,
          "status": final.lifecycle_state,
          "connection_strings": connection_strings,
          "executed_at": datetime.now(timezone.utc).isoformat(),
          "_auto_asset": auto_asset,
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.delete_adb import execute as delete
      return await delete(
          {"db_id": execution_result.get("db_id", parameters.get("db_id", ""))},
          [], connector,
      )
  ```

- [ ] Step 2: Create `stop_adb.py` — polls until STOPPED, up to 20 x 30s = 10 min:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_id = parameters.get("db_id", "")

      if not creds:
          return {"action": "stop_adb", "db_id": db_id, "status": "STOPPED", "mock": True}

      from ._client import get_database_client
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()

      def _stop_and_poll():
          db_client.stop_autonomous_database(autonomous_database_id=db_id)
          for _ in range(20):
              info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
              if info.lifecycle_state == "STOPPED":
                  return info.lifecycle_state
              time.sleep(30)
          raise TimeoutError("ADB did not reach STOPPED within 10 minutes")

      state = await loop.run_in_executor(None, _stop_and_poll)
      return {
          "action": "stop_adb",
          "db_id": db_id,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.start_adb import execute as start
      return await start({"db_id": execution_result.get("db_id", parameters.get("db_id", ""))}, [], connector)
  ```

- [ ] Step 3: Create `start_adb.py` — polls until AVAILABLE, up to 20 x 30s = 10 min:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_id = parameters.get("db_id", "")

      if not creds:
          return {"action": "start_adb", "db_id": db_id, "status": "AVAILABLE", "mock": True}

      from ._client import get_database_client
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()

      def _start_and_poll():
          db_client.start_autonomous_database(autonomous_database_id=db_id)
          for _ in range(20):
              info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
              if info.lifecycle_state == "AVAILABLE":
                  return info.lifecycle_state
              time.sleep(30)
          raise TimeoutError("ADB did not reach AVAILABLE within 10 minutes")

      state = await loop.run_in_executor(None, _start_and_poll)
      return {
          "action": "start_adb",
          "db_id": db_id,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.stop_adb import execute as stop
      return await stop({"db_id": execution_result.get("db_id", parameters.get("db_id", ""))}, [], connector)
  ```

---

### Task 3: ADB delete and backup executors

**Files:**
- Create: `backend/app/connectors/executors/oci/delete_adb.py`
- Create: `backend/app/connectors/executors/oci/backup_adb.py`

- [ ] Step 1: Create `delete_adb.py` — polls until TERMINATED, up to 20 x 30s = 10 min; no rollback (destructive):
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_id = parameters.get("db_id", "")

      if not creds:
          return {"action": "delete_adb", "db_id": db_id, "status": "TERMINATED", "mock": True}

      from ._client import get_database_client
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()

      def _delete_and_poll():
          db_client.delete_autonomous_database(autonomous_database_id=db_id)
          for _ in range(20):
              try:
                  info = db_client.get_autonomous_database(autonomous_database_id=db_id).data
                  if info.lifecycle_state == "TERMINATED":
                      return "TERMINATED"
              except Exception:
                  # 404 means deleted
                  return "TERMINATED"
              time.sleep(30)
          raise TimeoutError("ADB did not reach TERMINATED within 10 minutes")

      state = await loop.run_in_executor(None, _delete_and_poll)
      return {
          "action": "delete_adb",
          "db_id": db_id,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }
  ```

- [ ] Step 2: Create `backup_adb.py` — polls until backup lifecycle = ACTIVE, up to 30 x 60s = 30 min; rollback deletes backup:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_id = parameters.get("db_id", "")
      ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
      display_name = parameters.get("display_name", f"nexplane-backup-{ts}")

      if not creds:
          return {
              "action": "backup_adb",
              "db_id": db_id,
              "backup_id": "mock-backup-id",
              "status": "ACTIVE",
              "mock": True,
          }

      import oci
      from ._client import get_database_client
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()

      def _backup_and_poll():
          details = oci.database.models.CreateAutonomousDatabaseBackupDetails(
              autonomous_database_id=db_id,
              display_name=display_name,
          )
          backup = db_client.create_autonomous_database_backup(
              create_autonomous_database_backup_details=details
          ).data
          backup_id = backup.id
          for _ in range(30):
              info = db_client.get_autonomous_database_backup(
                  autonomous_database_backup_id=backup_id
              ).data
              if info.lifecycle_state == "ACTIVE":
                  return backup_id, "ACTIVE"
              if info.lifecycle_state == "FAILED":
                  raise RuntimeError("Backup failed")
              time.sleep(60)
          raise TimeoutError("Backup did not become ACTIVE within 30 minutes")

      backup_id, state = await loop.run_in_executor(None, _backup_and_poll)
      return {
          "action": "backup_adb",
          "db_id": db_id,
          "backup_id": backup_id,
          "display_name": display_name,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      """Delete the backup that was created."""
      creds = getattr(connector, "credentials", {})
      backup_id = execution_result.get("backup_id", "")
      if not creds or not backup_id:
          return {"action": "rollback_backup_adb", "mock": True}

      from ._client import get_database_client
      import asyncio
      db_client = get_database_client(creds)
      loop = asyncio.get_running_loop()
      await loop.run_in_executor(
          None,
          lambda: db_client.delete_autonomous_database_backup(
              autonomous_database_backup_id=backup_id
          ),
      )
      return {"action": "rollback_backup_adb", "backup_id": backup_id, "status": "DELETED"}
  ```

---

### Task 4: MySQL HeatWave executors — create, stop, start, delete

**Files:**
- Create: `backend/app/connectors/executors/oci/create_mysql.py`
- Create: `backend/app/connectors/executors/oci/stop_mysql.py`
- Create: `backend/app/connectors/executors/oci/start_mysql.py`
- Create: `backend/app/connectors/executors/oci/delete_mysql.py`

- [ ] Step 1: Create `create_mysql.py` — timeout 25 min (50 x 30s polls); MySQL provisioning is slow:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")
      display_name = parameters.get("display_name", "nexplane-mysql")
      admin_username = parameters.get("admin_username", "nexplane")
      admin_password = parameters.get("admin_password", "Nexplane1234!")
      shape_name = parameters.get("shape_name", "MySQL.VM.Standard.E4.1.8GB")
      mysql_version = parameters.get("mysql_version", "8.0.36")
      subnet_id = parameters.get("subnet_id", "")
      data_storage_size_in_gbs = parameters.get("data_storage_size_in_gbs", 50)
      availability_domain = parameters.get("availability_domain", "")

      auto_asset = {
          "name": display_name,
          "asset_type": "database",
          "environment": "prod",
          "criticality": "high",
          "asset_metadata": {
              "shape_name": shape_name,
              "mysql_version": mysql_version,
              "compartment_id": compartment_id,
              "provider": "oci",
          },
          "tags": ["oci", "mysql"],
      }

      if not creds:
          return {
              "action": "create_mysql",
              "display_name": display_name,
              "db_system_id": "mock-mysql-id",
              "status": "ACTIVE",
              "mock": True,
              "_auto_asset": auto_asset,
          }

      import oci
      from ._client import get_mysql_client
      mysql_client = get_mysql_client(creds)
      loop = asyncio.get_running_loop()

      def _create():
          details = oci.mysql.models.CreateDbSystemDetails(
              compartment_id=compartment_id,
              display_name=display_name,
              admin_username=admin_username,
              admin_password=admin_password,
              shape_name=shape_name,
              mysql_version=mysql_version,
              subnet_id=subnet_id,
              data_storage_size_in_gbs=data_storage_size_in_gbs,
              availability_domain=availability_domain,
          )
          return mysql_client.create_db_system(create_db_system_details=details).data

      sys = await loop.run_in_executor(None, _create)
      db_system_id = sys.id

      # Poll until ACTIVE — up to 50 x 30s = 25 min
      def _poll():
          for _ in range(50):
              info = mysql_client.get_db_system(db_system_id=db_system_id).data
              if info.lifecycle_state == "ACTIVE":
                  return info
              if info.lifecycle_state in ("FAILED", "DELETED"):
                  raise RuntimeError(f"MySQL DB System entered state: {info.lifecycle_state}")
              time.sleep(30)
          raise TimeoutError("MySQL DB System did not become ACTIVE within 25 minutes")

      final = await loop.run_in_executor(None, _poll)
      endpoint_hostname = ""
      port = 3306
      if final.endpoints:
          endpoint_hostname = final.endpoints[0].hostname or ""
          port = final.endpoints[0].port or 3306

      auto_asset["asset_metadata"]["db_system_id"] = db_system_id
      auto_asset["asset_metadata"]["lifecycle_state"] = final.lifecycle_state
      auto_asset["asset_metadata"]["endpoint_hostname"] = endpoint_hostname
      auto_asset["asset_metadata"]["port"] = port
      auto_asset["asset_metadata"]["data_storage_size_in_gbs"] = data_storage_size_in_gbs

      return {
          "action": "create_mysql",
          "display_name": display_name,
          "db_system_id": db_system_id,
          "endpoint_hostname": endpoint_hostname,
          "port": port,
          "status": final.lifecycle_state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
          "_auto_asset": auto_asset,
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.delete_mysql import execute as delete
      return await delete(
          {"db_system_id": execution_result.get("db_system_id", parameters.get("db_system_id", ""))},
          [], connector,
      )
  ```

- [ ] Step 2: Create `stop_mysql.py` — calls `stop_db_system`, polls until INACTIVE, up to 20 x 30s = 10 min:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_system_id = parameters.get("db_system_id", "")

      if not creds:
          return {"action": "stop_mysql", "db_system_id": db_system_id, "status": "INACTIVE", "mock": True}

      import oci
      from ._client import get_mysql_client
      mysql_client = get_mysql_client(creds)
      loop = asyncio.get_running_loop()

      def _stop_and_poll():
          details = oci.mysql.models.StopDbSystemDetails(
              shutdown_type=oci.mysql.models.StopDbSystemDetails.SHUTDOWN_TYPE_SLOW
          )
          mysql_client.stop_db_system(
              db_system_id=db_system_id,
              stop_db_system_details=details,
          )
          for _ in range(20):
              info = mysql_client.get_db_system(db_system_id=db_system_id).data
              if info.lifecycle_state == "INACTIVE":
                  return info.lifecycle_state
              time.sleep(30)
          raise TimeoutError("MySQL DB System did not reach INACTIVE within 10 minutes")

      state = await loop.run_in_executor(None, _stop_and_poll)
      return {
          "action": "stop_mysql",
          "db_system_id": db_system_id,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.start_mysql import execute as start
      return await start(
          {"db_system_id": execution_result.get("db_system_id", parameters.get("db_system_id", ""))},
          [], connector,
      )
  ```

- [ ] Step 3: Create `start_mysql.py` — calls `start_db_system`, polls until ACTIVE, up to 20 x 30s = 10 min:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_system_id = parameters.get("db_system_id", "")

      if not creds:
          return {"action": "start_mysql", "db_system_id": db_system_id, "status": "ACTIVE", "mock": True}

      from ._client import get_mysql_client
      mysql_client = get_mysql_client(creds)
      loop = asyncio.get_running_loop()

      def _start_and_poll():
          mysql_client.start_db_system(db_system_id=db_system_id)
          for _ in range(20):
              info = mysql_client.get_db_system(db_system_id=db_system_id).data
              if info.lifecycle_state == "ACTIVE":
                  return info.lifecycle_state
              time.sleep(30)
          raise TimeoutError("MySQL DB System did not reach ACTIVE within 10 minutes")

      state = await loop.run_in_executor(None, _start_and_poll)
      return {
          "action": "start_mysql",
          "db_system_id": db_system_id,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.stop_mysql import execute as stop
      return await stop(
          {"db_system_id": execution_result.get("db_system_id", parameters.get("db_system_id", ""))},
          [], connector,
      )
  ```

- [ ] Step 4: Create `delete_mysql.py` — calls `delete_db_system`, polls until DELETED, up to 20 x 30s = 10 min; no rollback:
  ```python
  import asyncio
  import time
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      db_system_id = parameters.get("db_system_id", "")

      if not creds:
          return {"action": "delete_mysql", "db_system_id": db_system_id, "status": "DELETED", "mock": True}

      from ._client import get_mysql_client
      mysql_client = get_mysql_client(creds)
      loop = asyncio.get_running_loop()

      def _delete_and_poll():
          mysql_client.delete_db_system(db_system_id=db_system_id)
          for _ in range(20):
              try:
                  info = mysql_client.get_db_system(db_system_id=db_system_id).data
                  if info.lifecycle_state == "DELETED":
                      return "DELETED"
              except Exception:
                  return "DELETED"
              time.sleep(30)
          raise TimeoutError("MySQL DB System did not reach DELETED within 10 minutes")

      state = await loop.run_in_executor(None, _delete_and_poll)
      return {
          "action": "delete_mysql",
          "db_system_id": db_system_id,
          "status": state,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }
  ```

---

### Task 5: Monitoring alarm executors — create_alarm.py and delete_alarm.py

**Files:**
- Create: `backend/app/connectors/executors/oci/create_alarm.py`
- Create: `backend/app/connectors/executors/oci/delete_alarm.py`

- [ ] Step 1: Create `create_alarm.py`. OCI Monitoring uses MQL query syntax (e.g. `CpuUtilization[1m].mean() > 80`) not CloudWatch syntax:
  ```python
  import asyncio
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")
      display_name = parameters.get("display_name", "nexplane-alarm")
      namespace = parameters.get("namespace", "oci_computeagent")
      # MQL query syntax — note the square-bracket interval and aggregation function
      query = parameters.get("query", "CpuUtilization[1m].mean() > 80")
      severity = parameters.get("severity", "CRITICAL")
      body = parameters.get("body", "CPU utilization exceeded 80%")
      destinations = parameters.get("destinations", [])   # OCI Notification topic OCIDs
      is_enabled = parameters.get("is_enabled", True)

      if not creds:
          return {
              "action": "create_alarm",
              "display_name": display_name,
              "alarm_id": "mock-alarm-id",
              "mock": True,
          }

      import oci
      from ._client import get_monitoring_client
      mon_client = get_monitoring_client(creds)
      loop = asyncio.get_running_loop()

      def _create():
          details = oci.monitoring.models.CreateAlarmDetails(
              compartment_id=compartment_id,
              display_name=display_name,
              namespace=namespace,
              query=query,
              severity=severity,
              body=body,
              destinations=destinations,
              is_enabled=is_enabled,
          )
          return mon_client.create_alarm(create_alarm_details=details).data

      alarm = await loop.run_in_executor(None, _create)
      return {
          "action": "create_alarm",
          "display_name": display_name,
          "alarm_id": alarm.id,
          "namespace": namespace,
          "query": query,
          "severity": severity,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      from app.connectors.executors.oci.delete_alarm import execute as delete
      return await delete(
          {"alarm_id": execution_result.get("alarm_id", parameters.get("alarm_id", ""))},
          [], connector,
      )
  ```

- [ ] Step 2: Create `delete_alarm.py`:
  ```python
  import asyncio
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      alarm_id = parameters.get("alarm_id", "")

      if not creds:
          return {"action": "delete_alarm", "alarm_id": alarm_id, "mock": True}

      from ._client import get_monitoring_client
      mon_client = get_monitoring_client(creds)
      loop = asyncio.get_running_loop()

      await loop.run_in_executor(None, lambda: mon_client.delete_alarm(alarm_id=alarm_id))
      return {
          "action": "delete_alarm",
          "alarm_id": alarm_id,
          "status": "DELETED",
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }
  ```

---

### Task 6: Logging executor — enable_logging.py

**Files:**
- Create: `backend/app/connectors/executors/oci/enable_logging.py`

- [ ] Step 1: Create `enable_logging.py` — creates a Log Group then a Log within it. Rollback deletes both:
  ```python
  import asyncio
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      creds = getattr(connector, "credentials", {})
      compartment_id = parameters.get("compartment_id", "")
      log_group_name = parameters.get("log_group_name", "nexplane-logs")
      log_name = parameters.get("log_name", "nexplane-audit-log")
      log_type = parameters.get("log_type", "AUDIT")
      is_enabled = parameters.get("is_enabled", True)
      retention_duration = parameters.get("retention_duration", 30)

      if not creds:
          return {
              "action": "enable_logging",
              "log_group_id": "mock-log-group-id",
              "log_id": "mock-log-id",
              "mock": True,
          }

      import oci
      from ._client import get_logging_client
      log_client = get_logging_client(creds)
      loop = asyncio.get_running_loop()

      def _create_group():
          details = oci.logging.models.CreateLogGroupDetails(
              compartment_id=compartment_id,
              display_name=log_group_name,
          )
          return log_client.create_log_group(create_log_group_details=details).data

      log_group = await loop.run_in_executor(None, _create_group)
      log_group_id = log_group.id

      def _create_log():
          details = oci.logging.models.CreateLogDetails(
              display_name=log_name,
              log_type=log_type,
              is_enabled=is_enabled,
              retention_duration=retention_duration,
              configuration=oci.logging.models.Configuration(
                  compartment_id=compartment_id,
              ),
          )
          return log_client.create_log(
              log_group_id=log_group_id,
              create_log_details=details,
          ).data

      log = await loop.run_in_executor(None, _create_log)
      return {
          "action": "enable_logging",
          "log_group_id": log_group_id,
          "log_group_name": log_group_name,
          "log_id": log.id,
          "log_name": log_name,
          "log_type": log_type,
          "executed_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      """Delete the log and log group that were created."""
      creds = getattr(connector, "credentials", {})
      log_group_id = execution_result.get("log_group_id", "")
      log_id = execution_result.get("log_id", "")
      if not creds or not log_group_id:
          return {"action": "rollback_enable_logging", "mock": True}

      from ._client import get_logging_client
      log_client = get_logging_client(creds)
      loop = asyncio.get_running_loop()

      if log_id:
          await loop.run_in_executor(
              None,
              lambda: log_client.delete_log(log_group_id=log_group_id, log_id=log_id),
          )
      await loop.run_in_executor(
          None,
          lambda: log_client.delete_log_group(log_group_id=log_group_id),
      )
      return {
          "action": "rollback_enable_logging",
          "log_group_id": log_group_id,
          "log_id": log_id,
          "status": "DELETED",
      }
  ```

---

### Task 7: Extend oci.json catalog

**Files:**
- Modify: `backend/app/connectors/catalog/oci.json`

- [ ] Step 1: Open `oci.json`. In the `"actions"` array, append the following 15 new action objects (3 discover + 12 change). Each action follows the exact same schema as existing actions in the file — confirm field names match before inserting:

  ```json
  {
    "action_id": "discover_adb",
    "action_type": "discover",
    "display_name": "Discover Autonomous Databases",
    "description": "Discover OCI Autonomous Database instances in a compartment.",
    "executor": "oci.discover_adb",
    "generic_action": "discover_adb",
    "applicable_asset_types": ["cloud_account"],
    "parameters": [{"name": "compartment_id", "type": "string", "required": true}],
    "estimated_duration_seconds": 15,
    "execution_tier": 1
  },
  {
    "action_id": "discover_mysql",
    "action_type": "discover",
    "display_name": "Discover MySQL HeatWave DB Systems",
    "description": "Discover OCI MySQL HeatWave DB System instances in a compartment.",
    "executor": "oci.discover_mysql",
    "generic_action": "discover_mysql",
    "applicable_asset_types": ["cloud_account"],
    "parameters": [{"name": "compartment_id", "type": "string", "required": true}],
    "estimated_duration_seconds": 15,
    "execution_tier": 1
  },
  {
    "action_id": "discover_alarms",
    "action_type": "discover",
    "display_name": "Discover OCI Monitoring Alarms",
    "description": "Discover OCI Monitoring alarms in a compartment.",
    "executor": "oci.discover_alarms",
    "generic_action": "discover_alarms",
    "applicable_asset_types": ["cloud_account"],
    "parameters": [{"name": "compartment_id", "type": "string", "required": true}],
    "estimated_duration_seconds": 10,
    "execution_tier": 1
  },
  {
    "action_id": "oci_adb_create",
    "action_type": "change",
    "display_name": "Create Autonomous Database",
    "description": "Provision an OCI Autonomous Database instance. ADB admin_password must be 12+ characters with upper, lower, number, and special character.",
    "executor": "oci.create_adb",
    "generic_action": "oci_adb_create",
    "rollback_action": "oci_adb_delete",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["cloud_account"],
    "estimated_duration_seconds": 900,
    "execution_tier": 2,
    "parameters": [
      {"name": "compartment_id", "type": "string", "required": true},
      {"name": "display_name", "type": "string", "required": false},
      {"name": "db_name", "type": "string", "required": false},
      {"name": "admin_password", "type": "password", "required": false},
      {"name": "db_workload", "type": "string", "required": false},
      {"name": "cpu_core_count", "type": "integer", "required": false},
      {"name": "data_storage_size_in_tbs", "type": "integer", "required": false},
      {"name": "is_free_tier", "type": "boolean", "required": false},
      {"name": "license_model", "type": "string", "required": false}
    ]
  },
  {
    "action_id": "oci_adb_stop",
    "action_type": "change",
    "display_name": "Stop Autonomous Database",
    "description": "Stop a running OCI Autonomous Database instance.",
    "executor": "oci.stop_adb",
    "generic_action": "oci_adb_stop",
    "rollback_action": "oci_adb_start",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 300,
    "execution_tier": 2,
    "parameters": [{"name": "db_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_adb_start",
    "action_type": "change",
    "display_name": "Start Autonomous Database",
    "description": "Start a stopped OCI Autonomous Database instance.",
    "executor": "oci.start_adb",
    "generic_action": "oci_adb_start",
    "rollback_action": "oci_adb_stop",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 300,
    "execution_tier": 2,
    "parameters": [{"name": "db_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_adb_delete",
    "action_type": "change",
    "display_name": "Delete Autonomous Database",
    "description": "Terminate and permanently delete an OCI Autonomous Database instance. Destructive — no rollback.",
    "executor": "oci.delete_adb",
    "generic_action": "oci_adb_delete",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 300,
    "execution_tier": 3,
    "parameters": [{"name": "db_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_adb_backup",
    "action_type": "change",
    "display_name": "Create ADB Manual Backup",
    "description": "Create a manual backup of an OCI Autonomous Database.",
    "executor": "oci.backup_adb",
    "generic_action": "oci_adb_backup",
    "rollback_action": "delete_adb_backup",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 600,
    "execution_tier": 2,
    "parameters": [
      {"name": "db_id", "type": "string", "required": true},
      {"name": "display_name", "type": "string", "required": false}
    ]
  },
  {
    "action_id": "oci_mysql_create",
    "action_type": "change",
    "display_name": "Create MySQL HeatWave DB System",
    "description": "Provision an OCI MySQL HeatWave DB System. Provisioning can take up to 20 minutes.",
    "executor": "oci.create_mysql",
    "generic_action": "oci_mysql_create",
    "rollback_action": "oci_mysql_delete",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["cloud_account"],
    "estimated_duration_seconds": 1200,
    "execution_tier": 2,
    "parameters": [
      {"name": "compartment_id", "type": "string", "required": true},
      {"name": "display_name", "type": "string", "required": false},
      {"name": "admin_username", "type": "string", "required": false},
      {"name": "admin_password", "type": "password", "required": false},
      {"name": "shape_name", "type": "string", "required": false},
      {"name": "mysql_version", "type": "string", "required": false},
      {"name": "subnet_id", "type": "string", "required": true},
      {"name": "data_storage_size_in_gbs", "type": "integer", "required": false},
      {"name": "availability_domain", "type": "string", "required": true}
    ]
  },
  {
    "action_id": "oci_mysql_stop",
    "action_type": "change",
    "display_name": "Stop MySQL HeatWave DB System",
    "description": "Stop a running OCI MySQL HeatWave DB System.",
    "executor": "oci.stop_mysql",
    "generic_action": "oci_mysql_stop",
    "rollback_action": "oci_mysql_start",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 300,
    "execution_tier": 2,
    "parameters": [{"name": "db_system_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_mysql_start",
    "action_type": "change",
    "display_name": "Start MySQL HeatWave DB System",
    "description": "Start a stopped OCI MySQL HeatWave DB System.",
    "executor": "oci.start_mysql",
    "generic_action": "oci_mysql_start",
    "rollback_action": "oci_mysql_stop",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 300,
    "execution_tier": 2,
    "parameters": [{"name": "db_system_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_mysql_delete",
    "action_type": "change",
    "display_name": "Delete MySQL HeatWave DB System",
    "description": "Delete an OCI MySQL HeatWave DB System. Destructive — no rollback.",
    "executor": "oci.delete_mysql",
    "generic_action": "oci_mysql_delete",
    "applicable_asset_types": ["database"],
    "estimated_duration_seconds": 300,
    "execution_tier": 3,
    "parameters": [{"name": "db_system_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_alarm_create",
    "action_type": "change",
    "display_name": "Create OCI Monitoring Alarm",
    "description": "Create an OCI Monitoring alarm using MQL query syntax (e.g. CpuUtilization[1m].mean() > 80).",
    "executor": "oci.create_alarm",
    "generic_action": "oci_alarm_create",
    "rollback_action": "oci_alarm_delete",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["cloud_account"],
    "estimated_duration_seconds": 15,
    "execution_tier": 1,
    "parameters": [
      {"name": "compartment_id", "type": "string", "required": true},
      {"name": "display_name", "type": "string", "required": false},
      {"name": "namespace", "type": "string", "required": false},
      {"name": "query", "type": "string", "required": false},
      {"name": "severity", "type": "string", "required": false},
      {"name": "body", "type": "string", "required": false},
      {"name": "destinations", "type": "array", "required": false},
      {"name": "is_enabled", "type": "boolean", "required": false}
    ]
  },
  {
    "action_id": "oci_alarm_delete",
    "action_type": "change",
    "display_name": "Delete OCI Monitoring Alarm",
    "description": "Delete an OCI Monitoring alarm by its OCID.",
    "executor": "oci.delete_alarm",
    "generic_action": "oci_alarm_delete",
    "applicable_asset_types": ["cloud_account"],
    "estimated_duration_seconds": 10,
    "execution_tier": 1,
    "parameters": [{"name": "alarm_id", "type": "string", "required": true}]
  },
  {
    "action_id": "oci_logging_enable",
    "action_type": "change",
    "display_name": "Enable OCI Logging",
    "description": "Create an OCI Log Group and Log to enable compartment-level logging.",
    "executor": "oci.enable_logging",
    "generic_action": "oci_logging_enable",
    "rollback_action": "disable_logging",
    "rollback_connector_type": "oci",
    "applicable_asset_types": ["cloud_account"],
    "estimated_duration_seconds": 30,
    "execution_tier": 1,
    "parameters": [
      {"name": "compartment_id", "type": "string", "required": true},
      {"name": "log_group_name", "type": "string", "required": false},
      {"name": "log_name", "type": "string", "required": false},
      {"name": "log_type", "type": "string", "required": false},
      {"name": "is_enabled", "type": "boolean", "required": false},
      {"name": "retention_duration", "type": "integer", "required": false}
    ]
  }
  ```

---

### Task 8: DB migration 044, ChangeType enum, and safety engine

**Files:**
- Create: `backend/alembic/versions/044_add_oci_database_change_types.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`

- [ ] Step 1: Confirm the most recent migration number. Look in `backend/alembic/versions/` and find the highest number. As of the reference files the last is `039`. If sub-projects 1-4 have added 040-043, chain off the actual highest. Assume revision `043` is `down_revision` unless a higher file exists.

- [ ] Step 2: Create `044_add_oci_database_change_types.py`:
  ```python
  """add OCI database and observability change types

  Revision ID: 044
  Revises: 043
  Create Date: 2026-05-10
  """
  from alembic import op

  revision = '044'
  down_revision = '043'
  branch_labels = None
  depends_on = None


  def upgrade():
      for t in [
          'oci_adb_create',
          'oci_adb_stop',
          'oci_adb_start',
          'oci_adb_delete',
          'oci_adb_backup',
          'oci_mysql_create',
          'oci_mysql_stop',
          'oci_mysql_start',
          'oci_mysql_delete',
          'oci_alarm_create',
          'oci_alarm_delete',
          'oci_logging_enable',
      ]:
          op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


  def downgrade():
      pass
  ```
  Note: scan `backend/alembic/versions/` for the actual highest revision and set `down_revision` to that value.

- [ ] Step 3: In `backend/app/models/change_request.py`, add the 12 new OCI values to the `ChangeType` enum after the last existing entry (currently `ip_campaign`). Add a comment header:
  ```python
      # OCI Sub-project 5 — Autonomous Database, MySQL HeatWave, Monitoring, Logging
      oci_adb_create = "oci_adb_create"
      oci_adb_stop = "oci_adb_stop"
      oci_adb_start = "oci_adb_start"
      oci_adb_delete = "oci_adb_delete"
      oci_adb_backup = "oci_adb_backup"
      oci_mysql_create = "oci_mysql_create"
      oci_mysql_stop = "oci_mysql_stop"
      oci_mysql_start = "oci_mysql_start"
      oci_mysql_delete = "oci_mysql_delete"
      oci_alarm_create = "oci_alarm_create"
      oci_alarm_delete = "oci_alarm_delete"
      oci_logging_enable = "oci_logging_enable"
  ```

- [ ] Step 4: In `backend/app/services/safety_engine.py`, add all 12 new change types to `_IMPLICIT_ROLLBACK_TYPES`. They are now proper enum members, so use the enum form. Add them after the `agent_containerize_auto` line (currently last item before the closing `}`):
  ```python
      ChangeType.oci_adb_create, ChangeType.oci_adb_stop, ChangeType.oci_adb_start,
      ChangeType.oci_adb_delete, ChangeType.oci_adb_backup,
      ChangeType.oci_mysql_create, ChangeType.oci_mysql_stop, ChangeType.oci_mysql_start,
      ChangeType.oci_mysql_delete,
      ChangeType.oci_alarm_create, ChangeType.oci_alarm_delete,
      ChangeType.oci_logging_enable,
  ```

---

### Task 9: Frontend extensions — api.ts, CreateChangeRequest.tsx, AssetDetail quick actions

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`
- Modify: `frontend/src/pages/AssetDetail.tsx` (or equivalent asset detail component — confirm actual filename)

- [ ] Step 1: In `frontend/src/types/api.ts`, add the 12 new OCI change type string literals to the `ChangeType` union type, after the last existing entry (`"suppress"`):
  ```typescript
    | "oci_adb_create"
    | "oci_adb_stop"
    | "oci_adb_start"
    | "oci_adb_delete"
    | "oci_adb_backup"
    | "oci_mysql_create"
    | "oci_mysql_stop"
    | "oci_mysql_start"
    | "oci_mysql_delete"
    | "oci_alarm_create"
    | "oci_alarm_delete"
    | "oci_logging_enable"
  ```

- [ ] Step 2: In `frontend/src/pages/CreateChangeRequest.tsx`, locate the `CHANGE_TYPE_META` object and add entries for all 12 new change types. Group them under an "Oracle Cloud" category. If a `CATEGORY_MAP` or equivalent grouping structure already exists from sub-projects 1-4, add to the OCI group; otherwise add the entries directly. All outcome templates must pre-populate sensible defaults including passwords that meet OCI complexity requirements (12+ chars, upper, lower, number, special char — `"Nexplane1234!"` is the canonical default):

  ```typescript
  oci_adb_create: {
    label: "Create Autonomous Database",
    description: "Provision an OCI Autonomous Database (Always Free tier). ADB provisioning takes up to 15 minutes.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-adb",
      db_name: "nexplaneadb",
      admin_password: "Nexplane1234!",
      db_workload: "OLTP",
      cpu_core_count: 1,
      data_storage_size_in_tbs: 1,
      is_auto_scaling_enabled: false,
      is_free_tier: true,
      license_model: "LICENSE_INCLUDED",
      rollback_strategy: "oci_adb_delete",
    }, null, 2),
  },
  oci_adb_stop: {
    label: "Stop Autonomous Database",
    description: "Stop a running OCI Autonomous Database instance.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      rollback_strategy: "oci_adb_start",
    }, null, 2),
  },
  oci_adb_start: {
    label: "Start Autonomous Database",
    description: "Start a stopped OCI Autonomous Database instance.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      rollback_strategy: "oci_adb_stop",
    }, null, 2),
  },
  oci_adb_delete: {
    label: "Delete Autonomous Database",
    description: "Permanently terminate an OCI Autonomous Database instance. This action is irreversible.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_adb_backup: {
    label: "Create ADB Manual Backup",
    description: "Create a manual backup of an OCI Autonomous Database. Backup activation can take up to 30 minutes.",
    outcomeTemplate: JSON.stringify({
      db_id: "",
      display_name: "nexplane-backup",
      rollback_strategy: "delete_backup",
    }, null, 2),
  },
  oci_mysql_create: {
    label: "Create MySQL HeatWave DB System",
    description: "Provision an OCI MySQL HeatWave DB System. MySQL provisioning takes up to 20 minutes.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-mysql",
      admin_username: "nexplane",
      admin_password: "Nexplane1234!",
      shape_name: "MySQL.VM.Standard.E4.1.8GB",
      mysql_version: "8.0.36",
      subnet_id: "",
      data_storage_size_in_gbs: 50,
      availability_domain: "",
      rollback_strategy: "oci_mysql_delete",
    }, null, 2),
  },
  oci_mysql_stop: {
    label: "Stop MySQL HeatWave DB System",
    description: "Stop a running OCI MySQL HeatWave DB System.",
    outcomeTemplate: JSON.stringify({
      db_system_id: "",
      rollback_strategy: "oci_mysql_start",
    }, null, 2),
  },
  oci_mysql_start: {
    label: "Start MySQL HeatWave DB System",
    description: "Start a stopped OCI MySQL HeatWave DB System.",
    outcomeTemplate: JSON.stringify({
      db_system_id: "",
      rollback_strategy: "oci_mysql_stop",
    }, null, 2),
  },
  oci_mysql_delete: {
    label: "Delete MySQL HeatWave DB System",
    description: "Delete an OCI MySQL HeatWave DB System. This action is irreversible.",
    outcomeTemplate: JSON.stringify({
      db_system_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_alarm_create: {
    label: "Create OCI Monitoring Alarm",
    description: "Create an OCI Monitoring alarm. Uses MQL query syntax — e.g. CpuUtilization[1m].mean() > 80.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-alarm",
      namespace: "oci_computeagent",
      query: "CpuUtilization[1m].mean() > 80",
      severity: "CRITICAL",
      body: "CPU utilization exceeded 80%",
      destinations: [],
      is_enabled: true,
      rollback_strategy: "oci_alarm_delete",
    }, null, 2),
  },
  oci_alarm_delete: {
    label: "Delete OCI Monitoring Alarm",
    description: "Delete an OCI Monitoring alarm by its OCID.",
    outcomeTemplate: JSON.stringify({
      alarm_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_logging_enable: {
    label: "Enable OCI Logging",
    description: "Create an OCI Log Group and Log to enable compartment-level audit logging.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      log_group_name: "nexplane-logs",
      log_name: "nexplane-audit-log",
      log_type: "AUDIT",
      is_enabled: true,
      retention_duration: 30,
      rollback_strategy: "disable_logging",
    }, null, 2),
  },
  ```

- [ ] Step 3: In `frontend/src/pages/AssetDetail.tsx` (confirm exact filename — may be `AssetDetail.tsx` or similar), add quick action buttons for OCI database assets. Find where quick action buttons are rendered for `database` type assets. After the existing database quick actions, add OCI-specific blocks:

  For `database` assets where `asset_metadata.db_id` is present (ADB assets — tagged `autonomous-database`):
  - Quick action: "Stop ADB" → creates CR with change_type `oci_adb_stop`, pre-fills `db_id` from `asset.asset_metadata.db_id`
  - Quick action: "Start ADB" → creates CR with change_type `oci_adb_start`, pre-fills `db_id`
  - Quick action: "Backup ADB" → creates CR with change_type `oci_adb_backup`, pre-fills `db_id`
  - Quick action: "Delete ADB" → creates CR with change_type `oci_adb_delete`, pre-fills `db_id` (show confirmation warning)

  For `database` assets where `asset_metadata.db_system_id` is present (MySQL assets — tagged `mysql`):
  - Quick action: "Stop MySQL" → creates CR with change_type `oci_mysql_stop`, pre-fills `db_system_id`
  - Quick action: "Start MySQL" → creates CR with change_type `oci_mysql_start`, pre-fills `db_system_id`
  - Quick action: "Delete MySQL" → creates CR with change_type `oci_mysql_delete`, pre-fills `db_system_id` (show confirmation warning)

  Detection pattern: check `asset.tags.includes("autonomous-database")` for ADB, `asset.tags.includes("mysql")` and `asset.asset_metadata.db_system_id` for MySQL. If `AssetDetail.tsx` already has a quick-action CR creation helper, reuse it; otherwise follow the same pattern used for any existing quick action CR buttons in the file.

- [ ] Step 4: Restart the frontend container after all frontend file changes:
  ```
  docker compose stop frontend && docker compose up frontend -d
  ```

---

### Task 10: Smoke tests for OCI_M (ADB) and OCI_N (Monitoring + Logging)

**Files:**
- Confirm the smoke test file location by looking for existing OCI smoke test files added in sub-projects 1-4 (likely in `tests/smoke/` or `backend/tests/smoke/`). Add new phases to the existing OCI smoke test file, or create `tests/smoke/test_oci_database_observability.py` if no OCI smoke test file exists yet.

- [ ] Step 1: Locate existing OCI smoke test file. Search for `oci` in `tests/smoke/` or `backend/tests/smoke/`. If found, append phases OCI_M and OCI_N to it. If not found, create `tests/smoke/test_oci_database_observability.py`.

- [ ] Step 2: Implement Phase OCI_M — Autonomous Database lifecycle. Follow the rollback-stack pattern: use Nexplane rollback as primary cleanup, OCI SDK verify-and-delete as safety net:
  ```python
  """
  Phase OCI_M: Autonomous Database lifecycle smoke test.
  Requires OCI credentials in environment / connector.
  ADB provisioning is slow — allow up to 20 min per step.
  """
  import pytest
  import time


  @pytest.mark.smoke
  @pytest.mark.oci
  @pytest.mark.slow
  def test_oci_m_adb_lifecycle(oci_connector, nexplane_client, compartment_id):
      """Full ADB lifecycle: create → stop → start → backup → delete."""
      created_db_id = None

      # 1. Create ADB
      cr = nexplane_client.create_change_request(
          change_type="oci_adb_create",
          parameters={
              "compartment_id": compartment_id,
              "display_name": "nexplane-smoke-adb",
              "db_name": "smokeadb",
              "admin_password": "Nexplane1234!",
              "db_workload": "OLTP",
              "cpu_core_count": 1,
              "data_storage_size_in_tbs": 1,
              "is_free_tier": True,
              "license_model": "LICENSE_INCLUDED",
          },
      )
      result = nexplane_client.wait_for_completion(cr["id"], timeout=1200)
      assert result["status"] == "completed"
      created_db_id = result["execution_result"]["db_id"]

      # Verify database asset in inventory
      assets = nexplane_client.list_assets(asset_type="database", tag="autonomous-database")
      assert any(a["asset_metadata"].get("db_id") == created_db_id for a in assets)
      assert result["execution_result"].get("connection_strings")

      try:
          # 2. Stop ADB
          cr = nexplane_client.create_change_request(
              change_type="oci_adb_stop",
              parameters={"db_id": created_db_id},
          )
          result = nexplane_client.wait_for_completion(cr["id"], timeout=600)
          assert result["status"] == "completed"
          # SDK verify STOPPED
          import oci
          db_client = oci.database.DatabaseClient(_get_oci_config(oci_connector))
          info = db_client.get_autonomous_database(autonomous_database_id=created_db_id).data
          assert info.lifecycle_state == "STOPPED"

          # 3. Start ADB
          cr = nexplane_client.create_change_request(
              change_type="oci_adb_start",
              parameters={"db_id": created_db_id},
          )
          result = nexplane_client.wait_for_completion(cr["id"], timeout=600)
          assert result["status"] == "completed"
          info = db_client.get_autonomous_database(autonomous_database_id=created_db_id).data
          assert info.lifecycle_state == "AVAILABLE"

          # 4. Backup ADB
          cr = nexplane_client.create_change_request(
              change_type="oci_adb_backup",
              parameters={"db_id": created_db_id, "display_name": "nexplane-smoke-backup"},
          )
          result = nexplane_client.wait_for_completion(cr["id"], timeout=1800)
          assert result["status"] == "completed"
          backup_id = result["execution_result"]["backup_id"]
          backup_info = db_client.get_autonomous_database_backup(
              autonomous_database_backup_id=backup_id
          ).data
          assert backup_info.lifecycle_state == "ACTIVE"
          # Rollback backup via Nexplane
          nexplane_client.rollback(cr["id"])

      finally:
          # 5. Delete ADB (primary cleanup via Nexplane)
          try:
              del_cr = nexplane_client.create_change_request(
                  change_type="oci_adb_delete",
                  parameters={"db_id": created_db_id},
              )
              nexplane_client.wait_for_completion(del_cr["id"], timeout=600)
          except Exception:
              # Safety net: delete directly via OCI SDK
              try:
                  import oci
                  db_client = oci.database.DatabaseClient(_get_oci_config(oci_connector))
                  db_client.delete_autonomous_database(autonomous_database_id=created_db_id)
              except Exception:
                  pass
  ```

- [ ] Step 3: Implement Phase OCI_N — Monitoring alarm and Logging:
  ```python
  @pytest.mark.smoke
  @pytest.mark.oci
  def test_oci_n_alarm_lifecycle(oci_connector, nexplane_client, compartment_id):
      """Alarm: create → verify active → delete."""
      alarm_id = None
      try:
          cr = nexplane_client.create_change_request(
              change_type="oci_alarm_create",
              parameters={
                  "compartment_id": compartment_id,
                  "display_name": "nexplane-smoke-alarm",
                  "namespace": "oci_computeagent",
                  "query": "CpuUtilization[1m].mean() > 80",
                  "severity": "CRITICAL",
                  "body": "Smoke test alarm",
                  "destinations": [],
                  "is_enabled": True,
              },
          )
          result = nexplane_client.wait_for_completion(cr["id"], timeout=60)
          assert result["status"] == "completed"
          alarm_id = result["execution_result"]["alarm_id"]

          import oci
          mon_client = oci.monitoring.MonitoringClient(_get_oci_config(oci_connector))
          alarm_info = mon_client.get_alarm(alarm_id=alarm_id).data
          assert alarm_info.lifecycle_state == "ACTIVE"

          # Delete via Nexplane rollback
          nexplane_client.rollback(cr["id"])
          alarm_id = None  # Nullify so finally block skips SDK delete
      finally:
          if alarm_id:
              # Safety net
              try:
                  import oci
                  mon_client = oci.monitoring.MonitoringClient(_get_oci_config(oci_connector))
                  mon_client.delete_alarm(alarm_id=alarm_id)
              except Exception:
                  pass


  @pytest.mark.smoke
  @pytest.mark.oci
  def test_oci_n_logging_enable(oci_connector, nexplane_client, compartment_id):
      """Logging: enable → verify log group + log → rollback."""
      log_group_id = None
      log_id = None
      try:
          cr = nexplane_client.create_change_request(
              change_type="oci_logging_enable",
              parameters={
                  "compartment_id": compartment_id,
                  "log_group_name": "nexplane-smoke-logs",
                  "log_name": "nexplane-smoke-audit-log",
                  "log_type": "AUDIT",
                  "is_enabled": True,
                  "retention_duration": 30,
              },
          )
          result = nexplane_client.wait_for_completion(cr["id"], timeout=120)
          assert result["status"] == "completed"
          log_group_id = result["execution_result"]["log_group_id"]
          log_id = result["execution_result"]["log_id"]

          import oci
          log_client = oci.logging.LoggingManagementClient(_get_oci_config(oci_connector))
          grp = log_client.get_log_group(log_group_id=log_group_id).data
          assert grp is not None
          lg = log_client.get_log(log_group_id=log_group_id, log_id=log_id).data
          assert lg is not None

          # Rollback via Nexplane
          nexplane_client.rollback(cr["id"])
          log_group_id = None
          log_id = None
      finally:
          if log_group_id:
              try:
                  import oci
                  log_client = oci.logging.LoggingManagementClient(_get_oci_config(oci_connector))
                  if log_id:
                      log_client.delete_log(log_group_id=log_group_id, log_id=log_id)
                  log_client.delete_log_group(log_group_id=log_group_id)
              except Exception:
                  pass
  ```

  Note: `_get_oci_config` is a helper that converts the connector credentials to an OCI config dict; reuse the version defined in sub-projects 1-4 if it exists, or add a local version.

- [ ] Step 4: MySQL smoke test is **skipped by default** (slow provisioning, costly on paid tiers). Add an `--include-slow` flag guard consistent with how other slow tests are marked in the codebase, alongside Phase J (RDS slow tests).

---

### Task 11: Commit all changes

**Files:** All files created and modified in Tasks 1-10.

- [ ] Step 1: Verify no Python syntax errors by running a quick import check in the backend container (or local env):
  ```
  python -c "import ast, pathlib; [ast.parse(p.read_text()) for p in pathlib.Path('backend/app/connectors/executors/oci').glob('*.py')]"
  ```

- [ ] Step 2: Verify the frontend TypeScript compiles without errors (optional if container restart in Task 9 succeeded):
  ```
  docker compose exec frontend npx tsc --noEmit
  ```

- [ ] Step 3: Stage and commit all changes:
  ```
  git add \
    backend/app/connectors/executors/oci/discover_adb.py \
    backend/app/connectors/executors/oci/discover_mysql.py \
    backend/app/connectors/executors/oci/discover_alarms.py \
    backend/app/connectors/executors/oci/create_adb.py \
    backend/app/connectors/executors/oci/stop_adb.py \
    backend/app/connectors/executors/oci/start_adb.py \
    backend/app/connectors/executors/oci/delete_adb.py \
    backend/app/connectors/executors/oci/backup_adb.py \
    backend/app/connectors/executors/oci/create_mysql.py \
    backend/app/connectors/executors/oci/stop_mysql.py \
    backend/app/connectors/executors/oci/start_mysql.py \
    backend/app/connectors/executors/oci/delete_mysql.py \
    backend/app/connectors/executors/oci/create_alarm.py \
    backend/app/connectors/executors/oci/delete_alarm.py \
    backend/app/connectors/executors/oci/enable_logging.py \
    backend/app/connectors/catalog/oci.json \
    backend/alembic/versions/044_add_oci_database_change_types.py \
    backend/app/models/change_request.py \
    backend/app/services/safety_engine.py \
    frontend/src/types/api.ts \
    frontend/src/pages/CreateChangeRequest.tsx \
    frontend/src/pages/AssetDetail.tsx

  git commit -m "feat: OCI sub-project 5 — Autonomous Database, MySQL HeatWave, Monitoring alarms, and Logging executors"
  ```
  Include any additional files touched (e.g. `_client.py`, smoke test file).
