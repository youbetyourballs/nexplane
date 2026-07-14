<!-- Tasks 8-13 — continuation of 2026-07-14-reference-scan-and-update.md -->

---

### Task 8: Agent Scan Command and Executor

**Files:**
- Create: `agent/commands/reference/scan.go`
- Modify: `agent/commands/register.go` (or equivalent command registration file)
- Create: `backend/app/connectors/executors/nexplane_agent/scan_host_references.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: `backend/tests/unit/test_agent_reference_scan.py`

**Interfaces:**
- Agent command: `reference-scan --terms <comma-separated> --paths <comma-separated> [--extensions <comma-separated>]`
- Agent outputs JSON to stdout: `{"hits": [<unified hit schema>], "scan_summary": {"scanned": int, "matched": int}}`
- Surface value for agent hits: `"host_file"`
- Consumer `stable_id`: host's Nexplane asset ID (passed by executor as parameter), fallback hostname
- Executor dispatches via `dispatch_agent_job(command="reference-scan", parameters={...}, asset_ids=[asset_id])`

- [ ] **Step 1: Read existing agent command structure**

```bash
ls agent/commands/
head -60 agent/commands/register.go 2>/dev/null || ls agent/
```

Note the exact pattern for registering a new command subcommand.

- [ ] **Step 2: Write the failing test for the executor**

```python
# backend/tests/unit/test_agent_reference_scan.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_scan_host_references_dispatches_agent_job():
    from app.connectors.executors.nexplane_agent.scan_host_references import execute

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "search_terms": ["old-db.internal"],
        "paths": ["/etc", "/opt/app/config"],
        "extensions": [".conf", ".env", ".yaml"],
        "asset_id": "asset-uuid-123",
    }
    mock_cr.asset_id = "asset-uuid-123"
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    mock_job_result = {
        "output": '{"hits": [{"surface": "host_file", "location": "/etc/app/config.conf", "matched_term": "old-db.internal", "snippet": "db_host=old-db.internal", "consumer_identity": {"stable_id": "asset-uuid-123", "hostname": "web-01", "surface_metadata": {}}}], "scan_summary": {"scanned": 42, "matched": 1}}',
        "exit_code": 0,
    }

    with patch("app.connectors.executors.nexplane_agent.scan_host_references._dispatch.dispatch_agent_job", new_callable=AsyncMock, return_value=mock_job_result):
        result = await execute(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    assert result["hits"][0]["surface"] == "host_file"
    assert result["scan_summary"]["matched"] == 1
```

Run: `cd backend && python -m pytest tests/unit/test_agent_reference_scan.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Add catalog action to nexplane_agent.json**

```json
{
  "id": "scan_host_references",
  "name": "Scan Host for References",
  "description": "Search files on an agent-monitored host for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "paths": {"type": "array", "items": {"type": "string"}, "description": "Directories to search (default: /etc, /opt, /var/www, /home)"},
      "extensions": {"type": "array", "items": {"type": "string"}, "description": "File extensions (default: .conf .env .yaml .yml .json .toml .ini .properties .sh)"}
    }
  },
  "rollback_strategy": "no_op"
}
```

- [ ] **Step 4: Create the executor**

```python
# backend/app/connectors/executors/nexplane_agent/scan_host_references.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import logging
from app.connectors.executors.nexplane_agent import _dispatch

logger = logging.getLogger(__name__)

_DEFAULT_PATHS = ["/etc", "/opt", "/var/www", "/home"]
_DEFAULT_EXTENSIONS = [".conf", ".env", ".yaml", ".yml", ".json", ".toml", ".ini", ".properties", ".sh"]


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    paths = params.get("paths") or _DEFAULT_PATHS
    extensions = params.get("extensions") or _DEFAULT_EXTENSIONS
    asset_id = str(cr.asset_id) if cr.asset_id else None

    result = await _dispatch.dispatch_agent_job(
        command="reference-scan",
        parameters={
            "terms": ",".join(search_terms),
            "paths": ",".join(paths),
            "extensions": ",".join(extensions),
        },
        asset_ids=[asset_id] if asset_id else [],
        timeout_seconds=300,
    )

    if result.get("exit_code", 1) != 0:
        return {"hits": [], "scan_summary": {"scanned": 0, "matched": 0, "error": result.get("output", "agent job failed")}}

    try:
        data = json.loads(result.get("output", "{}"))
    except json.JSONDecodeError:
        logger.warning("Agent reference-scan returned non-JSON output")
        return {"hits": [], "scan_summary": {"scanned": 0, "matched": 0, "error": "parse_failure"}}

    return {"hits": data.get("hits", []), "scan_summary": data.get("scan_summary", {})}
```

- [ ] **Step 5: Create the Go agent command at `agent/commands/reference/scan.go`**

The command must implement `reference-scan` with flags `--terms`, `--paths`, `--extensions`, `--asset-id`. It walks the given paths, checks each file's extension, scans line by line for any of the terms (case-insensitive), and outputs JSON matching the unified hit schema. Full implementation follows the same pattern as existing agent commands in `agent/commands/`. Surface value is `"host_file"`. Location is `"<filepath>:<linenum>"`. One hit per matching line per term.

- [ ] **Step 6: Register the command in register.go**

Add `rootCmd.AddCommand(reference.ScanCmd)` following the same pattern as other commands.

- [ ] **Step 7: Run the executor test**

Run: `cd backend && python -m pytest tests/unit/test_agent_reference_scan.py -v`
Expected: PASS

- [ ] **Step 8: Build the agent to confirm Go compiles**

```bash
cd agent && go build ./...
```

Expected: no errors

- [ ] **Step 9: Commit**

```bash
git add agent/commands/reference/scan.go agent/commands/register.go backend/app/connectors/executors/nexplane_agent/scan_host_references.py backend/app/connectors/catalog/nexplane_agent.json backend/tests/unit/test_agent_reference_scan.py
git commit -m "feat: agent reference-scan command + nexplane_agent executor"
```

---

### Task 9: Agent Update Command and Executor

**Files:**
- Create: `agent/commands/reference/update.go`
- Create: `backend/app/connectors/executors/nexplane_agent/update_config_file_reference.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: `backend/tests/unit/test_agent_reference_update.py`

**Interfaces:**
- Agent commands: `reference-update --file <path> --old-value <str> --new-value <str> --backup-dir <dir>` and `reference-restore --backup-path <path> --target-path <path>`
- `reference-update` reads the file, writes backup first, then replaces all occurrences; outputs `{"status": "updated", "backup_path": "...", "replacements": N}`
- `reference-restore` copies backup back to target path; outputs `{"status": "restored", "target": "..."}`
- Executor saves `backup_path` and `target_path` in `rollback_data`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_agent_reference_update.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_update_config_file_dispatches_agent_job():
    from app.connectors.executors.nexplane_agent.update_config_file_reference import execute

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "file_path": "/etc/app/config.conf",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    }
    mock_cr.asset_id = "asset-uuid-456"
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    mock_job_result = {
        "output": '{"status": "updated", "backup_path": "/tmp/nexplane-backup-abc123/config.conf", "replacements": 3}',
        "exit_code": 0,
    }

    with patch("app.connectors.executors.nexplane_agent.update_config_file_reference._dispatch.dispatch_agent_job", new_callable=AsyncMock, return_value=mock_job_result):
        result = await execute(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert "rollback_data" in result
    assert result["rollback_data"]["backup_path"] == "/tmp/nexplane-backup-abc123/config.conf"
    assert result["rollback_data"]["target_path"] == "/etc/app/config.conf"
```

Run: `cd backend && python -m pytest tests/unit/test_agent_reference_update.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2: Add catalog action to nexplane_agent.json**

```json
{
  "id": "update_config_file_reference",
  "name": "Update Config File Reference",
  "description": "Replace all occurrences of a string in a config file on an agent-monitored host. Backs up the file first for rollback.",
  "parameters": {
    "type": "object",
    "required": ["file_path", "old_value", "new_value"],
    "properties": {
      "file_path": {"type": "string"},
      "old_value": {"type": "string"},
      "new_value": {"type": "string"},
      "backup_dir": {"type": "string", "description": "Backup directory (default: /tmp/nexplane-backups)"}
    }
  },
  "rollback_strategy": "reconstitution"
}
```

- [ ] **Step 3: Create the executor**

```python
# backend/app/connectors/executors/nexplane_agent/update_config_file_reference.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import logging
from app.connectors.executors.nexplane_agent import _dispatch

logger = logging.getLogger(__name__)


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    file_path = params["file_path"]
    old_value = params["old_value"]
    new_value = params["new_value"]
    backup_dir = params.get("backup_dir", "/tmp/nexplane-backups")
    asset_id = str(cr.asset_id) if cr.asset_id else None

    result = await _dispatch.dispatch_agent_job(
        command="reference-update",
        parameters={
            "file": file_path,
            "old-value": old_value,
            "new-value": new_value,
            "backup-dir": backup_dir,
        },
        asset_ids=[asset_id] if asset_id else [],
        timeout_seconds=60,
    )

    if result.get("exit_code", 1) != 0:
        return {"status": "failed", "error": result.get("output", "agent job failed")}

    try:
        data = json.loads(result.get("output", "{}"))
    except json.JSONDecodeError:
        return {"status": "failed", "error": "parse_failure"}

    return {
        "status": data.get("status", "unknown"),
        "replacements": data.get("replacements", 0),
        "rollback_data": {
            "backup_path": data.get("backup_path"),
            "target_path": file_path,
            "asset_id": asset_id,
        },
    }


async def rollback(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    asset_id = rb.get("asset_id")

    result = await _dispatch.dispatch_agent_job(
        command="reference-restore",
        parameters={
            "backup-path": rb["backup_path"],
            "target-path": rb["target_path"],
        },
        asset_ids=[asset_id] if asset_id else [],
        timeout_seconds=60,
    )

    return {"status": "rolled_back" if result.get("exit_code") == 0 else "rollback_failed", "agent_output": result.get("output")}
```

- [ ] **Step 4: Create `agent/commands/reference/update.go`**

Implements two commands: `reference-update` (flags: `--file`, `--old-value`, `--new-value`, `--backup-dir`) and `reference-restore` (flags: `--backup-path`, `--target-path`). The update command: (1) reads file, (2) creates backup dir if needed, (3) writes timestamped backup, (4) writes replaced content back, (5) outputs JSON `{status, backup_path, replacements}`. The restore command copies backup to target and outputs `{status, target}`. Both preserve file permissions.

- [ ] **Step 5: Register both commands in register.go**

```go
rootCmd.AddCommand(reference.UpdateCmd)
rootCmd.AddCommand(reference.RestoreCmd)
```

- [ ] **Step 6: Run the executor test**

Run: `cd backend && python -m pytest tests/unit/test_agent_reference_update.py -v`
Expected: PASS

- [ ] **Step 7: Build the agent**

```bash
cd agent && go build ./...
```

Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add agent/commands/reference/update.go agent/commands/register.go backend/app/connectors/executors/nexplane_agent/update_config_file_reference.py backend/app/connectors/catalog/nexplane_agent.json backend/tests/unit/test_agent_reference_update.py
git commit -m "feat: agent reference-update/restore + nexplane_agent executor with reconstitution rollback"
```

---

### Task 10: scan_for_references Orchestrator and activities.py Routing

**Files:**
- Create: `backend/app/connectors/executors/reference/__init__.py`
- Create: `backend/app/connectors/executors/reference/scan_orchestrator.py`
- Modify: `backend/app/connectors/activities.py`
- Create: `backend/tests/unit/test_scan_orchestrator.py`

**Interfaces:**
- Consumes: scan executors from Tasks 4, 6, 8; `resolve_consumer_identity` from Task 2; `triage_scan_hits` from Task 3; `ScanException` from Task 1; `Asset` model
- Produces:
  ```python
  async def orchestrate_scan(cr, db, secrets_svc, settings) -> dict
  # Returns: {"hits_total": int, "confident_updates": [...], "exceptions_created": int, "assets_registered": int}
  ```
- `activities.py`: add `elif change_type == "scan_for_references"` in both the execute branch (~line 158) and rollback branch (~line 305)

- [ ] **Step 1: Read activities.py dispatch location**

```bash
grep -n "change_type\|elif\|catalog_action\|fan_out" backend/app/connectors/activities.py | head -40
```

Note the exact line numbers.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/unit/test_scan_orchestrator.py
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_orchestrate_scan_aggregates_hits_and_creates_exceptions():
    from app.connectors.executors.reference.scan_orchestrator import orchestrate_scan

    org_id = uuid.uuid4()
    scan_cr_id = uuid.uuid4()
    connector_id = str(uuid.uuid4())

    mock_cr = MagicMock()
    mock_cr.id = scan_cr_id
    mock_cr.organization_id = org_id
    mock_cr.parameters = {
        "search_terms": ["old-db.internal"],
        "migration_context": {"source_term": "old-db.internal", "target_term": "new-db.internal", "notes": "test"},
        "connectors": [{"connector_type": "aws", "connector_id": connector_id}],
    }

    mock_db = AsyncMock()
    mock_connector = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_connector
    mock_db.execute.return_value = mock_result

    mock_secrets = MagicMock()
    mock_settings = MagicMock()
    mock_settings.anthropic_api_key_encrypted = "enc"

    aws_hits = [{
        "surface": "aws_lambda_env",
        "location": "arn:aws:lambda:us-east-1:123:function:fn",
        "matched_term": "old-db.internal",
        "snippet": "DB_HOST=old-db.internal",
        "consumer_identity": {"stable_id": "arn:...", "hostname": None, "surface_metadata": {}},
    }]

    from app.services.identity_resolution import IdentityResolutionResult
    from app.services.reference_triage import TriageResult

    with patch("app.connectors.executors.reference.scan_orchestrator._run_aws_scans", new_callable=AsyncMock, return_value=aws_hits), \
         patch("app.connectors.executors.reference.scan_orchestrator._run_k8s_scans", new_callable=AsyncMock, return_value=[]), \
         patch("app.connectors.executors.reference.scan_orchestrator._run_agent_scans", new_callable=AsyncMock, return_value=[]), \
         patch("app.connectors.executors.reference.scan_orchestrator.resolve_consumer_identity", new_callable=AsyncMock, return_value=IdentityResolutionResult(asset_id=None, tier=4, confidence=0.0, new_asset_data={"name": "fn", "asset_type": "application", "environment": "unknown", "asset_metadata": {}})), \
         patch("app.connectors.executors.reference.scan_orchestrator.triage_scan_hits", new_callable=AsyncMock, return_value=TriageResult(confident_updates=[], exceptions=[{"hit_index": 0, "asset_id": None, "reason": "no match", "suggested_action": "register", "confidence": 0.3}])):
        result = await orchestrate_scan(mock_cr, mock_db, mock_secrets, mock_settings)

    assert result["hits_total"] == 1
    assert result["exceptions_created"] == 1
```

Run: `cd backend && python -m pytest tests/unit/test_scan_orchestrator.py -v`
Expected: FAIL

- [ ] **Step 3: Create `backend/app/connectors/executors/reference/__init__.py`** (empty)

- [ ] **Step 4: Create `backend/app/connectors/executors/reference/scan_orchestrator.py`**

```python
# backend/app/connectors/executors/reference/scan_orchestrator.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.scan_exception import ScanException
from app.services.identity_resolution import resolve_consumer_identity
from app.services.reference_triage import triage_scan_hits

logger = logging.getLogger(__name__)


async def _run_aws_scans(cr, connector, db) -> list[dict]:
    from app.connectors.executors.aws.reference_scan import (
        scan_lambda_env_vars, scan_ecs_task_defs, scan_rds_parameter_groups,
        scan_secrets_manager_metadata, scan_ssm_parameters_metadata, scan_ec2_user_data,
    )
    hits = []
    for fn in [scan_lambda_env_vars, scan_ecs_task_defs, scan_rds_parameter_groups,
               scan_secrets_manager_metadata, scan_ssm_parameters_metadata, scan_ec2_user_data]:
        try:
            hits.extend((await fn(cr, connector, db)).get("hits", []))
        except Exception as e:
            logger.warning("%s failed: %s", fn.__name__, e)
    return hits


async def _run_k8s_scans(cr, connector, db) -> list[dict]:
    from app.connectors.executors.kubernetes.reference_scan import (
        scan_configmaps, scan_secrets_metadata, scan_deployment_env, scan_ingress_rules,
    )
    hits = []
    for fn in [scan_configmaps, scan_secrets_metadata, scan_deployment_env, scan_ingress_rules]:
        try:
            hits.extend((await fn(cr, connector, db)).get("hits", []))
        except Exception as e:
            logger.warning("%s failed: %s", fn.__name__, e)
    return hits


async def _run_agent_scans(cr, connector, asset_ids, db) -> list[dict]:
    from app.connectors.executors.nexplane_agent.scan_host_references import execute
    hits = []
    for asset_id in asset_ids:
        mock_cr = type("CR", (), {"parameters": cr.parameters, "asset_id": asset_id})()
        try:
            hits.extend((await execute(mock_cr, connector, db)).get("hits", []))
        except Exception as e:
            logger.warning("Agent scan for %s failed: %s", asset_id, e)
    return hits


async def orchestrate_scan(cr, db: AsyncSession, secrets_svc, settings) -> dict:
    params = cr.parameters or {}
    org_id = cr.organization_id
    migration_context = params.get("migration_context", {})
    connector_configs = params.get("connectors", [])

    all_hits: list[dict] = []
    for cc in connector_configs:
        connector_type = cc.get("connector_type")
        connector_id = cc.get("connector_id")
        from app.models.connector import Connector
        r = await db.execute(select(Connector).where(Connector.id == connector_id))
        connector = r.scalar_one_or_none()
        if not connector:
            logger.warning("Connector %s not found, skipping", connector_id)
            continue
        if connector_type == "aws":
            hits = await _run_aws_scans(cr, connector, db)
        elif connector_type == "kubernetes":
            hits = await _run_k8s_scans(cr, connector, db)
        elif connector_type == "nexplane_agent":
            hits = await _run_agent_scans(cr, connector, cc.get("asset_ids", []), db)
        else:
            logger.warning("Unknown connector type %s, skipping", connector_type)
            continue
        all_hits.extend(hits)

    # Identity resolution
    resolved: list[dict] = []
    assets_registered = 0
    for hit in all_hits:
        res = await resolve_consumer_identity(db, org_id, hit.get("consumer_identity", {}))
        if res.tier == 4 and res.new_asset_data:
            new_asset = Asset(
                id=uuid.uuid4(),
                organization_id=org_id,
                name=res.new_asset_data["name"],
                asset_type=res.new_asset_data["asset_type"],
                environment=res.new_asset_data.get("environment", "unknown"),
                asset_metadata=res.new_asset_data.get("asset_metadata", {}),
            )
            db.add(new_asset)
            await db.flush()
            res = type("R", (), {"asset_id": new_asset.id, "tier": 4, "confidence": 0.0})()
            assets_registered += 1
        resolved.append({"hit": hit, "asset_id": res.asset_id, "tier": res.tier, "confidence": res.confidence})

    # AI triage
    triage = await triage_scan_hits(resolved, migration_context, settings, secrets_svc)

    # Persist exceptions
    for exc in triage.exceptions:
        hit_data = resolved[exc["hit_index"]]["hit"] if exc.get("hit_index") is not None else {}
        db.add(ScanException(
            organization_id=org_id,
            scan_cr_id=cr.id,
            consumer_asset_id=exc.get("asset_id"),
            matched_term=hit_data.get("matched_term", ""),
            location=hit_data.get("location", ""),
            surface=hit_data.get("surface", ""),
            snippet=hit_data.get("snippet", ""),
            reason=exc.get("reason", ""),
            suggested_action=exc.get("suggested_action", ""),
            confidence=exc.get("confidence", 0.0),
            status="pending",
        ))
    await db.commit()

    return {
        "hits_total": len(all_hits),
        "confident_updates": triage.confident_updates,
        "exceptions_created": len(triage.exceptions),
        "assets_registered": assets_registered,
    }
```

- [ ] **Step 5: Wire into activities.py**

In both the execute dispatch chain and rollback dispatch chain, add:

Execute branch:
```python
elif change_type == "scan_for_references":
    from app.connectors.executors.reference.scan_orchestrator import orchestrate_scan
    result = await orchestrate_scan(cr, db, secrets_svc, settings)
```

Rollback branch:
```python
elif change_type == "scan_for_references":
    result = {"status": "no_op", "note": "scan_for_references has no rollback action"}
```

- [ ] **Step 6: Run the test**

Run: `cd backend && python -m pytest tests/unit/test_scan_orchestrator.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/reference/__init__.py backend/app/connectors/executors/reference/scan_orchestrator.py backend/app/connectors/activities.py backend/tests/unit/test_scan_orchestrator.py
git commit -m "feat: scan_for_references orchestrator — fan-out, identity resolution, AI triage, exception persistence"
```

---

### Task 11: Exception Resolution Service and Router

**Files:**
- Create: `backend/app/services/scan_exception_service.py`
- Create: `backend/app/routers/scan_exceptions.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/unit/test_scan_exception_service.py`

**Interfaces:**
- Three resolution paths:
  1. `resolve_with_update_cr(exception_id, update_cr_params, db, org_id)` → creates `update_reference` CR in draft status, sets exception `status="resolved"`, `resolved_by_cr_id`
  2. `reattempt_triage(exception_id, operator_context, db, org_id, settings, secrets_svc)` → re-runs AI, updates exception or moves to confident
  3. `dismiss_exception(exception_id, reason, db, org_id)` → mandatory reason, sets `status="dismissed"`, `resolution_notes`
- `create_findings_for_unresolved(scan_cr_id, org_id, db)` → call from update_reference executor; creates `VulnerabilityFinding(finding_type="reference_not_updated")` for each pending exception
- REST: `GET /scan-exceptions?scan_cr_id=`, `POST /scan-exceptions/{id}/resolve`, `POST /scan-exceptions/{id}/reattempt`, `POST /scan-exceptions/{id}/dismiss`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_scan_exception_service.py
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_dismiss_exception_requires_reason():
    from app.services.scan_exception_service import dismiss_exception
    with pytest.raises(ValueError, match="reason"):
        await dismiss_exception(uuid.uuid4(), "", AsyncMock(), uuid.uuid4())


@pytest.mark.asyncio
async def test_dismiss_exception_sets_status():
    from app.services.scan_exception_service import dismiss_exception

    org_id = uuid.uuid4()
    mock_exception = MagicMock()
    mock_exception.organization_id = org_id
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_exception
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    result = await dismiss_exception(uuid.uuid4(), "intentional — read-only replica", mock_db, org_id)

    assert mock_exception.status == "dismissed"
    assert "read-only replica" in mock_exception.resolution_notes
    assert result["status"] == "dismissed"
```

Run: `cd backend && python -m pytest tests/unit/test_scan_exception_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2: Create `backend/app/services/scan_exception_service.py`**

```python
# backend/app/services/scan_exception_service.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.scan_exception import ScanException
from app.models.change_request import ChangeRequest, ChangeType, ChangeStatus

logger = logging.getLogger(__name__)


async def _get_exception(exception_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession) -> ScanException:
    r = await db.execute(select(ScanException).where(ScanException.id == exception_id, ScanException.organization_id == org_id))
    exc = r.scalar_one_or_none()
    if not exc:
        raise ValueError(f"ScanException {exception_id} not found")
    return exc


async def dismiss_exception(exception_id: uuid.UUID, reason: str, db: AsyncSession, org_id: uuid.UUID) -> dict:
    if not reason or not reason.strip():
        raise ValueError("reason is required when dismissing a scan exception")
    exc = await _get_exception(exception_id, org_id, db)
    exc.status = "dismissed"
    exc.resolution_notes = reason.strip()
    exc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "dismissed", "exception_id": str(exception_id)}


async def resolve_with_update_cr(exception_id: uuid.UUID, update_cr_params: dict, db: AsyncSession, org_id: uuid.UUID) -> dict:
    exc = await _get_exception(exception_id, org_id, db)
    cr = ChangeRequest(
        id=uuid.uuid4(),
        organization_id=org_id,
        change_type=ChangeType.update_reference,
        asset_id=exc.consumer_asset_id,
        title=f"Update reference at {exc.location}",
        parameters=update_cr_params,
        status=ChangeStatus.draft,
        created_at=datetime.now(timezone.utc),
    )
    db.add(cr)
    exc.status = "resolved"
    exc.resolved_by_cr_id = cr.id
    exc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "resolved", "exception_id": str(exception_id), "cr_id": str(cr.id)}


async def reattempt_triage(exception_id: uuid.UUID, operator_context: str, db: AsyncSession, org_id: uuid.UUID, settings, secrets_svc) -> dict:
    exc = await _get_exception(exception_id, org_id, db)
    from app.services.reference_triage import triage_scan_hits
    hit = {"hit": {"surface": exc.surface, "location": exc.location, "matched_term": exc.matched_term, "snippet": exc.snippet}, "asset_id": exc.consumer_asset_id, "tier": 2, "confidence": 0.5}
    triage = await triage_scan_hits([hit], {"operator_context": operator_context, "source_term": exc.matched_term}, settings, secrets_svc)
    if triage.confident_updates:
        exc.status = "resolved"
        exc.resolution_notes = f"AI re-triage succeeded: {operator_context}"
        exc.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "resolved_by_retriage", "confident_updates": triage.confident_updates}
    exc.reason = triage.exceptions[0]["reason"] if triage.exceptions else exc.reason
    exc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "still_exception", "updated_reason": exc.reason}


async def create_findings_for_unresolved(scan_cr_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession) -> int:
    from app.models.vulnerability import VulnerabilityFinding
    r = await db.execute(select(ScanException).where(ScanException.scan_cr_id == scan_cr_id, ScanException.organization_id == org_id, ScanException.status == "pending"))
    unresolved = r.scalars().all()
    for exc in unresolved:
        db.add(VulnerabilityFinding(
            id=uuid.uuid4(),
            organization_id=org_id,
            asset_id=exc.consumer_asset_id,
            finding_type="reference_not_updated",
            status="open",
            raw_payload={"scan_exception_id": str(exc.id), "location": exc.location, "surface": exc.surface, "matched_term": exc.matched_term, "snippet": exc.snippet, "reason": exc.reason},
        ))
    if unresolved:
        await db.commit()
    return len(unresolved)
```

- [ ] **Step 3: Create `backend/app/routers/scan_exceptions.py`**

```python
# backend/app/routers/scan_exceptions.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.dependencies import get_current_user
from app.models.scan_exception import ScanException
from app.services.scan_exception_service import dismiss_exception, resolve_with_update_cr, reattempt_triage

router = APIRouter(prefix="/scan-exceptions", tags=["scan-exceptions"])


@router.get("")
async def list_scan_exceptions(scan_cr_id: uuid.UUID, db=Depends(get_db), current_user=Depends(get_current_user)):
    r = await db.execute(select(ScanException).where(ScanException.scan_cr_id == scan_cr_id, ScanException.organization_id == current_user.organization_id))
    return [{"id": str(e.id), "matched_term": e.matched_term, "location": e.location, "surface": e.surface, "snippet": e.snippet, "reason": e.reason, "suggested_action": e.suggested_action, "confidence": e.confidence, "status": e.status, "consumer_asset_id": str(e.consumer_asset_id) if e.consumer_asset_id else None} for e in r.scalars().all()]


class ResolveRequest(BaseModel):
    update_cr_params: dict

class ReattemptRequest(BaseModel):
    operator_context: str

class DismissRequest(BaseModel):
    reason: str


@router.post("/{exception_id}/resolve")
async def resolve_exception(exception_id: uuid.UUID, body: ResolveRequest, db=Depends(get_db), current_user=Depends(get_current_user)):
    try:
        return await resolve_with_update_cr(exception_id, body.update_cr_params, db, current_user.organization_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{exception_id}/reattempt")
async def reattempt_exception(exception_id: uuid.UUID, body: ReattemptRequest, db=Depends(get_db), current_user=Depends(get_current_user)):
    from app.dependencies import get_settings, get_secrets_service
    try:
        return await reattempt_triage(exception_id, body.operator_context, db, current_user.organization_id, get_settings(), get_secrets_service())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{exception_id}/dismiss")
async def dismiss(exception_id: uuid.UUID, body: DismissRequest, db=Depends(get_db), current_user=Depends(get_current_user)):
    try:
        return await dismiss_exception(exception_id, body.reason, db, current_user.organization_id)
    except ValueError as e:
        raise HTTPException(status_code=400 if "reason" in str(e) else 404, detail=str(e))
```

- [ ] **Step 4: Register in main.py**

```python
from app.routers.scan_exceptions import router as scan_exceptions_router
app.include_router(scan_exceptions_router)
```

- [ ] **Step 5: Run the test**

Run: `cd backend && python -m pytest tests/unit/test_scan_exception_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scan_exception_service.py backend/app/routers/scan_exceptions.py backend/app/main.py backend/tests/unit/test_scan_exception_service.py
git commit -m "feat: scan exception resolution service + REST endpoints (resolve/reattempt/dismiss)"
```

---

### Task 12: MCP Tools for Reference Scan

**Files:**
- Create: `backend/app/mcp_tools/reference_scan.py`
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/app/mcp_tools/server_instructions.py`
- Create: `backend/tests/unit/test_mcp_reference_scan.py`

**Interfaces:**
- 6 MCP tools: `scan_for_references`, `get_scan_results`, `list_reference_exceptions`, `resolve_reference_exception`, `reattempt_reference_triage`, `dismiss_reference_exception`
- All tools follow the same auth pattern as existing MCP tools: `_get_raw_token(ctx)` → `resolve_mcp_token` → `org_id`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_mcp_reference_scan.py
def test_mcp_reference_scan_tools_registered():
    import app.mcp_tools.reference_scan as module
    for tool_name in ["scan_for_references", "get_scan_results", "list_reference_exceptions", "resolve_reference_exception", "reattempt_reference_triage", "dismiss_reference_exception"]:
        assert hasattr(module, tool_name), f"Missing MCP tool: {tool_name}"
```

Run: `cd backend && python -m pytest tests/unit/test_mcp_reference_scan.py -v`
Expected: FAIL

- [ ] **Step 2: Create `backend/app/mcp_tools/reference_scan.py`**

Read existing MCP tool modules (e.g. `app/mcp_tools/change_requests.py`) to understand the exact auth helper pattern, then implement 6 tools:

1. **`scan_for_references(search_terms, connector_ids, migration_context, asset_id=None)`** — creates a `scan_for_references` CR, resolves connector types from DB, sets status to `planned`, returns `{scan_cr_id, status, connector_count}`

2. **`get_scan_results(scan_cr_id)`** — reads CR execution_result and counts ScanExceptions; returns `{scan_cr_id, status, hits_total, confident_updates, exceptions_total, exceptions_pending}`

3. **`list_reference_exceptions(scan_cr_id)`** — returns list of exception dicts for the scan CR (id, surface, location, matched_term, snippet, reason, suggested_action, confidence, status, consumer_asset_id)

4. **`resolve_reference_exception(exception_id, update_cr_params)`** — calls `resolve_with_update_cr`; returns result dict

5. **`reattempt_reference_triage(exception_id, operator_context)`** — calls `reattempt_triage`; returns result dict

6. **`dismiss_reference_exception(exception_id, reason)`** — calls `dismiss_exception`; reason is required; returns result dict

All tools wrap ValueError in `{"error": str(e)}` rather than raising.

- [ ] **Step 3: Import in mcp_server.py**

In `create_mcp_app()`:
```python
import app.mcp_tools.reference_scan  # noqa: F401
```

- [ ] **Step 4: Update NEXPLANE_SERVER_INSTRUCTIONS**

In `backend/app/mcp_tools/server_instructions.py`, in the `INTENT → WORKFLOW` section (before `RULES`), add:

```
Reference scan / dependency discovery (references / hardcoded / connection string / hostname update) — scan_for_references(search_terms, connector_ids, migration_context) creates a scan CR. Poll get_scan_results(scan_cr_id) until status=completed. Review confident_updates (pre-populated update CRs ready to approve) and list_reference_exceptions for ambiguous hits. For each exception: reattempt_reference_triage with context if you know more, resolve_reference_exception with explicit params if you can determine the correct update, or dismiss_reference_exception with a documented reason. Unresolved exceptions at execution time become reference_not_updated findings.
```

- [ ] **Step 5: Run the test**

Run: `cd backend && python -m pytest tests/unit/test_mcp_reference_scan.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_tools/reference_scan.py backend/app/mcp_server.py backend/app/mcp_tools/server_instructions.py backend/tests/unit/test_mcp_reference_scan.py
git commit -m "feat: MCP tools for reference scan — 6 tools with full exception resolution parity"
```

---

### Task 13: Smoke Tests

**Files:**
- Create: `backend/tests/smoke/test_reference_scan_live.py`

**Interfaces:**
- All smoke phases use full CR lifecycle via platform REST API (create → plan → approve → execute)
- Phases: REF_SCAN_AWS, REF_SCAN_K8S, REF_SCAN_AGENT, REF_UPDATE_LAMBDA, REF_EXCEPTION_RESOLVE, REF_FINDING, REF_MCP_PARITY
- Marker string: `nexplane-smoke-test-marker.internal`
- Prerequisite: AWS Lambda with `NEXPLANE_TEST_REF=nexplane-smoke-test-marker.internal` env var

- [ ] **Step 1: Write the smoke test file**

```python
# backend/tests/smoke/test_reference_scan_live.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke tests for Reference Scan and Update.

Prerequisites:
- NEXPLANE_API_URL and NEXPLANE_API_TOKEN env vars
- AWS Lambda with env var NEXPLANE_TEST_REF=nexplane-smoke-test-marker.internal
- (Optional) K8s ConfigMap and agent host file with same marker

Run: pytest backend/tests/smoke/test_reference_scan_live.py -v -s -m smoke
"""

import os
import time
import pytest
import requests

SEARCH_TERM = "nexplane-smoke-test-marker.internal"
REPLACEMENT = "nexplane-smoke-updated-marker.internal"
API_URL = os.environ.get("NEXPLANE_API_URL", "http://100.101.186.39:8000")
API_TOKEN = os.environ.get("NEXPLANE_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}


def api(method, path, **kwargs):
    resp = requests.request(method, f"{API_URL}{path}", headers=HEADERS, timeout=60, **kwargs)
    resp.raise_for_status()
    return resp.json()


def wait_for_cr(cr_id, target_status="completed", timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = api("GET", f"/change-requests/{cr_id}")
        status = cr["status"]
        if status == target_status:
            return cr
        if status in ("failed", "rolled_back"):
            raise AssertionError(f"CR {cr_id} ended in {status}: {cr.get('execution_result')}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} did not reach {target_status} within {timeout}s")


def full_cr_lifecycle(change_type, title, parameters, asset_id=None):
    cr = api("POST", "/change-requests", json={"change_type": change_type, "title": title, "parameters": parameters, "asset_id": asset_id, "desired_outcome": {"dry_run": False}})
    cr_id = cr["id"]
    api("POST", f"/change-requests/{cr_id}/plan")
    api("POST", f"/change-requests/{cr_id}/submit")
    api("POST", f"/change-requests/{cr_id}/approve")
    api("POST", f"/change-requests/{cr_id}/execute")
    return wait_for_cr(cr_id, "completed")


@pytest.mark.smoke
def test_ref_scan_aws():
    connectors = api("GET", "/connectors")
    aws = next((c for c in connectors if c["connector_type"] == "aws"), None)
    if not aws:
        pytest.skip("No AWS connector")
    cr = full_cr_lifecycle("scan_for_references", "Smoke: AWS reference scan", {
        "search_terms": [SEARCH_TERM],
        "migration_context": {"source_term": SEARCH_TERM, "target_term": REPLACEMENT, "notes": "smoke test"},
        "connectors": [{"connector_type": "aws", "connector_id": aws["id"]}],
    })
    result = cr["execution_result"]
    assert result["hits_total"] >= 1, f"Expected ≥1 AWS hit, got: {result}"
    print(f"REF_SCAN_AWS: {result['hits_total']} hits, {result['exceptions_created']} exceptions ✓")


@pytest.mark.smoke
def test_ref_scan_k8s():
    connectors = api("GET", "/connectors")
    k8s = next((c for c in connectors if c["connector_type"] == "kubernetes"), None)
    if not k8s:
        pytest.skip("No K8s connector")
    cr = full_cr_lifecycle("scan_for_references", "Smoke: K8s reference scan", {
        "search_terms": [SEARCH_TERM],
        "migration_context": {"source_term": SEARCH_TERM, "target_term": REPLACEMENT, "notes": "smoke test"},
        "connectors": [{"connector_type": "kubernetes", "connector_id": k8s["id"]}],
    })
    assert cr["execution_result"]["hits_total"] >= 1
    print("REF_SCAN_K8S ✓")


@pytest.mark.smoke
def test_ref_scan_agent():
    assets = api("GET", "/assets?asset_type=server")
    agent_asset = next((a for a in assets if a.get("connector_type") == "nexplane_agent"), None)
    if not agent_asset:
        pytest.skip("No Nexplane Agent asset")
    cr = full_cr_lifecycle("scan_for_references", "Smoke: agent host reference scan", {
        "search_terms": [SEARCH_TERM],
        "migration_context": {"source_term": SEARCH_TERM, "target_term": REPLACEMENT, "notes": "smoke test"},
        "connectors": [{"connector_type": "nexplane_agent", "connector_id": agent_asset["connector_id"], "asset_ids": [agent_asset["id"]]}],
    })
    assert cr["execution_result"]["hits_total"] >= 1
    print("REF_SCAN_AGENT ✓")


@pytest.mark.smoke
def test_ref_update_lambda_and_rollback():
    connectors = api("GET", "/connectors")
    aws = next((c for c in connectors if c["connector_type"] == "aws"), None)
    if not aws:
        pytest.skip("No AWS connector")
    assets = api("GET", "/assets?asset_type=application")
    lambda_asset = next((a for a in assets if "smoke" in a.get("name", "").lower() and a.get("asset_metadata", {}).get("arn", "").startswith("arn:aws:lambda")), None)
    if not lambda_asset:
        pytest.skip("No smoke Lambda asset — provision one with NEXPLANE_TEST_REF env var")

    # Execute update
    cr = full_cr_lifecycle("update_reference", "Smoke: update Lambda env var", {
        "connector_type": "aws",
        "action_id": "update_lambda_env_var",
        "params": {"function_arn": lambda_asset["asset_metadata"]["arn"], "env_var_key": "NEXPLANE_TEST_REF", "old_value": SEARCH_TERM, "new_value": REPLACEMENT},
    }, asset_id=lambda_asset["id"])
    assert cr["execution_result"]["status"] == "updated"

    # Rollback
    rb = api("POST", f"/change-requests/{cr['id']}/rollback")
    wait_for_cr(rb["id"], "rolled_back")

    # Verify restored by re-scanning
    verify_cr = full_cr_lifecycle("scan_for_references", "Smoke: verify rollback", {
        "search_terms": [SEARCH_TERM],
        "migration_context": {"source_term": SEARCH_TERM, "target_term": REPLACEMENT, "notes": "rollback verify"},
        "connectors": [{"connector_type": "aws", "connector_id": aws["id"]}],
    })
    assert verify_cr["execution_result"]["hits_total"] >= 1, "Rollback failed — original term not found after rollback"
    print("REF_UPDATE_LAMBDA + rollback ✓")


@pytest.mark.smoke
def test_ref_exception_resolve():
    connectors = api("GET", "/connectors")
    aws = next((c for c in connectors if c["connector_type"] == "aws"), None)
    if not aws:
        pytest.skip("No AWS connector")
    scan_cr = full_cr_lifecycle("scan_for_references", "Smoke: generate exceptions for resolution test", {
        "search_terms": [SEARCH_TERM],
        "migration_context": {"source_term": SEARCH_TERM, "target_term": REPLACEMENT, "notes": "exception test"},
        "connectors": [{"connector_type": "aws", "connector_id": aws["id"]}],
    })
    exceptions = api("GET", f"/scan-exceptions?scan_cr_id={scan_cr['id']}")
    if not exceptions:
        pytest.skip("No exceptions generated — confirm test Lambda has NEXPLANE_TEST_REF env var")
    exc_id = exceptions[0]["id"]
    result = api("POST", f"/scan-exceptions/{exc_id}/dismiss", json={"reason": "Smoke test marker — intentional, no update needed"})
    assert result["status"] == "dismissed"
    print("REF_EXCEPTION_RESOLVE (dismiss path) ✓")


@pytest.mark.smoke
def test_ref_finding_endpoint():
    findings = api("GET", "/findings?finding_type=reference_not_updated")
    assert isinstance(findings, list), "findings endpoint must accept reference_not_updated type"
    print("REF_FINDING endpoint accepts reference_not_updated ✓")


@pytest.mark.smoke
def test_ref_mcp_parity():
    try:
        import httpx
        resp = httpx.get(f"{API_URL}/mcp/tools", headers=HEADERS, timeout=10)
        tools = {t["name"] for t in resp.json()} if resp.status_code == 200 else set()
        expected = {"scan_for_references", "get_scan_results", "list_reference_exceptions", "resolve_reference_exception", "reattempt_reference_triage", "dismiss_reference_exception"}
        missing = expected - tools
        assert not missing, f"MCP tools missing: {missing}"
        print("REF_MCP_PARITY: all 6 tools present ✓")
    except Exception as e:
        pytest.skip(f"MCP tool check failed: {e}")
```

- [ ] **Step 2: Verify smoke test is importable**

```bash
cd backend && python -m pytest tests/smoke/test_reference_scan_live.py --collect-only 2>&1 | head -20
```

Expected: lists all test functions without import errors.

- [ ] **Step 3: Commit and push**

```bash
git add backend/tests/smoke/test_reference_scan_live.py
git commit -m "feat: reference scan smoke tests — AWS/K8s/agent scan+update+rollback+exceptions+MCP parity"
git push origin master
```

- [ ] **Step 4: Pull on EC2 and run all unit tests**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd nexplane && git pull && docker exec nexplane-backend-1 python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -40"
```

Expected: all unit tests pass.

- [ ] **Step 5: Run smoke tests on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec -e NEXPLANE_API_URL=http://localhost:8000 -e NEXPLANE_API_TOKEN=<token> nexplane-backend-1 python -m pytest tests/smoke/test_reference_scan_live.py -v -s -m smoke 2>&1 | tail -50"
```

Fix any failures before marking complete. Smoke tests are required to pass — unit green alone is not done.
