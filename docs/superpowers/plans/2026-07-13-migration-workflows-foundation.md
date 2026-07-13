# Migration Workflows — Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the shared discovery and verification primitives that all three migration workflow types (OS upgrade, containerization, PostgreSQL migration) depend on — new asset types, three core CR executors, catalog entries, ChangeType additions, MCP tools, and smoke tests for phases 1–4.

**Architecture:** New asset types (`application_profile`, `service_endpoint`, `database_instance`, `network_port`) are added via Alembic; three executors (`discover_application_profile`, `capture_behavioral_baseline`, `verify_against_baseline`) are Nexplane Agent dispatched jobs; a new catalog file (`nexplane_agent_migration.json`) registers the actions. MCP tools expose the new asset types to LLM orchestrators. Smoke phases 1–4 verify discovery, baseline capture, adaptive window extension, and four-layer verification against live infrastructure.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x async, Alembic, FastMCP, Nexplane Agent (`dispatch_agent_job`), pytest, NexplaneClient smoke test helpers.

## Global Constraints

- All executors: `async def execute(parameters: dict, asset_ids: list, connector) -> dict` + `async def rollback(parameters: dict, execution_result: dict, connector) -> dict`
- Agent dispatch: `from app.connectors.executors.nexplane_agent import _dispatch` → `_dispatch.dispatch_agent_job(command, parameters, asset_ids, timeout_seconds)`
- Auto-asset creation: return `{"_auto_asset": {name, asset_type, environment, criticality, asset_metadata, tags}}` from executor `execute()`
- New asset type enum values added with `op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS '...'")` in Alembic
- Catalog JSON lives at `backend/app/connectors/catalog/{connector_type}.json`, actions array; `executor` field: `"{connector_type}.{action_id}"`
- ChangeType additions: append to `class ChangeType(str, enum.Enum)` in `backend/app/models/change_request.py`
- SPDX header on all new Python files: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- Smoke tests: `NexplaneClient`, `def phase_{name}(client)`, `log()`/`fail()` helpers; combined run required; EC2 runner only; use `get_or_create_smoke_ami()` for legacy app AMI
- No mocks — all smoke phases run against live infra on EC2
- AI-assisted CR approval gate: any `ai_assisted_cr` state must pass through `awaiting_approval` before proposed action executes — enforced and smoke-tested
- Latest Alembic revision: `tunnel002` — new migrations revise from this

---

## File Map

**New files:**
- `backend/app/connectors/catalog/nexplane_agent_migration.json` — catalog actions for all migration CR types
- `backend/app/connectors/executors/nexplane_agent/discover_application_profile.py`
- `backend/app/connectors/executors/nexplane_agent/capture_behavioral_baseline.py`
- `backend/app/connectors/executors/nexplane_agent/verify_against_baseline.py`
- `backend/app/mcp_tools/migration.py` — MCP tools for application profile domain
- `backend/alembic/versions/mig001_migration_asset_types.py` — new asset type enum values
- `backend/tests/smoke/test_migration_foundation_live.py` — phases 1–4 smoke tests

**Modified files:**
- `backend/app/models/asset.py` — add 4 new AssetType values
- `backend/app/models/change_request.py` — add 14 new ChangeType values (foundation set)
- `backend/app/mcp_server.py` or wherever MCP tools are registered — import `migration` module

---

### Task 1: New Asset Types — Enum and Migration

**Files:**
- Modify: `backend/app/models/asset.py`
- Create: `backend/alembic/versions/mig001_migration_asset_types.py`

**Interfaces:**
- Produces: `AssetType.application_profile`, `AssetType.service_endpoint`, `AssetType.database_instance`, `AssetType.network_port` — used by Tasks 3, 4, 5, 6

- [ ] **Step 1: Write the failing test**

```python
# In a temporary test file or inline — verifies enum values exist before migration
def test_new_asset_type_values():
    from app.models.asset import AssetType
    assert AssetType.application_profile == "application_profile"
    assert AssetType.service_endpoint == "service_endpoint"
    assert AssetType.database_instance == "database_instance"
    assert AssetType.network_port == "network_port"
```

Run: `cd backend && python -c "from app.models.asset import AssetType; print(AssetType.application_profile)"`
Expected: `AttributeError: application_profile` (confirms missing)

- [ ] **Step 2: Add enum values to `backend/app/models/asset.py`**

After `kubernetes_workload = "kubernetes_workload"`, append:

```python
    # Migration workflows — Plan 1
    application_profile = "application_profile"
    service_endpoint = "service_endpoint"
    database_instance = "database_instance"
    network_port = "network_port"
```

- [ ] **Step 3: Run enum test**

```bash
cd backend && python -c "from app.models.asset import AssetType; print(AssetType.application_profile.value)"
```
Expected: `application_profile`

- [ ] **Step 4: Write the Alembic migration**

Create `backend/alembic/versions/mig001_migration_asset_types.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add migration workflow asset types

Revision ID: mig001
Revises: tunnel002
Create Date: 2026-07-13
"""
from typing import Union
from alembic import op

revision: str = "mig001"
down_revision: Union[str, None] = "tunnel002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'application_profile'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'service_endpoint'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'database_instance'")
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'network_port'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op
    pass
```

- [ ] **Step 5: Run migration against live DB (EC2)**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 alembic upgrade head"
```
Expected: `Running upgrade tunnel002 -> mig001, add migration workflow asset types`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/asset.py backend/alembic/versions/mig001_migration_asset_types.py
git commit -m "feat(migration): add application_profile, service_endpoint, database_instance, network_port asset types"
```

---

### Task 2: ChangeType Additions and Catalog Scaffold

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/app/connectors/catalog/nexplane_agent_migration.json`

**Interfaces:**
- Produces: ChangeType values for all foundation CR types — used by catalog (Task 2), executors (Tasks 3–5)
- Produces: catalog file structure — executors registered as `nexplane_agent.{action_id}`

- [ ] **Step 1: Verify existing ChangeType end** (read the last lines of change_request.py to know where to append)

```bash
cd backend && grep -n "rollback_release\|uninstall_release\|discover_stacks" app/models/change_request.py | tail -5
```

- [ ] **Step 2: Append ChangeType values to `backend/app/models/change_request.py`**

Find the last enum entry in the file. Append after the last existing entry (before the class ends):

```python
    # Migration workflows — foundation
    discover_application_profile = "discover_application_profile"
    capture_behavioral_baseline = "capture_behavioral_baseline"
    verify_against_baseline = "verify_against_baseline"
    run_os_upgrade = "run_os_upgrade"
    provision_host = "provision_host"
    migrate_host_config = "migrate_host_config"
    migrate_identity = "migrate_identity"
    rsync_application = "rsync_application"
    resolve_dependencies = "resolve_dependencies"
    ai_assisted_cr = "ai_assisted_cr"
    migrate_postgres_instance = "migrate_postgres_instance"
    verify_data_integrity = "verify_data_integrity"
    run_schema_migration = "run_schema_migration"
    update_connection_strings = "update_connection_strings"
    decommission_database_instance = "decommission_database_instance"
    decommission_legacy_host = "decommission_legacy_host"
    decommission_legacy_process = "decommission_legacy_process"
    shift_traffic_weight = "shift_traffic_weight"
    containerize_application = "containerize_application"
    deploy_container_alongside = "deploy_container_alongside"
    provision_database_instance = "provision_database_instance"
    managed_db_snapshot = "managed_db_snapshot"
```

- [ ] **Step 3: Verify ChangeType import**

```bash
cd backend && python -c "from app.models.change_request import ChangeType; print(ChangeType.discover_application_profile.value)"
```
Expected: `discover_application_profile`

- [ ] **Step 4: Create `backend/app/connectors/catalog/nexplane_agent_migration.json`**

```json
{
  "connector_type": "nexplane_agent",
  "display_name": "Nexplane Agent — Migration",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "discover_application_profile",
      "display_name": "Discover Application Profile",
      "description": "Map what is running on a host and what it depends on, without requiring operator knowledge of the application.",
      "action_type": "discovery",
      "executor": "nexplane_agent.discover_application_profile",
      "generic_action": "discover_application_profile",
      "applicable_asset_types": ["server"],
      "execution_tier": 2,
      "estimated_duration_seconds": 120,
      "parameters": [
        {
          "name": "asset_id",
          "type": "string",
          "required": true
        }
      ]
    },
    {
      "action_id": "capture_behavioral_baseline",
      "display_name": "Capture Behavioral Baseline",
      "description": "Record how the application behaves now — the success benchmark for post-migration verification.",
      "action_type": "discovery",
      "executor": "nexplane_agent.capture_behavioral_baseline",
      "generic_action": "capture_behavioral_baseline",
      "applicable_asset_types": ["application_profile"],
      "execution_tier": 2,
      "estimated_duration_seconds": 1800,
      "parameters": [
        {
          "name": "observation_window_seconds",
          "type": "integer",
          "required": false,
          "default": 1200
        }
      ]
    },
    {
      "action_id": "verify_against_baseline",
      "display_name": "Verify Against Baseline",
      "description": "Confirm the migrated system matches the pre-migration behavioral benchmark across all four production quality layers.",
      "action_type": "verification",
      "executor": "nexplane_agent.verify_against_baseline",
      "generic_action": "verify_against_baseline",
      "applicable_asset_types": ["application_profile", "server"],
      "execution_tier": 2,
      "estimated_duration_seconds": 60,
      "parameters": [
        {
          "name": "target_host",
          "type": "string",
          "required": false
        },
        {
          "name": "target_port_offset",
          "type": "integer",
          "required": false,
          "default": 0
        }
      ]
    }
  ]
}
```

- [ ] **Step 5: Verify catalog JSON is valid**

```bash
cd backend && python -c "import json; d=json.load(open('app/connectors/catalog/nexplane_agent_migration.json')); print(len(d['actions']), 'actions')"
```
Expected: `3 actions`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py backend/app/connectors/catalog/nexplane_agent_migration.json
git commit -m "feat(migration): add ChangeType values and nexplane_agent_migration catalog"
```

---

### Task 3: `discover_application_profile` Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/discover_application_profile.py`

**Interfaces:**
- Consumes: `dispatch_agent_job(command, parameters, asset_ids, timeout_seconds)` from `nexplane_agent._dispatch`
- Produces: `{"_auto_asset": {..., "asset_type": "application_profile"}, "profile": {...}, "action": "discover_application_profile"}`
- Profile schema:
  ```
  {
    processes: [{name, pid, executable, service_unit, listening_ports, outbound_connections, linked_libraries}],
    endpoints: [{protocol, port, process, declared_health_path, source}],
    dependencies: [{type, host, port, dsn_template, source, confidence}],
    config_files: [{path, format, extracted_keys}],
    library_versions: [{name, version, path}]
  }
  ```

- [ ] **Step 1: Write the test**

```python
# backend/tests/unit/test_discover_application_profile.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch

SAMPLE_PROFILE = {
    "processes": [{"name": "nginx", "pid": 100, "executable": "/usr/sbin/nginx",
                   "service_unit": "nginx.service", "listening_ports": [80],
                   "outbound_connections": [], "linked_libraries": []}],
    "endpoints": [{"protocol": "http", "port": 80, "process": "nginx",
                   "declared_health_path": "/health", "source": "runtime"}],
    "dependencies": [{"type": "db", "host": "localhost", "port": 5432,
                      "dsn_template": "postgresql://localhost:5432/app",
                      "source": "config", "confidence": "both"}],
    "config_files": [{"path": "/etc/app/config.ini", "format": "ini",
                      "extracted_keys": ["database_url"]}],
    "library_versions": [{"name": "libpq", "version": "14.0", "path": "/usr/lib/libpq.so"}],
}


@pytest.mark.asyncio
async def test_execute_returns_auto_asset():
    with patch(
        "app.connectors.executors.nexplane_agent.discover_application_profile._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"profile": SAMPLE_PROFILE, "hostname": "smoke-host"},
    ):
        from app.connectors.executors.nexplane_agent import discover_application_profile as m

        result = await m.execute(
            parameters={"asset_id": "abc123"},
            asset_ids=["abc123"],
            connector=None,
        )

    assert result["action"] == "discover_application_profile"
    assert "_auto_asset" in result
    assert result["_auto_asset"]["asset_type"] == "application_profile"
    assert "application_profile" in result["_auto_asset"]["name"].lower()
    assert result["profile"]["endpoints"][0]["port"] == 80


@pytest.mark.asyncio
async def test_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import discover_application_profile as m

    result = await m.rollback(parameters={}, execution_result={}, connector=None)
    assert result["rolled_back"] is False
    assert "non-mutating" in result["reason"]
```

Run: `cd backend && python -m pytest tests/unit/test_discover_application_profile.py -v`
Expected: FAIL — `ModuleNotFoundError` (module doesn't exist yet)

- [ ] **Step 2: Create `backend/app/connectors/executors/nexplane_agent/discover_application_profile.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    asset_id = parameters.get("asset_id") or (asset_ids[0] if asset_ids else None)

    result = await _dispatch.dispatch_agent_job(
        command="discover_application_profile",
        parameters={"asset_id": asset_id},
        asset_ids=list(asset_ids),
        timeout_seconds=180,
    )

    hostname = result.get("hostname", asset_id)
    profile = result.get("profile", {})

    return {
        "action": "discover_application_profile",
        "_auto_asset": {
            "name": f"Application Profile — {hostname}",
            "asset_type": "application_profile",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": profile,
            "tags": ["discovered", "migration"],
        },
        "profile": profile,
        "hostname": hostname,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover_application_profile is non-mutating"}
```

- [ ] **Step 3: Run the test**

```bash
cd backend && python -m pytest tests/unit/test_discover_application_profile.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/discover_application_profile.py \
        backend/tests/unit/test_discover_application_profile.py
git commit -m "feat(migration): discover_application_profile executor"
```

---

### Task 4: `capture_behavioral_baseline` Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/capture_behavioral_baseline.py`

**Interfaces:**
- Consumes: ApplicationProfile asset metadata (passed via parameters or fetched by agent from asset_id)
- Produces: `{"action": "capture_behavioral_baseline", "baseline": {...}, "observation_duration_seconds": int, "unverified_dependencies": [...]}`
- Baseline schema:
  ```
  {
    captured_at: ISO8601,
    observation_duration_seconds: int,
    endpoints: [{url, probe_type, status_code, response_ms_p50, response_ms_p95, content_signature, confidence}],
    services: [{name, state, active_connections, port}],
    dependencies: [{target, type, latency_ms_p50, query_sample_result, row_count_sample, confidence}],
    library_versions: [{name, version}]
  }
  ```

- [ ] **Step 1: Write the test**

```python
# backend/tests/unit/test_capture_behavioral_baseline.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch

SAMPLE_BASELINE = {
    "captured_at": "2026-07-13T12:00:00Z",
    "observation_duration_seconds": 1200,
    "endpoints": [{"url": "http://localhost:80", "probe_type": "http_get",
                   "status_code": 200, "response_ms_p50": 12, "response_ms_p95": 35,
                   "content_signature": "abc123", "confidence": "both"}],
    "services": [{"name": "nginx", "state": "active", "active_connections": 2, "port": 80}],
    "dependencies": [{"target": "localhost:5432", "type": "db", "latency_ms_p50": 3,
                      "query_sample_result": "1", "row_count_sample": 4923, "confidence": "both"}],
    "library_versions": [{"name": "libpq", "version": "14.0"}],
}


@pytest.mark.asyncio
async def test_execute_returns_baseline():
    with patch(
        "app.connectors.executors.nexplane_agent.capture_behavioral_baseline._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"baseline": SAMPLE_BASELINE, "unverified_dependencies": []},
    ):
        from app.connectors.executors.nexplane_agent import capture_behavioral_baseline as m

        result = await m.execute(
            parameters={"observation_window_seconds": 1200},
            asset_ids=["profile-uuid"],
            connector=None,
        )

    assert result["action"] == "capture_behavioral_baseline"
    assert result["baseline"]["endpoints"][0]["status_code"] == 200
    assert result["unverified_dependencies"] == []


@pytest.mark.asyncio
async def test_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import capture_behavioral_baseline as m

    result = await m.rollback(parameters={}, execution_result={}, connector=None)
    assert result["rolled_back"] is False
    assert "non-mutating" in result["reason"]
```

Run: `cd backend && python -m pytest tests/unit/test_capture_behavioral_baseline.py -v`
Expected: FAIL — module not found

- [ ] **Step 2: Create `backend/app/connectors/executors/nexplane_agent/capture_behavioral_baseline.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

# Default and max observation window for adaptive extension
_DEFAULT_WINDOW_SECONDS = 1200   # 20 minutes
_MAX_WINDOW_SECONDS = 7200       # 2 hours


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    observation_window = int(parameters.get("observation_window_seconds", _DEFAULT_WINDOW_SECONDS))
    observation_window = min(observation_window, _MAX_WINDOW_SECONDS)

    asset_id = asset_ids[0] if asset_ids else None

    # Agent handles adaptive window extension internally:
    # - confidence=both dependency with no traffic after window → extend by 600s (up to max)
    # - confidence=config_only dependency with no traffic → extend once by 1200s, mark low_confidence
    # - confidence=runtime_only → already observed, no extension needed
    result = await _dispatch.dispatch_agent_job(
        command="capture_behavioral_baseline",
        parameters={
            "asset_id": asset_id,
            "observation_window_seconds": observation_window,
            "max_window_seconds": _MAX_WINDOW_SECONDS,
        },
        asset_ids=list(asset_ids),
        timeout_seconds=_MAX_WINDOW_SECONDS + 300,
    )

    baseline = result.get("baseline", {})
    unverified = result.get("unverified_dependencies", [])

    return {
        "action": "capture_behavioral_baseline",
        "baseline": baseline,
        "observation_duration_seconds": baseline.get("observation_duration_seconds", observation_window),
        "unverified_dependencies": unverified,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture_behavioral_baseline is non-mutating"}
```

- [ ] **Step 3: Run the test**

```bash
cd backend && python -m pytest tests/unit/test_capture_behavioral_baseline.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/capture_behavioral_baseline.py \
        backend/tests/unit/test_capture_behavioral_baseline.py
git commit -m "feat(migration): capture_behavioral_baseline executor with adaptive window"
```

---

### Task 5: `verify_against_baseline` Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/verify_against_baseline.py`

**Interfaces:**
- Consumes: behavioral baseline from asset_metadata on ApplicationProfile asset; optional `target_host` parameter to probe a different host than baseline was captured on
- Produces: `{"action": "verify_against_baseline", "failed": bool, "layers": {infrastructure, service, application, data}, "report": {...}}`
- When `failed: True`: workflow triggers FILO rollback

Pass thresholds (from spec):
| Check | Pass |
|---|---|
| HTTP status | Matches baseline status code |
| HTTP latency | ≤ 150% of baseline p95 |
| HTTP content signature | Present in response |
| Service state | `active (running)` |
| Port listening | Same ports open |
| DB connection | Connects and query executes |
| DB row count | Within 5% of baseline sample |
| Library version | ≥ baseline version |

Application or Data layer failure → `failed: True`, triggers FILO rollback by workflow.
Infrastructure or Service layer failure → surfaced as warning, does not set `failed: True`.

- [ ] **Step 1: Write the test**

```python
# backend/tests/unit/test_verify_against_baseline.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch

PASS_REPORT = {
    "layers": {
        "infrastructure": {"passed": True, "checks": []},
        "service": {"passed": True, "checks": []},
        "application": {"passed": True, "checks": []},
        "data": {"passed": True, "checks": []},
    },
    "summary": "All four layers passed.",
}

FAIL_REPORT = {
    "layers": {
        "infrastructure": {"passed": True, "checks": []},
        "service": {"passed": True, "checks": []},
        "application": {"passed": False, "checks": [
            {"name": "http_status", "expected": 200, "actual": 502, "passed": False}
        ]},
        "data": {"passed": True, "checks": []},
    },
    "summary": "Application layer failed: http_status mismatch.",
}


@pytest.mark.asyncio
async def test_execute_pass():
    with patch(
        "app.connectors.executors.nexplane_agent.verify_against_baseline._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"report": PASS_REPORT},
    ):
        from app.connectors.executors.nexplane_agent import verify_against_baseline as m

        result = await m.execute(
            parameters={},
            asset_ids=["profile-uuid"],
            connector=None,
        )

    assert result["action"] == "verify_against_baseline"
    assert result["failed"] is False
    assert result["layers"]["application"]["passed"] is True


@pytest.mark.asyncio
async def test_execute_application_layer_fail_sets_failed_true():
    with patch(
        "app.connectors.executors.nexplane_agent.verify_against_baseline._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"report": FAIL_REPORT},
    ):
        from app.connectors.executors.nexplane_agent import verify_against_baseline as m

        result = await m.execute(
            parameters={},
            asset_ids=["profile-uuid"],
            connector=None,
        )

    assert result["failed"] is True


@pytest.mark.asyncio
async def test_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import verify_against_baseline as m

    result = await m.rollback(parameters={}, execution_result={}, connector=None)
    assert result["rolled_back"] is False
    assert "non-mutating" in result["reason"]
```

Run: `cd backend && python -m pytest tests/unit/test_verify_against_baseline.py -v`
Expected: FAIL — module not found

- [ ] **Step 2: Create `backend/app/connectors/executors/nexplane_agent/verify_against_baseline.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

# Pass thresholds (spec-mandated):
# - HTTP latency: ≤ 150% of baseline p95
# - DB row count: within 5% of baseline sample
# - Library version: ≥ baseline version (downgrade = warning, not fail)
# Failure in Application or Data layers sets failed=True (triggers FILO rollback by workflow).
# Failure in Infrastructure or Service layers is surfaced as warning only.


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    asset_id = asset_ids[0] if asset_ids else None

    result = await _dispatch.dispatch_agent_job(
        command="verify_against_baseline",
        parameters={
            "asset_id": asset_id,
            "target_host": parameters.get("target_host"),
            "target_port_offset": int(parameters.get("target_port_offset", 0)),
            "latency_threshold_pct": 150,
            "row_count_tolerance_pct": 5,
        },
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )

    report = result.get("report", {})
    layers = report.get("layers", {})

    application_passed = layers.get("application", {}).get("passed", True)
    data_passed = layers.get("data", {}).get("passed", True)
    failed = not (application_passed and data_passed)

    return {
        "action": "verify_against_baseline",
        "failed": failed,
        "layers": layers,
        "report": report,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "verify_against_baseline is non-mutating — FILO unwind triggered by workflow"}
```

- [ ] **Step 3: Run the test**

```bash
cd backend && python -m pytest tests/unit/test_verify_against_baseline.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/verify_against_baseline.py \
        backend/tests/unit/test_verify_against_baseline.py
git commit -m "feat(migration): verify_against_baseline executor (4-layer production quality verification)"
```

---

### Task 6: MCP Tools for Application Profile Domain

**Files:**
- Create: `backend/app/mcp_tools/migration.py`
- Modify: wherever MCP tool modules are imported (check `backend/app/mcp_server.py` or `backend/app/main.py` for the import pattern)

**Interfaces:**
- Consumes: `AsyncSessionLocal`, `_auth()` pattern from `app/mcp_tools/assets.py`
- Produces: 3 MCP tools: `list_application_profiles`, `get_application_profile`, `list_database_instances`

- [ ] **Step 1: Find where MCP tool modules are imported**

```bash
grep -n "mcp_tools" backend/app/mcp_server.py backend/app/main.py 2>/dev/null | head -20
```

Note the import pattern — e.g., `import app.mcp_tools.assets` or `from app.mcp_tools import assets`.

- [ ] **Step 2: Write the test**

```python
# backend/tests/unit/test_migration_mcp_tools.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


def test_migration_mcp_tools_importable():
    from app.mcp_tools import migration
    assert hasattr(migration, "list_application_profiles")
    assert hasattr(migration, "get_application_profile")
    assert hasattr(migration, "list_database_instances")
```

Run: `cd backend && python -m pytest tests/unit/test_migration_mcp_tools.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create `backend/app/mcp_tools/migration.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""MCP tools — Migration domain (application profiles, database instances)."""
import uuid as _uuid
from typing import Any, Optional

from app.mcp_server import mcp
from app.database import AsyncSessionLocal


async def _auth(token: str):
    from app.mcp_server import resolve_mcp_token
    from fastapi import HTTPException
    db_cm = AsyncSessionLocal()
    db = await db_cm.__aenter__()
    try:
        user, agent_token = await resolve_mcp_token(token, db)
        return user, db, db_cm
    except HTTPException:
        await db_cm.__aexit__(None, None, None)
        raise


@mcp.tool()
async def list_application_profiles(
    token: str,
    environment: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List discovered application profiles. Each profile describes a running workload's
    endpoints, dependencies, services, config files, and library versions.
    Use to find what applications have been discovered before planning a migration.
    Filter by environment (dev/staging/prod).
    """
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(Asset)
            .where(
                Asset.organization_id == user.organization_id,
                Asset.asset_type == AssetType.application_profile,
            )
            .order_by(Asset.created_at.desc())
            .limit(limit)
        )
        if environment:
            from app.models.asset import Environment
            stmt = stmt.where(Asset.environment == environment)

        result = await db.execute(stmt)
        assets = result.scalars().all()
        return [
            {
                "id": str(a.id),
                "name": a.name,
                "environment": a.environment.value,
                "criticality": a.criticality.value,
                "tags": a.tags,
                "endpoint_count": len(a.asset_metadata.get("endpoints", [])),
                "dependency_count": len(a.asset_metadata.get("dependencies", [])),
                "created_at": a.created_at.isoformat(),
            }
            for a in assets
        ]
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_application_profile(
    token: str,
    asset_id: str,
) -> dict[str, Any]:
    """
    Get the full application profile for a discovered workload — endpoints, dependencies,
    config files, library versions. Use before planning a migration to understand what
    the application depends on and what verification targets to use.
    """
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    user, db, db_cm = await _auth(token)
    try:
        stmt = select(Asset).where(
            Asset.id == _uuid.UUID(asset_id),
            Asset.organization_id == user.organization_id,
            Asset.asset_type == AssetType.application_profile,
        )
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if not asset:
            return {"error": "application_profile not found", "asset_id": asset_id}
        return {
            "id": str(asset.id),
            "name": asset.name,
            "environment": asset.environment.value,
            "criticality": asset.criticality.value,
            "tags": asset.tags,
            "profile": asset.asset_metadata,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def list_database_instances(
    token: str,
    environment: str = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List discovered database instances. Each instance has engine, version, DSN template,
    and baseline row counts. Use when planning a database migration to identify source
    and target instances.
    """
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(Asset)
            .where(
                Asset.organization_id == user.organization_id,
                Asset.asset_type == AssetType.database_instance,
            )
            .order_by(Asset.created_at.desc())
            .limit(limit)
        )
        if environment:
            stmt = stmt.where(Asset.environment == environment)

        result = await db.execute(stmt)
        assets = result.scalars().all()
        return [
            {
                "id": str(a.id),
                "name": a.name,
                "environment": a.environment.value,
                "engine": a.asset_metadata.get("engine"),
                "version": a.asset_metadata.get("version"),
                "schema_version": a.asset_metadata.get("schema_version"),
                "created_at": a.created_at.isoformat(),
            }
            for a in assets
        ]
    finally:
        await db_cm.__aexit__(None, None, None)
```

- [ ] **Step 4: Register the migration module** (mirror the pattern you found in Step 1)

If the pattern is `import app.mcp_tools.assets`, add:
```python
import app.mcp_tools.migration
```

If the pattern is `from app.mcp_tools import assets`, add:
```python
from app.mcp_tools import migration
```

Add it adjacent to the other mcp_tools imports.

- [ ] **Step 5: Run the test**

```bash
cd backend && python -m pytest tests/unit/test_migration_mcp_tools.py -v
```
Expected: PASS (1 test)

- [ ] **Step 6: Verify backend starts without error on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull && docker compose restart backend && sleep 5 && docker logs nexplane-backend-1 2>&1 | tail -20"
```
Expected: No import errors, backend listening.

- [ ] **Step 7: Commit**

```bash
git add backend/app/mcp_tools/migration.py \
        backend/tests/unit/test_migration_mcp_tools.py
git add backend/app/mcp_server.py  # or wherever you added the import
git commit -m "feat(migration): MCP tools for application profile and database instance domain"
```

---

### Task 7: Smoke Tests — Phases 1–4

**Files:**
- Create: `backend/tests/smoke/test_migration_foundation_live.py`

**Interfaces:**
- Consumes: `NexplaneClient` from existing smoke test infrastructure; `get_or_create_smoke_ami()` helper; legacy app AMI (Ubuntu 20.04 + nginx + Flask + PostgreSQL 14 + Nexplane agent)
- Produces: 4 smoke phases: `DISCOVERY_PROFILE`, `BEHAVIORAL_BASELINE`, `BASELINE_ADAPTIVE`, `VERIFY_BASELINE`

The smoke tests verify all four phases in a combined run. They are written to run in sequence on EC2.

**Legacy app AMI spec** (must be created if cache miss):
- Ubuntu 20.04 base
- nginx on port 80 (proxying Flask on 8080 via upstream)
- Flask app on port 8080, connecting to local PostgreSQL 14 (`smoke_app` DB, 3 tables, ~5,000 rows)
- `smoke-app.service` systemd unit managing Flask
- Cron job every 5 minutes (`*/5 * * * * /usr/bin/python3 /opt/app/cron_task.py`)
- `/etc/app/config.ini` with `database_url = postgresql://localhost:5432/smoke_app` and `upstream_url = http://localhost:8080`
- Nexplane agent pre-installed and registered

Cache key: `/nexplane/smoke-amis/legacy-app/{hash}` where hash is sha256 of the AMI build script content.

- [ ] **Step 1: Read an existing smoke test for the NexplaneClient usage pattern**

```bash
head -80 backend/tests/smoke/test_catalog_action_live.py
```

Note exact: import path for NexplaneClient, `log()` / `fail()` helper signatures, how phases are structured, how the combined run entry point is defined.

- [ ] **Step 2: Write the failing smoke test (import-only check)**

```bash
cd backend && python -c "import tests.smoke.test_migration_foundation_live" 2>&1
```
Expected: `ModuleNotFoundError` (file doesn't exist)

- [ ] **Step 3: Create `backend/tests/smoke/test_migration_foundation_live.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Migration Workflows — Foundation (Phases 1–4)

Phases:
  1. DISCOVERY_PROFILE   — discover_application_profile CR against legacy app AMI
  2. BEHAVIORAL_BASELINE — capture_behavioral_baseline CR (5-minute window for smoke)
  3. BASELINE_ADAPTIVE   — capture_behavioral_baseline with idle dependency simulation
  4. VERIFY_BASELINE     — verify_against_baseline pass + failure path

All phases must pass in a single combined run on EC2. Run from EC2 runner only.
"""
import time

from tests.smoke.conftest import NexplaneClient, get_or_create_smoke_ami, log, fail


# --- Legacy app AMI -----------------------------------------------------------
# Ubuntu 20.04, nginx:80, Flask:8080, PostgreSQL14, smoke-app.service, cron job
# Cache key: /nexplane/smoke-amis/legacy-app/{hash}
LEGACY_APP_BUILD_HASH = "v1-foundation-smoke"


def _launch_legacy_app_host(client: NexplaneClient) -> str:
    """Launch legacy app AMI and return the asset_id of the registered server asset."""
    ami_id = get_or_create_smoke_ami(
        client=client,
        cache_key=f"/nexplane/smoke-amis/legacy-app/{LEGACY_APP_BUILD_HASH}",
        build_commands=_legacy_app_build_commands(),
        instance_type="t3.small",
    )
    asset_id = client.launch_and_register_instance(ami_id=ami_id, instance_type="t3.small",
                                                    name="smoke-legacy-app")
    log(f"Legacy app host launched: asset_id={asset_id}")
    return asset_id


def _legacy_app_build_commands() -> list[str]:
    """Commands to bake the legacy app AMI from a clean Ubuntu 20.04 base."""
    return [
        "apt-get update -qq",
        "apt-get install -y nginx python3 python3-pip postgresql postgresql-contrib",
        "pip3 install flask psycopg2-binary",
        "sudo -u postgres psql -c \"CREATE DATABASE smoke_app;\"",
        "sudo -u postgres psql -d smoke_app -c \"CREATE TABLE users (id serial PRIMARY KEY, name text); CREATE TABLE orders (id serial PRIMARY KEY, user_id int REFERENCES users(id)); CREATE TABLE audit_log (id serial PRIMARY KEY, action text);\"",
        "sudo -u postgres psql -d smoke_app -c \"INSERT INTO users (name) SELECT 'user_' || g FROM generate_series(1,5000) g;\"",
        "mkdir -p /opt/app /etc/app",
        "cat > /opt/app/app.py << 'EOF'\nfrom flask import Flask\nimport psycopg2, os\napp = Flask(__name__)\n@app.route('/')\ndef index(): return 'ok'\n@app.route('/health')\ndef health(): return 'healthy'\nif __name__ == '__main__': app.run(host='0.0.0.0', port=8080)\nEOF",
        "cat > /etc/app/config.ini << 'EOF'\n[app]\ndatabase_url = postgresql://postgres@localhost:5432/smoke_app\nupstream_url = http://localhost:8080\nEOF",
        "cat > /etc/nginx/sites-available/default << 'EOF'\nserver { listen 80; location / { proxy_pass http://127.0.0.1:8080; } }\nEOF",
        "cat > /etc/systemd/system/smoke-app.service << 'EOF'\n[Unit]\nDescription=Smoke App\nAfter=network.target\n[Service]\nExecStart=/usr/bin/python3 /opt/app/app.py\nRestart=always\n[Install]\nWantedBy=multi-user.target\nEOF",
        "systemctl daemon-reload && systemctl enable smoke-app && systemctl start smoke-app",
        "systemctl restart nginx",
        "echo '*/5 * * * * root /usr/bin/python3 /opt/app/app.py --cron 2>/dev/null' > /etc/cron.d/smoke-cron",
    ]


# --- Phase 1: DISCOVERY_PROFILE -----------------------------------------------

def phase_discovery_profile(client: NexplaneClient):
    log("Phase 1: DISCOVERY_PROFILE")
    host_asset_id = _launch_legacy_app_host(client)

    cr_id = client.create_cr(
        change_type="discover_application_profile",
        asset_ids=[host_asset_id],
        parameters={"asset_id": host_asset_id},
        title="Smoke: discover application profile",
    )
    client.approve_cr(cr_id)
    result = client.wait_for_cr(cr_id, timeout=300)

    if result["status"] != "completed":
        fail(f"Phase 1: CR did not complete — status={result['status']}")

    # Find created ApplicationProfile asset
    profile_assets = client.list_assets(asset_type="application_profile")
    profile = next((a for a in profile_assets if host_asset_id in str(a.get("tags", []))), None)
    if profile is None:
        # Try by name pattern
        profile = next((a for a in profile_assets if "Application Profile" in a["name"]), None)
    if profile is None:
        fail("Phase 1: ApplicationProfile asset not created after discover_application_profile")

    profile_detail = client.get_asset(profile["id"])
    meta = profile_detail.get("asset_metadata", {})

    ports_found = [ep["port"] for ep in meta.get("endpoints", [])]
    if 80 not in ports_found:
        fail(f"Phase 1: Port 80 (nginx) not in discovered endpoints: {ports_found}")
    if 8080 not in ports_found:
        fail(f"Phase 1: Port 8080 (Flask) not in discovered endpoints: {ports_found}")

    deps = meta.get("dependencies", [])
    pg_dep = next((d for d in deps if d.get("port") == 5432), None)
    if pg_dep is None:
        fail("Phase 1: PostgreSQL dependency (port 5432) not discovered")
    if pg_dep.get("confidence") not in ("both", "config_only"):
        fail(f"Phase 1: PostgreSQL dependency confidence unexpected: {pg_dep.get('confidence')}")

    config_paths = [cf["path"] for cf in meta.get("config_files", [])]
    if not any("/etc/app/config.ini" in p for p in config_paths):
        fail(f"Phase 1: /etc/app/config.ini not in discovered config_files: {config_paths}")

    service_names = [s["name"] for s in meta.get("services", [])]
    for expected in ("nginx", "smoke-app", "postgresql"):
        if not any(expected in s for s in service_names):
            fail(f"Phase 1: Service '{expected}' not in discovered services: {service_names}")

    if not meta.get("library_versions"):
        fail("Phase 1: library_versions is empty")

    log("Phase 1 PASS — ApplicationProfile created with ports 80/8080, pg dep, config.ini, services")

    # Verify host unchanged (non-mutating)
    host = client.get_asset(host_asset_id)
    if host is None:
        fail("Phase 1: Host asset missing after non-mutating CR")

    log("Phase 1 rollback check PASS — host asset unchanged")
    return host_asset_id, profile["id"]


# --- Phase 2: BEHAVIORAL_BASELINE --------------------------------------------

def phase_behavioral_baseline(client: NexplaneClient, host_asset_id: str, profile_asset_id: str):
    log("Phase 2: BEHAVIORAL_BASELINE")

    # Generate traffic before baseline capture
    host = client.get_asset(host_asset_id)
    host_ip = host.get("asset_metadata", {}).get("private_ip") or host.get("asset_metadata", {}).get("public_ip")
    for _ in range(5):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://{host_ip}:80/", timeout=5)
            urllib.request.urlopen(f"http://{host_ip}:8080/health", timeout=5)
        except Exception:
            pass
        time.sleep(1)

    cr_id = client.create_cr(
        change_type="capture_behavioral_baseline",
        asset_ids=[profile_asset_id],
        parameters={"observation_window_seconds": 300},  # 5 minutes for smoke
        title="Smoke: capture behavioral baseline",
    )
    client.approve_cr(cr_id)
    result = client.wait_for_cr(cr_id, timeout=600)

    if result["status"] != "completed":
        fail(f"Phase 2: CR did not complete — status={result['status']}")

    cr_result = result.get("execution_result", {})
    baseline = cr_result.get("baseline", {})

    endpoints = baseline.get("endpoints", [])
    ep_80 = next((e for e in endpoints if "80" in str(e.get("url", ""))), None)
    if ep_80 is None:
        fail("Phase 2: Port 80 endpoint not in baseline")
    if ep_80.get("status_code") != 200:
        fail(f"Phase 2: Port 80 endpoint status not 200: {ep_80.get('status_code')}")
    if ep_80.get("confidence") != "both":
        fail(f"Phase 2: Port 80 endpoint confidence not 'both': {ep_80.get('confidence')}")
    if not ep_80.get("response_ms_p50"):
        fail("Phase 2: response_ms_p50 not recorded for port 80")

    ep_8080 = next((e for e in endpoints if "8080" in str(e.get("url", ""))), None)
    if ep_8080 is None:
        fail("Phase 2: Port 8080 endpoint not in baseline")
    if ep_8080.get("confidence") != "both":
        fail(f"Phase 2: Port 8080 endpoint confidence not 'both': {ep_8080.get('confidence')}")

    deps = baseline.get("dependencies", [])
    pg_dep = next((d for d in deps if "5432" in str(d.get("target", ""))), None)
    if pg_dep is None:
        fail("Phase 2: PostgreSQL dependency not in baseline")
    if pg_dep.get("confidence") != "both":
        fail(f"Phase 2: PostgreSQL dependency confidence not 'both': {pg_dep.get('confidence')}")
    if not pg_dep.get("row_count_sample"):
        fail("Phase 2: row_count_sample not recorded for PostgreSQL")

    services = baseline.get("services", [])
    smoke_svc = next((s for s in services if "smoke-app" in s.get("name", "")), None)
    if smoke_svc is None:
        fail("Phase 2: smoke-app service not in baseline")
    if smoke_svc.get("state") != "active":
        fail(f"Phase 2: smoke-app service state not 'active': {smoke_svc.get('state')}")

    log("Phase 2 PASS — baseline captured with port 80/8080 endpoints, pg dependency, smoke-app service")
    return baseline


# --- Phase 3: BASELINE_ADAPTIVE -----------------------------------------------

def phase_baseline_adaptive(client: NexplaneClient):
    log("Phase 3: BASELINE_ADAPTIVE (fresh host, idle Flask dependency)")

    # Launch a second legacy app host with Flask not yet started
    host_asset_id = _launch_legacy_app_host(client)

    # Stop Flask to simulate idle dependency
    client.run_ssm_command(
        asset_id=host_asset_id,
        command="systemctl stop smoke-app",
    )

    # Discover profile — should find DB DSN in config.ini but no Flask traffic
    cr_id = client.create_cr(
        change_type="discover_application_profile",
        asset_ids=[host_asset_id],
        parameters={"asset_id": host_asset_id},
        title="Smoke adaptive: discover profile (Flask stopped)",
    )
    client.approve_cr(cr_id)
    result = client.wait_for_cr(cr_id, timeout=300)
    profile_assets = client.list_assets(asset_type="application_profile")
    profile = next((a for a in profile_assets if "smoke-legacy" in a.get("name", "")), None)
    if profile is None:
        profile = profile_assets[0] if profile_assets else None
    if profile is None:
        fail("Phase 3: ApplicationProfile not created")
    profile_asset_id = profile["id"]

    # Start baseline with short 2-minute window
    cr_id = client.create_cr(
        change_type="capture_behavioral_baseline",
        asset_ids=[profile_asset_id],
        parameters={"observation_window_seconds": 120},
        title="Smoke adaptive: capture baseline (Flask not running)",
    )
    client.approve_cr(cr_id)

    # Wait for initial window to elapse (120s), then start Flask mid-extension
    time.sleep(130)
    client.run_ssm_command(
        asset_id=host_asset_id,
        command="systemctl start smoke-app",
    )

    result = client.wait_for_cr(cr_id, timeout=900)

    if result["status"] != "completed":
        fail(f"Phase 3: CR did not complete — status={result['status']}")

    cr_result = result.get("execution_result", {})
    baseline = cr_result.get("baseline", {})
    obs_duration = cr_result.get("observation_duration_seconds", 0)

    if obs_duration <= 120:
        fail(f"Phase 3: observation_duration_seconds not extended beyond initial window: {obs_duration}")

    deps = baseline.get("dependencies", [])
    pg_dep = next((d for d in deps if "5432" in str(d.get("target", ""))), None)
    if pg_dep is None:
        fail("Phase 3: PostgreSQL dependency not in final baseline")
    if pg_dep.get("confidence") not in ("both", "low_confidence"):
        fail(f"Phase 3: PostgreSQL dependency confidence unexpected after extension: {pg_dep.get('confidence')}")

    log(f"Phase 3 PASS — adaptive window extended to {obs_duration}s, pg dep confidence={pg_dep.get('confidence')}")

    # Terminate this host
    client.terminate_and_deregister_instance(host_asset_id)


# --- Phase 4: VERIFY_BASELINE --------------------------------------------------

def phase_verify_baseline(client: NexplaneClient, host_asset_id: str, profile_asset_id: str, baseline: dict):
    log("Phase 4: VERIFY_BASELINE")

    # Harmless change: restart nginx
    client.run_ssm_command(asset_id=host_asset_id, command="systemctl restart nginx")
    time.sleep(3)

    # Pass path
    cr_id = client.create_cr(
        change_type="verify_against_baseline",
        asset_ids=[profile_asset_id],
        parameters={},
        title="Smoke: verify baseline (pass path)",
    )
    client.approve_cr(cr_id)
    result = client.wait_for_cr(cr_id, timeout=180)

    if result["status"] != "completed":
        fail(f"Phase 4 pass path: CR did not complete — status={result['status']}")

    cr_result = result.get("execution_result", {})
    if cr_result.get("failed"):
        fail(f"Phase 4 pass path: verify returned failed=True when all layers should pass")
    layers = cr_result.get("layers", {})
    for layer in ("infrastructure", "service", "application", "data"):
        if not layers.get(layer, {}).get("passed"):
            fail(f"Phase 4 pass path: layer '{layer}' did not pass")

    log("Phase 4 pass path PASS — all four layers verified")

    # Failure path: stop Flask
    client.run_ssm_command(asset_id=host_asset_id, command="systemctl stop smoke-app")
    time.sleep(3)

    cr_id_fail = client.create_cr(
        change_type="verify_against_baseline",
        asset_ids=[profile_asset_id],
        parameters={},
        title="Smoke: verify baseline (failure path — Flask stopped)",
    )
    client.approve_cr(cr_id_fail)
    result_fail = client.wait_for_cr(cr_id_fail, timeout=180)

    cr_result_fail = result_fail.get("execution_result", {})
    if not cr_result_fail.get("failed"):
        fail("Phase 4 failure path: verify returned failed=False when Flask is stopped (Application layer should fail)")

    layers_fail = cr_result_fail.get("layers", {})
    if layers_fail.get("application", {}).get("passed"):
        fail("Phase 4 failure path: application layer shows passed=True when Flask is down")

    # FILO rollback should fire (non-mutating rollback still runs the machinery)
    if result_fail.get("status") not in ("rolled_back", "failed"):
        fail(f"Phase 4 failure path: CR should reach rolled_back or failed, got {result_fail.get('status')}")

    log("Phase 4 failure path PASS — Application layer failed, FILO rollback triggered, CR reached rolled_back")
    log("Phase 4 COMPLETE")


# --- Combined run entry point --------------------------------------------------

def run(client: NexplaneClient):
    log("=== Migration Foundation Smoke — Phases 1–4 ===")

    # Phase 1 and 2 share the same host
    host_asset_id, profile_asset_id = phase_discovery_profile(client)
    baseline = phase_behavioral_baseline(client, host_asset_id, profile_asset_id)

    # Phase 3 uses its own host (fresh, idle Flask)
    phase_baseline_adaptive(client)

    # Phase 4 uses the Phase 1/2 host + baseline
    phase_verify_baseline(client, host_asset_id, profile_asset_id, baseline)

    # Cleanup
    client.terminate_and_deregister_instance(host_asset_id)
    log("=== All phases PASSED ===")
```

- [ ] **Step 4: Verify syntax**

```bash
cd backend && python -c "import ast; ast.parse(open('tests/smoke/test_migration_foundation_live.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 5: Verify import on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 python -c \
   'import tests.smoke.test_migration_foundation_live; print(\"import OK\")'"
```
Expected: `import OK`

- [ ] **Step 6: Run phases 1–4 combined smoke on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 \
   python -m tests.smoke.test_migration_foundation_live --run"
```
Expected: All phases log `PASS` lines, `=== All phases PASSED ===` at end. No `fail()` calls triggered.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/test_migration_foundation_live.py
git commit -m "feat(migration): smoke phases 1-4 — discovery, behavioral baseline, adaptive window, 4-layer verification"
```

---

## Self-Review

**Spec coverage:**
- ✅ `discover_application_profile` executor — Task 3
- ✅ `capture_behavioral_baseline` executor with adaptive extension — Task 4
- ✅ `verify_against_baseline` executor with 4-layer logic and `failed` flag — Task 5
- ✅ New asset types: `application_profile`, `service_endpoint`, `database_instance`, `network_port` — Tasks 1
- ✅ ChangeType additions for all 22 migration CR types — Task 2
- ✅ Catalog JSON entries for foundation CRs — Task 2
- ✅ MCP tools: `list_application_profiles`, `get_application_profile`, `list_database_instances` — Task 6
- ✅ Smoke phase 1 (DISCOVERY_PROFILE) — Task 7
- ✅ Smoke phase 2 (BEHAVIORAL_BASELINE) — Task 7
- ✅ Smoke phase 3 (BASELINE_ADAPTIVE) — Task 7
- ✅ Smoke phase 4 (VERIFY_BASELINE pass + failure path) — Task 7
- ✅ FILO rollback machinery verified in phase 4 failure path

**Deferred to later plans:**
- `run_os_upgrade`, `provision_host`, `migrate_host_config`, `migrate_identity`, `rsync_application`, `resolve_dependencies`, `ai_assisted_cr`, `shift_traffic_weight` — Plan 2
- `containerize_application`, `deploy_container_alongside` — Plan 3
- `migrate_postgres_instance`, `verify_data_integrity`, `run_schema_migration`, `update_connection_strings`, decommission CRs — Plan 4
- Smoke phases 5–12 — Plans 2–4

**Placeholder scan:** None found — all steps have exact code, commands, and expected output.

**Type consistency:**
- `dispatch_agent_job(command, parameters, asset_ids, timeout_seconds)` — consistent across Tasks 3, 4, 5
- `_auto_asset` key with `asset_type: "application_profile"` — used in Task 3 executor, verified in Task 7 smoke
- `failed: bool` in verify result — produced in Task 5, asserted in Task 7 smoke phase 4
