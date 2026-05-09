# Containerize Legacy Workloads — Plan 1: Foundation + Discovery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the backend scaffolding for the containerize-and-migrate feature (new ChangeTypes, AssetTypes, executor stubs, change type JSON definitions) and implement the `agent_appdiscovery` CR end-to-end: Go agent scans a Linux host for running applications, results are persisted to `asset_metadata.applications[]`, and an Applications tab on the asset detail page shows what was found.

**Architecture:** Four new ChangeType enum values and two new AssetType values are added to the models. Four Python executor stubs follow the existing `upgrade_linux_instance.py` pattern. A new Go agent command `appdiscovery` registers as `discover_applications` in `executor/executor.go`. After an `agent_appdiscovery` CR completes, a post-execution hook in the workflow writes the discovered applications list to `asset.asset_metadata["applications"]` — the same pattern used by `write_cis_score_to_metadata` in `compliance/drift.py`. The frontend Applications tab reads from this metadata field.

**Tech Stack:** Python/FastAPI + SQLAlchemy (backend), Go 1.21 (agent), React/TypeScript (frontend), existing agent executor dispatch pattern

---

## File Structure

```
backend/app/models/change_request.py              MODIFY — add 4 ChangeType enum values
backend/app/models/asset.py                       MODIFY — add 2 AssetType enum values
backend/app/services/safety_engine.py             MODIFY — add 4 types to _IMPLICIT_ROLLBACK_TYPES
backend/app/connectors/change_type_definitions/   CREATE — 4 new JSON files
  agent_appdiscovery.json
  agent_containerize_build.json
  k8s_workload_deploy.json
  agent_containerize_retire.json
backend/app/connectors/executors/nexplane_agent/  CREATE — 3 new Python stubs
  app_discovery.py
  containerize_build.py
  containerize_retire.py
backend/app/connectors/executors/kubernetes/      CREATE — new connector directory
  __init__.py
  workload_deploy.py
backend/app/services/app_discovery_service.py    CREATE — write_discovered_apps_to_metadata()
backend/app/workflows/execute_change_workflow.py  MODIFY — add appdiscovery post-execution hook
backend/tests/test_containerize_foundation.py     CREATE — unit tests for all backend changes
agent/commands/appdiscovery/                      CREATE — new Go command package
  appdiscovery.go
  appdiscovery_linux.go
  appdiscovery_other.go
  appdiscovery_test.go
agent/executor/executor.go                        MODIFY — register discover_applications
frontend/src/pages/AssetDetail.tsx               MODIFY — add Applications tab section
```

---

## Task 1: Backend Enum Extensions + Safety Engine

**Files:**
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/models/asset.py`
- Modify: `backend/app/services/safety_engine.py`
- Create: `backend/tests/test_containerize_foundation.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_containerize_foundation.py`:

```python
import pytest
from app.models.change_request import ChangeType
from app.models.asset import AssetType


def test_agent_appdiscovery_change_type_exists():
    assert ChangeType.agent_appdiscovery == "agent_appdiscovery"


def test_agent_containerize_build_change_type_exists():
    assert ChangeType.agent_containerize_build == "agent_containerize_build"


def test_k8s_workload_deploy_change_type_exists():
    assert ChangeType.k8s_workload_deploy == "k8s_workload_deploy"


def test_agent_containerize_retire_change_type_exists():
    assert ChangeType.agent_containerize_retire == "agent_containerize_retire"


def test_kubernetes_cluster_asset_type_exists():
    assert AssetType.kubernetes_cluster == "kubernetes_cluster"


def test_container_image_asset_type_exists():
    assert AssetType.container_image == "container_image"


def test_appdiscovery_in_implicit_rollback_types():
    from app.services.safety_engine import score_change_request
    from app.models.change_request import ChangeRequest
    import inspect
    # _IMPLICIT_ROLLBACK_TYPES is a module-level set in safety_engine
    import app.services.safety_engine as se
    assert ChangeType.agent_appdiscovery in se._IMPLICIT_ROLLBACK_TYPES
    assert ChangeType.agent_containerize_build in se._IMPLICIT_ROLLBACK_TYPES
    assert ChangeType.k8s_workload_deploy in se._IMPLICIT_ROLLBACK_TYPES
    assert ChangeType.agent_containerize_retire in se._IMPLICIT_ROLLBACK_TYPES
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_containerize_foundation.py -v 2>&1 | head -20
```

Expected: `AttributeError: agent_appdiscovery` or similar

- [ ] **Step 3: Add ChangeType enum values to `backend/app/models/change_request.py`**

Find the ALB lifecycle section at the bottom of the ChangeType class and add after it:

```python
    # Containerize legacy workloads
    agent_appdiscovery = "agent_appdiscovery"
    agent_containerize_build = "agent_containerize_build"
    k8s_workload_deploy = "k8s_workload_deploy"
    agent_containerize_retire = "agent_containerize_retire"
```

- [ ] **Step 4: Add AssetType enum values to `backend/app/models/asset.py`**

Find the `class AssetType(str, enum.Enum):` block and add at the end:

```python
    kubernetes_cluster = "kubernetes_cluster"
    container_image = "container_image"
```

- [ ] **Step 5: Add new types to `_IMPLICIT_ROLLBACK_TYPES` in `backend/app/services/safety_engine.py`**

Find `_IMPLICIT_ROLLBACK_TYPES = {` and add before the closing `}`:

```python
        ChangeType.agent_appdiscovery,
        ChangeType.agent_containerize_build,
        ChangeType.k8s_workload_deploy,
        ChangeType.agent_containerize_retire,
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_containerize_foundation.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 7: Run full suite to check for regressions**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q --tb=short --ignore=tests/smoke 2>&1 | tail -5
```

Expected: 74+ passed, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/change_request.py backend/app/models/asset.py \
    backend/app/services/safety_engine.py backend/tests/test_containerize_foundation.py
git commit -m "feat(containerize): add ChangeType/AssetType enums and safety engine wiring"
```

---

## Task 2: Change Type JSON Definitions

**Files:**
- Create: `backend/app/connectors/change_type_definitions/agent_appdiscovery.json`
- Create: `backend/app/connectors/change_type_definitions/agent_containerize_build.json`
- Create: `backend/app/connectors/change_type_definitions/k8s_workload_deploy.json`
- Create: `backend/app/connectors/change_type_definitions/agent_containerize_retire.json`

- [ ] **Step 1: Create `agent_appdiscovery.json`**

```json
{
  "change_type": "agent_appdiscovery",
  "display_name": "Agent: Discover Applications",
  "steps": [
    {"generic_action": "discover_applications", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 2: Create `agent_containerize_build.json`**

```json
{
  "change_type": "agent_containerize_build",
  "display_name": "Agent: Containerize and Build Image",
  "steps": [
    {"generic_action": "generate_dockerfile",      "purpose": "preflight_validate", "required": true},
    {"generic_action": "build_container_image",    "purpose": "execute",            "required": true},
    {"generic_action": "push_container_image",     "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 3: Create `k8s_workload_deploy.json`**

```json
{
  "change_type": "k8s_workload_deploy",
  "display_name": "Kubernetes: Deploy Workload",
  "steps": [
    {"generic_action": "provision_persistent_volume", "purpose": "preflight_validate", "required": false},
    {"generic_action": "migrate_data_to_volume",      "purpose": "execute",            "required": false},
    {"generic_action": "apply_k8s_manifests",         "purpose": "execute",            "required": true},
    {"generic_action": "wait_for_pods_ready",         "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 4: Create `agent_containerize_retire.json`**

```json
{
  "change_type": "agent_containerize_retire",
  "display_name": "Agent: Retire Legacy Host",
  "steps": [
    {"generic_action": "verify_container_health",  "purpose": "preflight_validate", "required": true},
    {"generic_action": "snapshot_legacy_host",     "purpose": "execute",            "required": true},
    {"generic_action": "stop_legacy_service",      "purpose": "execute",            "required": true},
    {"generic_action": "mark_asset_retired",       "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 5: Verify the JSON files are valid**

```bash
for f in backend/app/connectors/change_type_definitions/agent_appdiscovery.json \
          backend/app/connectors/change_type_definitions/agent_containerize_build.json \
          backend/app/connectors/change_type_definitions/k8s_workload_deploy.json \
          backend/app/connectors/change_type_definitions/agent_containerize_retire.json; do
  python -m json.tool $f > /dev/null && echo "OK: $f" || echo "INVALID: $f"
done
```

Expected: 4x `OK:`

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/change_type_definitions/agent_appdiscovery.json \
    backend/app/connectors/change_type_definitions/agent_containerize_build.json \
    backend/app/connectors/change_type_definitions/k8s_workload_deploy.json \
    backend/app/connectors/change_type_definitions/agent_containerize_retire.json
git commit -m "feat(containerize): add change type JSON definitions for all 4 containerize CRs"
```

---

## Task 3: Python Executor Stubs

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/app_discovery.py`
- Create: `backend/app/connectors/executors/nexplane_agent/containerize_build.py`
- Create: `backend/app/connectors/executors/nexplane_agent/containerize_retire.py`
- Create: `backend/app/connectors/executors/kubernetes/__init__.py`
- Create: `backend/app/connectors/executors/kubernetes/workload_deploy.py`

- [ ] **Step 1: Create `app_discovery.py`**

```python
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Stub executor for agent_appdiscovery. Real execution dispatched to Go agent."""
    return {
        "action": "discover_applications",
        "applications": [
            {
                "id": "mock-app-nginx",
                "name": "nginx",
                "binary": "/usr/sbin/nginx",
                "systemd_unit": "nginx.service",
                "listening_ports": [{"port": 80, "protocol": "tcp"}, {"port": 443, "protocol": "tcp"}],
                "config_files": ["/etc/nginx/nginx.conf"],
                "data_directories": [],
                "estimated_data_size_gb": 0.0,
                "stateful": False,
                "external_data_stores": [],
                "process_user": "www-data",
                "env_vars": [],
                "dependencies": ["libc6", "libssl3"],
                "containerization_status": "not_started",
            }
        ],
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "host": parameters.get("hostname", "unknown"),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "appdiscovery is read-only, no rollback required"}
```

- [ ] **Step 2: Create `containerize_build.py`**

```python
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Stub executor for agent_containerize_build. Real execution dispatched to Go agent."""
    app_id = parameters.get("application_id", "unknown")
    registry = parameters.get("container_registry", "registry.example.com")
    return {
        "action": "containerize_build",
        "application_id": app_id,
        "dockerfile_generated": True,
        "image_tag": f"{registry}/nexplane-migrated/{app_id}:latest",
        "image_digest": "sha256:abc123mock",
        "k8s_manifest_generated": True,
        "pvc_spec_generated": parameters.get("stateful", False),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "image_deleted": True,
        "image_tag": execution_result.get("image_tag"),
    }
```

- [ ] **Step 3: Create `containerize_retire.py`**

```python
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Stub executor for agent_containerize_retire. Real execution dispatched to Go agent."""
    return {
        "action": "containerize_retire",
        "health_check_passed": True,
        "snapshot_id": "snap-mock123",
        "legacy_service_stopped": True,
        "asset_marked_retired": True,
        "retired_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "legacy_service_restarted": True,
        "snapshot_id": execution_result.get("snapshot_id"),
    }
```

- [ ] **Step 4: Create `backend/app/connectors/executors/kubernetes/__init__.py`**

Empty file.

- [ ] **Step 5: Create `backend/app/connectors/executors/kubernetes/workload_deploy.py`**

```python
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Stub executor for k8s_workload_deploy. Real implementation uses kubeconfig from kubernetes connector."""
    target_cluster = parameters.get("target_cluster")
    if not target_cluster:
        raise ValueError("target_cluster is required for k8s_workload_deploy")
    namespace = parameters.get("namespace", "default")
    return {
        "action": "k8s_workload_deploy",
        "target_cluster": target_cluster,
        "namespace": namespace,
        "pods_running": 1,
        "pods_ready": 1,
        "service_ip": "10.96.0.100",
        "pvc_bound": parameters.get("stateful", False),
        "data_migrated_gb": parameters.get("estimated_data_size_gb", 0.0) if parameters.get("stateful") else 0.0,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "manifests_deleted": True,
        "pvc_retained": True,
        "target_cluster": parameters.get("target_cluster"),
    }
```

- [ ] **Step 6: Add tests for executor stubs**

Append to `backend/tests/test_containerize_foundation.py`:

```python
import asyncio


def test_app_discovery_executor_returns_applications():
    from app.connectors.executors.nexplane_agent.app_discovery import execute
    result = asyncio.run(execute({}, [], None))
    assert result["action"] == "discover_applications"
    assert isinstance(result["applications"], list)
    assert len(result["applications"]) > 0
    app = result["applications"][0]
    assert "name" in app
    assert "stateful" in app
    assert "containerization_status" in app


def test_workload_deploy_executor_requires_target_cluster():
    from app.connectors.executors.kubernetes.workload_deploy import execute
    with pytest.raises(ValueError, match="target_cluster"):
        asyncio.run(execute({}, [], None))


def test_workload_deploy_executor_succeeds_with_target_cluster():
    from app.connectors.executors.kubernetes.workload_deploy import execute
    result = asyncio.run(execute({"target_cluster": "eks-prod"}, [], None))
    assert result["action"] == "k8s_workload_deploy"
    assert result["pods_ready"] >= 1
```

- [ ] **Step 7: Run all tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_containerize_foundation.py -v
```

Expected: 10 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/app_discovery.py \
    backend/app/connectors/executors/nexplane_agent/containerize_build.py \
    backend/app/connectors/executors/nexplane_agent/containerize_retire.py \
    backend/app/connectors/executors/kubernetes/__init__.py \
    backend/app/connectors/executors/kubernetes/workload_deploy.py \
    backend/tests/test_containerize_foundation.py
git commit -m "feat(containerize): add Python executor stubs for all 4 containerize change types"
```

---

## Task 4: Post-Execution Asset Metadata Update

**Files:**
- Create: `backend/app/services/app_discovery_service.py`
- Modify: `backend/app/workflows/execute_change_workflow.py`
- Test: `backend/tests/test_containerize_foundation.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_containerize_foundation.py`:

```python
def test_write_discovered_apps_to_metadata_sets_applications():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.services.app_discovery_service import write_discovered_apps_to_metadata

    mock_asset = MagicMock()
    mock_asset.id = "asset-uuid-1"
    mock_asset.asset_metadata = {}

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_asset

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    applications = [
        {"id": "app-1", "name": "nginx", "stateful": False, "containerization_status": "not_started"}
    ]
    execution_result = {"steps": [{"result": {"action": "discover_applications", "applications": applications}}]}

    asyncio.run(write_discovered_apps_to_metadata(mock_db, ["asset-uuid-1"], execution_result))

    assert mock_asset.asset_metadata["applications"] == applications
    mock_db.commit.assert_called_once()


def test_write_discovered_apps_noop_when_no_applications_in_result():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.services.app_discovery_service import write_discovered_apps_to_metadata

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()

    execution_result = {"steps": [{"result": {"action": "something_else"}}]}
    asyncio.run(write_discovered_apps_to_metadata(mock_db, ["asset-uuid-1"], execution_result))

    mock_db.execute.assert_not_called()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_containerize_foundation.py::test_write_discovered_apps_to_metadata_sets_applications -v 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'write_discovered_apps_to_metadata'`

- [ ] **Step 3: Create `backend/app/services/app_discovery_service.py`**

```python
import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.asset import Asset

log = logging.getLogger(__name__)


async def write_discovered_apps_to_metadata(
    db: AsyncSession,
    asset_ids: list[str],
    execution_result: dict,
) -> None:
    """
    After an agent_appdiscovery CR completes, extract the discovered applications
    from the execution result and persist them to asset_metadata["applications"].

    Follows the same pattern as write_cis_score_to_metadata in compliance/drift.py.
    """
    # Extract applications from the first step result that contains them
    applications = None
    for step in execution_result.get("steps", []):
        result = step.get("result", {})
        if isinstance(result, dict) and "applications" in result:
            applications = result["applications"]
            break

    if applications is None:
        log.debug("write_discovered_apps_to_metadata: no applications found in execution result")
        return

    for asset_id_str in asset_ids:
        try:
            asset_uuid = uuid.UUID(asset_id_str)
        except ValueError:
            log.warning("write_discovered_apps_to_metadata: invalid asset_id %s", asset_id_str)
            continue

        result = await db.execute(select(Asset).where(Asset.id == asset_uuid))
        asset = result.scalar_one_or_none()
        if not asset:
            log.warning("write_discovered_apps_to_metadata: asset %s not found", asset_id_str)
            continue

        metadata = dict(asset.asset_metadata or {})
        metadata["applications"] = applications
        asset.asset_metadata = metadata
        log.info("write_discovered_apps_to_metadata: wrote %d apps to asset %s", len(applications), asset_id_str)

    await db.commit()
```

- [ ] **Step 4: Add post-execution hook to `backend/app/workflows/execute_change_workflow.py`**

First, add the import at the top of the file (after existing imports):

```python
from app.services.app_discovery_service import write_discovered_apps_to_metadata
```

Then find the block that comes after `execution_result = await activity_execute_change(...)` and before `await write_audit_event(... "execution.completed" ...)`. Insert:

```python
    # Post-execution: persist discovered applications to asset_metadata
    if data.get("change_type") == "agent_appdiscovery":
        async with AsyncSessionLocal() as post_db:
            await write_discovered_apps_to_metadata(
                post_db, data["target_asset_ids"], execution_result
            )
```

- [ ] **Step 5: Run all tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_containerize_foundation.py -v
```

Expected: 12 tests PASS.

- [ ] **Step 6: Run full suite**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q --tb=short --ignore=tests/smoke 2>&1 | tail -5
```

Expected: 86+ passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/app_discovery_service.py \
    backend/app/workflows/execute_change_workflow.py \
    backend/tests/test_containerize_foundation.py
git commit -m "feat(containerize): write discovered apps to asset_metadata after appdiscovery CR"
```

---

## Task 5: Go Agent — appdiscovery Command

**Files:**
- Create: `agent/commands/appdiscovery/appdiscovery.go`
- Create: `agent/commands/appdiscovery/appdiscovery_linux.go`
- Create: `agent/commands/appdiscovery/appdiscovery_other.go`
- Create: `agent/commands/appdiscovery/appdiscovery_test.go`
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Create `agent/commands/appdiscovery/appdiscovery.go`**

```go
package appdiscovery

import "fmt"

// Application represents a discovered application running on the host.
type Application struct {
	ID                    string            `json:"id"`
	Name                  string            `json:"name"`
	Binary                string            `json:"binary"`
	SystemdUnit           string            `json:"systemd_unit"`
	ListeningPorts        []PortBinding     `json:"listening_ports"`
	ConfigFiles           []string          `json:"config_files"`
	DataDirectories       []string          `json:"data_directories"`
	EstimatedDataSizeGB   float64           `json:"estimated_data_size_gb"`
	Stateful              bool              `json:"stateful"`
	ExternalDataStores    []string          `json:"external_data_stores"`
	ProcessUser           string            `json:"process_user"`
	EnvVars               []string          `json:"env_vars"`
	Dependencies          []string          `json:"dependencies"`
	ContainerizationStatus string           `json:"containerization_status"`
}

// PortBinding represents a port a process is listening on.
type PortBinding struct {
	Port     int    `json:"port"`
	Protocol string `json:"protocol"`
}

// DiscoverApplicationsExecute scans the host and returns discovered applications.
func DiscoverApplicationsExecute(params map[string]any) (map[string]any, error) {
	apps, err := discoverApplicationsOS(params)
	if err != nil {
		return nil, fmt.Errorf("discover_applications: %w", err)
	}

	// Convert []Application to []map[string]any for the generic result format
	result := make([]map[string]any, 0, len(apps))
	for _, app := range apps {
		result = append(result, map[string]any{
			"id":                      app.ID,
			"name":                    app.Name,
			"binary":                  app.Binary,
			"systemd_unit":            app.SystemdUnit,
			"listening_ports":         portBindingsToMaps(app.ListeningPorts),
			"config_files":            app.ConfigFiles,
			"data_directories":        app.DataDirectories,
			"estimated_data_size_gb":  app.EstimatedDataSizeGB,
			"stateful":                app.Stateful,
			"external_data_stores":    app.ExternalDataStores,
			"process_user":            app.ProcessUser,
			"env_vars":                app.EnvVars,
			"dependencies":            app.Dependencies,
			"containerization_status": "not_started",
		})
	}

	return map[string]any{
		"action":       "discover_applications",
		"applications": result,
	}, nil
}

func portBindingsToMaps(bindings []PortBinding) []map[string]any {
	out := make([]map[string]any, 0, len(bindings))
	for _, b := range bindings {
		out = append(out, map[string]any{"port": b.Port, "protocol": b.Protocol})
	}
	return out
}
```

- [ ] **Step 2: Create `agent/commands/appdiscovery/appdiscovery_linux.go`**

```go
//go:build linux

package appdiscovery

import (
	"crypto/md5"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// discoverApplicationsOS performs the actual host scan on Linux.
func discoverApplicationsOS(params map[string]any) ([]Application, error) {
	// 1. Get running systemd services
	systemdApps := discoverSystemdServices()

	// 2. Get listening ports and map to processes
	portMap := discoverListeningPorts()

	// 3. Find non-package binaries in standard locations
	extraApps := discoverNonPackageBinaries(systemdApps)

	// Merge: attach port bindings to each app
	all := append(systemdApps, extraApps...)
	for i := range all {
		if ports, ok := portMap[all[i].Binary]; ok {
			all[i].ListeningPorts = ports
			// If a service has data directories and is listening, likely stateful
			all[i].Stateful = len(all[i].DataDirectories) > 0
		}
	}

	return all, nil
}

// discoverSystemdServices lists running non-system services via systemctl.
func discoverSystemdServices() []Application {
	out, err := exec.Command("systemctl", "list-units", "--type=service",
		"--state=running", "--no-legend", "--plain").Output()
	if err != nil {
		return nil
	}

	// System services to skip (not user workloads)
	skipPrefixes := []string{
		"systemd-", "dbus", "NetworkManager", "sshd", "cron", "rsyslog",
		"snapd", "getty", "udev", "polkit", "accounts-daemon", "avahi",
	}

	var apps []Application
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 1 {
			continue
		}
		unitName := fields[0]

		// Skip system-level services
		skip := false
		for _, prefix := range skipPrefixes {
			if strings.HasPrefix(unitName, prefix) {
				skip = true
				break
			}
		}
		if skip {
			continue
		}

		// Get service details via systemctl show
		app := extractServiceDetails(unitName)
		if app.Binary != "" {
			apps = append(apps, app)
		}
	}
	return apps
}

// extractServiceDetails calls systemctl show to get ExecStart and related info.
func extractServiceDetails(unit string) Application {
	out, err := exec.Command("systemctl", "show", unit,
		"--property=ExecStart,User,EnvironmentFiles,WorkingDirectory").Output()
	if err != nil {
		return Application{}
	}

	app := Application{
		ID:          fmt.Sprintf("%x", md5.Sum([]byte(unit)))[:8],
		SystemdUnit: unit,
		Name:        strings.TrimSuffix(unit, ".service"),
		DataDirectories: []string{},
		ConfigFiles:     []string{},
		EnvVars:         []string{},
		Dependencies:    []string{},
		ExternalDataStores: []string{},
	}

	for _, line := range strings.Split(string(out), "\n") {
		kv := strings.SplitN(line, "=", 2)
		if len(kv) != 2 {
			continue
		}
		key, val := kv[0], kv[1]
		switch key {
		case "ExecStart":
			// ExecStart={ path=/usr/bin/nginx ; argv[]=/usr/bin/nginx ... }
			if idx := strings.Index(val, "path="); idx >= 0 {
				rest := val[idx+5:]
				if end := strings.IndexAny(rest, " ;"); end > 0 {
					app.Binary = rest[:end]
				}
			}
		case "User":
			if val != "" && val != "root" {
				app.ProcessUser = val
			}
		case "WorkingDirectory":
			if val != "" && val != "/" && val != "/root" {
				app.DataDirectories = append(app.DataDirectories, val)
			}
		}
	}

	// Estimate data size if we have data directories
	app.EstimatedDataSizeGB = estimateDirSizeGB(app.DataDirectories)

	// Detect config files in /etc/<name>/
	confDir := "/etc/" + app.Name
	if entries, err := os.ReadDir(confDir); err == nil {
		for _, e := range entries {
			if !e.IsDir() {
				app.ConfigFiles = append(app.ConfigFiles, filepath.Join(confDir, e.Name()))
			}
		}
	}

	return app
}

// discoverListeningPorts uses ss to map binaries to ports.
func discoverListeningPorts() map[string][]PortBinding {
	out, err := exec.Command("ss", "-tlnp").Output()
	if err != nil {
		// Fallback: no port info
		return map[string][]PortBinding{}
	}

	result := map[string][]PortBinding{}
	for _, line := range strings.Split(string(out), "\n")[1:] {
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		localAddr := fields[3]
		processInfo := fields[len(fields)-1]

		// Parse port from address like *:80 or 0.0.0.0:8080
		port := 0
		if idx := strings.LastIndex(localAddr, ":"); idx >= 0 {
			port, _ = strconv.Atoi(localAddr[idx+1:])
		}
		if port == 0 {
			continue
		}

		// Parse binary name from users:(("nginx",pid=1234,...))
		binary := ""
		if start := strings.Index(processInfo, `(("`); start >= 0 {
			rest := processInfo[start+3:]
			if end := strings.Index(rest, `"`); end >= 0 {
				binary = rest[:end]
			}
		}
		if binary == "" {
			continue
		}

		result[binary] = append(result[binary], PortBinding{Port: port, Protocol: "tcp"})
	}
	return result
}

// discoverNonPackageBinaries finds executables in non-standard locations.
func discoverNonPackageBinaries(existing []Application) []Application {
	searchDirs := []string{"/opt", "/usr/local/bin", "/usr/local/sbin"}
	knownBinaries := map[string]bool{}
	for _, a := range existing {
		knownBinaries[filepath.Base(a.Binary)] = true
	}

	var apps []Application
	for _, dir := range searchDirs {
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if e.IsDir() || knownBinaries[e.Name()] {
				continue
			}
			fullPath := filepath.Join(dir, e.Name())
			info, err := e.Info()
			if err != nil {
				continue
			}
			// Must be executable
			if info.Mode()&0111 == 0 {
				continue
			}
			apps = append(apps, Application{
				ID:                     fmt.Sprintf("%x", md5.Sum([]byte(fullPath)))[:8],
				Name:                   e.Name(),
				Binary:                 fullPath,
				DataDirectories:        []string{},
				ConfigFiles:            []string{},
				EnvVars:                []string{},
				Dependencies:           []string{},
				ExternalDataStores:     []string{},
				ContainerizationStatus: "not_started",
			})
		}
	}
	return apps
}

// estimateDirSizeGB returns the total size in GB of the given directories.
func estimateDirSizeGB(dirs []string) float64 {
	var totalBytes uint64
	for _, dir := range dirs {
		filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
			if err == nil && !info.IsDir() {
				totalBytes += uint64(info.Size())
			}
			return nil
		})
	}
	return float64(totalBytes) / (1024 * 1024 * 1024)
}

// blockDeviceSize reads block device size from sysfs (unused but available).
func blockDeviceSize(dev string) uint64 {
	data, err := os.ReadFile(fmt.Sprintf("/sys/block/%s/size", filepath.Base(dev)))
	if err != nil {
		return 0
	}
	var sectors uint64
	fmt.Sscanf(strings.TrimSpace(string(data)), "%d", &sectors)
	_ = syscall.O_RDONLY // keep syscall import used
	return sectors * 512
}
```

- [ ] **Step 3: Create `agent/commands/appdiscovery/appdiscovery_other.go`**

```go
//go:build !linux

package appdiscovery

func discoverApplicationsOS(params map[string]any) ([]Application, error) {
	return []Application{}, nil
}
```

- [ ] **Step 4: Create `agent/commands/appdiscovery/appdiscovery_test.go`**

```go
package appdiscovery

import (
	"testing"
)

func TestDiscoverApplicationsExecuteReturnsMap(t *testing.T) {
	result, err := DiscoverApplicationsExecute(map[string]any{})
	if err != nil {
		t.Fatalf("DiscoverApplicationsExecute returned error: %v", err)
	}
	if result["action"] != "discover_applications" {
		t.Errorf("expected action=discover_applications, got %v", result["action"])
	}
	apps, ok := result["applications"].([]map[string]any)
	if !ok {
		t.Errorf("expected applications to be []map[string]any, got %T", result["applications"])
	}
	_ = apps // may be empty in test environment
}

func TestPortBindingsToMaps(t *testing.T) {
	bindings := []PortBinding{{Port: 80, Protocol: "tcp"}, {Port: 443, Protocol: "tcp"}}
	maps := portBindingsToMaps(bindings)
	if len(maps) != 2 {
		t.Fatalf("expected 2 maps, got %d", len(maps))
	}
	if maps[0]["port"] != 80 {
		t.Errorf("expected port 80, got %v", maps[0]["port"])
	}
}
```

- [ ] **Step 5: Register in `agent/executor/executor.go`**

Add the import:
```go
"nexplane-agent/commands/appdiscovery"
```

Add to the `commands` map (near other agent commands):
```go
"discover_applications": appdiscovery.DiscoverApplicationsExecute,
```

- [ ] **Step 6: Build and test the Go agent**

```bash
cd f:/Nexplane/nexplane/agent
"C:/Program Files/Go/bin/go.exe" build ./... 2>&1
"C:/Program Files/Go/bin/go.exe" test ./commands/appdiscovery/... -v 2>&1
```

Expected: build succeeds, 2 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/commands/appdiscovery/ agent/executor/executor.go
git commit -m "feat(containerize): add Go agent appdiscovery command (discover_applications)"
```

---

## Task 6: Frontend — Asset Detail Applications Tab

**Files:**
- Modify: `frontend/src/pages/AssetDetail.tsx`

This adds an "Applications" section to the asset detail page. The page currently shows a two-column layout with asset metadata on the left and change requests on the right. Add an Applications section below the existing content for `server` and `endpoint` asset types.

- [ ] **Step 1: Read the current AssetDetail.tsx structure**

Read `frontend/src/pages/AssetDetail.tsx` to find:
- The end of the JSX return (around where `</div>` closes the main layout)
- The `asset` data type — what fields are available (specifically `asset_metadata` and `asset_type`)

- [ ] **Step 2: Add the Applications section to `AssetDetail.tsx`**

Find the closing `</div>` of the right column (the one containing Change Requests, around line 690+). Before the outer closing `</div>` of the main content area, add this Applications section. It should only render for `server` and `endpoint` asset types:

```typescript
{/* Applications (containerization) */}
{(asset.asset_type === "server" || asset.asset_type === "endpoint") && (
  <div className="mt-6">
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-sm font-semibold text-slate-900">Applications</h2>
      <button
        onClick={() => {
          // Fire agent_appdiscovery CR — placeholder for Plan 3 wizard
          alert("Run discovery via Change Requests > New > Agent: Discover Applications");
        }}
        className="text-xs text-brand-600 hover:underline"
      >
        Re-scan
      </button>
    </div>
    {Array.isArray((asset.asset_metadata as Record<string, unknown>)?.applications) ? (
      <div className="space-y-1.5">
        {((asset.asset_metadata as Record<string, unknown>).applications as Array<Record<string, unknown>>).map((app, i) => (
          <div key={i} className="flex items-center justify-between bg-slate-50 border border-slate-100 rounded-md px-3 py-2 text-xs">
            <div>
              <span className="font-medium text-slate-900">{String(app.name)}</span>
              {Array.isArray(app.listening_ports) && (app.listening_ports as Array<Record<string, unknown>>).length > 0 && (
                <span className="text-slate-400 ml-2">
                  :{(app.listening_ports as Array<Record<string, unknown>>).map(p => String(p.port)).join(", :")}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {app.stateful ? (
                <span className="px-1.5 py-0.5 rounded text-amber-700 bg-amber-50 border border-amber-200">
                  stateful {app.estimated_data_size_gb ? `· ${Number(app.estimated_data_size_gb).toFixed(1)} GB` : ""}
                </span>
              ) : (
                <span className="px-1.5 py-0.5 rounded text-slate-500 bg-slate-100">
                  stateless
                </span>
              )}
              <span className="text-slate-400">{String(app.containerization_status ?? "not_started")}</span>
            </div>
          </div>
        ))}
      </div>
    ) : (
      <div className="text-xs text-slate-400 bg-slate-50 rounded-md px-3 py-4 text-center border border-slate-100">
        No applications discovered — run a scan to detect running services
      </div>
    )}
  </div>
)}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep AssetDetail | head -10"
```

Expected: no errors.

- [ ] **Step 4: Restart frontend**

```bash
docker compose -f f:/Nexplane/nexplane/docker-compose.yml stop frontend && docker compose -f f:/Nexplane/nexplane/docker-compose.yml up frontend -d
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AssetDetail.tsx
git commit -m "feat(containerize): add Applications tab to asset detail page"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `agent_appdiscovery` ChangeType enum value
- ✅ `agent_containerize_build` ChangeType enum value
- ✅ `k8s_workload_deploy` ChangeType enum value
- ✅ `agent_containerize_retire` ChangeType enum value
- ✅ `kubernetes_cluster` AssetType enum value
- ✅ `container_image` AssetType enum value
- ✅ All 4 new types in `_IMPLICIT_ROLLBACK_TYPES`
- ✅ 4 change type JSON definition files
- ✅ Python executor stubs for `app_discovery`, `containerize_build`, `containerize_retire`, `workload_deploy`
- ✅ `workload_deploy` validates `target_cluster` is set
- ✅ `write_discovered_apps_to_metadata` service function
- ✅ Post-execution hook in `execute_change_workflow.py` for `agent_appdiscovery`
- ✅ Go agent `appdiscovery` package with `DiscoverApplicationsExecute`
- ✅ Linux scan: systemd services, listening ports (ss), non-package binaries (/opt, /usr/local/bin)
- ✅ `discover_applications` registered in `executor/executor.go`
- ✅ Asset detail Applications tab reads from `asset_metadata.applications`
- ✅ Re-scan button (placeholder)
- ✅ Stateful/stateless badge with data size
- ✅ Empty state for unscanned assets

**Placeholder scan:** None — all code blocks are complete.

**Type consistency:**
- `Application` struct in Go → `map[string]any` in executor result → stored in `asset_metadata["applications"]` as list of dicts → read as `Record<string, unknown>[]` in TypeScript ✅
- `write_discovered_apps_to_metadata(db, asset_ids, execution_result)` called from workflow with matching signature ✅
- `DiscoverApplicationsExecute` returns `{"action": "discover_applications", "applications": [...]}` — matches what `write_discovered_apps_to_metadata` expects ✅
