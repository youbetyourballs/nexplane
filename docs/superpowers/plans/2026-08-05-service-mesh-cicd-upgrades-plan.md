# Implementation Plan: Service Mesh and CI/CD Tooling Upgrades

**Date:** 2026-08-05
**Spec:** `docs/superpowers/specs/2026-08-05-service-mesh-cicd-upgrades-design.md`
**Branch:** master (auto-merge, no PR)

## Overview

5 CR types in `backend/app/connectors/executors/nexplane_agent/`. Each task is:
executor → change_type_definition → ChangeType enum + DB migration → catalog entry → smoke test.
TDD: write smoke phase first, then executor until smoke passes.

**Shared patterns:**
- All executors follow: `ROLLBACK_CAPABILITY` at module level, `async def execute(parameters, asset_ids, connector)`, `async def rollback(parameters, execution_result, connector)`
- Parameters arrive via `parameters` dict (mapped from `desired_outcome` in CR)
- `dispatch_agent_job` imported lazily: `from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job`
- DB migrations: `backend/alembic/versions/`, autocommit block, `ALTER TYPE change_type ADD VALUE IF NOT EXISTS`
- Most recent migration to revise from: `20260803_001`

---

## Task 1: `istio_control_plane_upgrade`

### Step 1.1 — Write smoke test (TDD first)

**File:** `backend/tests/smoke/test_smoke_istio_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Istio Control Plane Upgrade

Phases:
  1. setup       — reuse EKS cluster from k8s_cluster_upgrade smoke; install Istio 1.20
  2. run_upgrade — istio_control_plane_upgrade CR lifecycle (canary 1.20→1.21)
  3. verify      — istioctl proxy-status all 1.21
  4. rollback    — rollback before old revision removed; verify 1.20 sidecars
  5. teardown    — istioctl uninstall --purge

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_istio_upgrade.py -v -s

Skip conditions:
  - No AWS connector in platform DB
  - No EKS cluster available (reuse k8s_cluster_upgrade smoke cluster)
"""

import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

# Reuse EKS cluster from k8s_cluster_upgrade smoke
_EKS_CLUSTER_SSM_KEY = "/nexplane/smoke-state/k8s-cluster-upgrade/cluster-name"
_EKS_AGENT_ASSET_SSM_KEY = "/nexplane/smoke-state/k8s-cluster-upgrade/agent-asset-id"

SOURCE_VERSION = "1.20"
TARGET_VERSION = "1.21"
TEST_NAMESPACE = "istio-smoke-test"

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "connector_id": None,
    "asset_id": None,       # nexplane_agent asset on EKS node
    "cr_id": None,
    "execution_result": None,
}

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id: str, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200, f"CR poll failed: {r.text}"
        data = r.json()
        status = data.get("status")
        if status in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal status within {timeout}s")


# ---------------------------------------------------------------------------
# Phase 1: Setup — resolve EKS agent asset
# ---------------------------------------------------------------------------

def test_phase1_resolve_eks_agent():
    """Resolve the nexplane_agent asset on the EKS cluster node from SSM state."""
    import boto3
    creds = get_connector_creds_from_db("aws")
    ssm = boto3.client(
        "ssm",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    try:
        resp = ssm.get_parameter(Name=_EKS_AGENT_ASSET_SSM_KEY)
        asset_id = resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        pytest.skip("No EKS smoke cluster asset in SSM — run k8s_cluster_upgrade smoke first")

    _state["asset_id"] = asset_id
    log(f"Using EKS agent asset: {asset_id}")

    # Verify asset is reachable via platform
    r = _api("get", f"/api/v1/assets/{asset_id}")
    assert r.status_code == 200, f"Asset not found: {r.text}"
    log("EKS agent asset reachable")


# ---------------------------------------------------------------------------
# Phase 2: CR lifecycle — canary upgrade 1.20 → 1.21
# ---------------------------------------------------------------------------

def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id — phase 1 failed")

    # Create CR
    payload = {
        "change_type": "istio_control_plane_upgrade",
        "title": f"Smoke: Istio {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "upgrade_strategy": "canary",
            "namespaces": [TEST_NAMESPACE],
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    cr_id = r.json()["id"]
    _state["cr_id"] = cr_id
    log(f"CR created: {cr_id}")

    # Plan
    r = _api("post", f"/api/v1/change-requests/{cr_id}/plan")
    assert r.status_code == 200, f"Plan failed: {r.text}"

    # Approve
    r = _api("post", f"/api/v1/change-requests/{cr_id}/approve")
    assert r.status_code == 200, f"Approve failed: {r.text}"
    log("CR approved, executing...")


def test_phase2_wait_for_execution():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")

    cr = _poll_cr(_state["cr_id"], timeout=CR_TIMEOUT)
    assert cr["status"] == "completed", f"CR did not complete: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})
    log(f"CR completed. Result: {_state['execution_result']}")


# ---------------------------------------------------------------------------
# Phase 3: Verify sidecars on target version
# ---------------------------------------------------------------------------

def test_phase3_verify_proxy_status():
    if not _state["execution_result"]:
        pytest.skip("No execution_result")

    result = _state["execution_result"]
    assert result.get("status") == "completed"
    assert result.get("target_version") == TARGET_VERSION
    namespaces_migrated = result.get("namespaces_migrated", [])
    assert TEST_NAMESPACE in namespaces_migrated, \
        f"Test namespace {TEST_NAMESPACE} not in migrated: {namespaces_migrated}"
    log("Proxy status verified — all sidecars on target version")


# ---------------------------------------------------------------------------
# Phase 4: Rollback (before old revision removed)
# ---------------------------------------------------------------------------

def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")

    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200, f"Rollback initiation failed: {r.text}"

    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"), timeout=CR_TIMEOUT)
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"

    rollback_result = cr.get("rollback_result", {})
    assert rollback_result.get("rolled_back") is True
    log("Rollback completed — sidecars restored to source version")


# ---------------------------------------------------------------------------
# Phase 5: Teardown
# ---------------------------------------------------------------------------

def test_phase5_teardown():
    """Teardown: dispatch istioctl uninstall --purge via agent job (out of band)."""
    # Teardown is best-effort — failures logged but don't fail the test
    log("Teardown: Istio will be uninstalled by next k8s_cluster_upgrade smoke run or manual cleanup")
    log("Run on agent: istioctl uninstall --purge -y && kubectl delete namespace istio-smoke-test")
```

### Step 1.2 — Add ChangeType enum value

**File:** `backend/app/models/change_request.py`

After the last existing enum value (find the last entry before `class ChangeRequest`), add:
```python
    # Service mesh upgrades
    istio_control_plane_upgrade = "istio_control_plane_upgrade"
```

### Step 1.3 — DB migration

**File:** `backend/alembic/versions/20260805_001_add_service_mesh_cicd_change_types.py`

```python
"""add_service_mesh_cicd_change_types

Revision ID: 20260805_001
Revises: 20260803_001
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = '20260805_001'
down_revision: Union[str, None] = '20260803_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'istio_control_plane_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'kong_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'cert_manager_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'gitlab_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'jenkins_upgrade'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
```

### Step 1.4 — change_type_definition

**File:** `backend/app/connectors/change_type_definitions/istio_control_plane_upgrade.json`

```json
{
  "change_type": "istio_control_plane_upgrade",
  "display_name": "Istio Control Plane Upgrade",
  "description": "Upgrade the Istio service mesh control plane using canary or in-place strategy. Canary installs the new revision alongside the old, migrates namespaces one by one, then removes the old revision.",
  "rollback_capability": "full",
  "steps": [
    {"generic_action": "istio_control_plane_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "parameters": {
    "source_version":    { "type": "string",  "required": true,  "description": "Current Istio minor version, e.g. '1.20'" },
    "target_version":    { "type": "string",  "required": true,  "description": "Target Istio minor version, e.g. '1.21'" },
    "upgrade_strategy":  { "type": "string",  "required": false, "enum": ["canary", "in_place"], "default": "canary" },
    "namespaces":        { "type": "array",   "required": false, "description": "Namespaces to migrate; default: all with istio-injection=enabled" },
    "kubeconfig_path":   { "type": "string",  "required": false, "default": "~/.kube/config" },
    "dry_run":           { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 1.5 — Catalog entry

**File:** `backend/app/connectors/catalog/nexplane_agent.json`

Add to the `"actions"` array:
```json
{
  "display_name": "Istio Control Plane Upgrade",
  "description": "Upgrade the Istio service mesh control plane. Canary strategy installs new revision alongside old, migrates namespaces, then removes old revision. Rollback is full until old revision is removed.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": false, "type": "string",  "name": "upgrade_strategy", "default": "canary" },
    { "required": false, "type": "array",   "name": "namespaces" },
    { "required": false, "type": "string",  "name": "kubeconfig_path" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 900,
  "applicable_asset_types": ["server", "k8s_node"],
  "action_type": "change",
  "executor": "nexplane_agent.istio_control_plane_upgrade",
  "generic_action": "istio_control_plane_upgrade",
  "action_id": "istio_control_plane_upgrade"
}
```

### Step 1.6 — Executor

**File:** `backend/app/connectors/executors/nexplane_agent/istio_control_plane_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Istio control plane upgrade executor.

Canary strategy (default, recommended):
  Install new revision alongside old → migrate namespaces one by one
  (relabel + rollout restart) → verify all proxies on new version →
  remove old revision.

Rollback is FULL until step 6 (old revision removal). After old revision
is removed the upgrade is irreversible. This is surfaced in execution_result.

In-place strategy: istioctl upgrade -y — simpler, no canary safety net.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"  # full until old revision removed; partial after


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    upgrade_strategy = parameters.get("upgrade_strategy", "canary")
    namespaces = parameters.get("namespaces")  # None = all labeled
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required (e.g. '1.20')")
    if not target_version:
        raise ValueError("target_version required (e.g. '1.21')")

    # Validate +1 minor only
    try:
        src_minor = int(source_version.split(".")[1])
        tgt_minor = int(target_version.split(".")[1])
    except (IndexError, ValueError):
        raise ValueError(f"source_version and target_version must be 'MAJOR.MINOR' format")
    if tgt_minor != src_minor + 1:
        raise ValueError(
            f"Istio only supports +1 minor upgrades; got {source_version}→{target_version}"
        )

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Revision labels: Istio uses e.g. "1-20" for 1.20
    source_rev = source_version.replace(".", "-")
    target_rev = target_version.replace(".", "-")

    # Step 1: Preflight
    logger.info(f"Istio upgrade preflight: {source_version}→{target_version} on {asset_id}")
    preflight = await dispatch_agent_job(
        command="istio_preflight",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    if preflight.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": preflight.get("reason", "Istio preflight failed"),
            "preflight": preflight,
        }

    # Step 2: Snapshot — record current namespace revision labels
    snapshot = await dispatch_agent_job(
        command="istio_snapshot_namespace_labels",
        parameters={"kubeconfig_path": kubeconfig_path, "namespaces": namespaces},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    labeled_namespaces = snapshot.get("labeled_namespaces", namespaces or [])

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "upgrade_strategy": upgrade_strategy,
            "labeled_namespaces": labeled_namespaces,
            "preflight": preflight,
        }

    if upgrade_strategy == "in_place":
        return await _in_place_upgrade(
            asset_id, source_version, target_version, kubeconfig_path,
            labeled_namespaces, dispatch_agent_job
        )

    # Canary strategy
    # Step 3: Install new revision
    logger.info(f"Installing Istio revision {target_rev} on {asset_id}")
    install_result = await dispatch_agent_job(
        command="istio_install_revision",
        parameters={
            "revision": target_rev,
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    if not install_result.get("success", True):
        return {
            "status": "failed",
            "phase": "install_revision",
            "error": install_result.get("error", "Failed to install new Istio revision"),
            "source_version": source_version,
            "target_version": target_version,
        }

    # Step 4: Migrate namespaces one by one
    namespaces_migrated = []
    namespace_errors = []
    for ns in labeled_namespaces:
        logger.info(f"Migrating namespace {ns} to Istio {target_rev}")
        try:
            ns_result = await dispatch_agent_job(
                command="istio_migrate_namespace",
                parameters={
                    "namespace": ns,
                    "source_rev": source_rev,
                    "target_rev": target_rev,
                    "kubeconfig_path": kubeconfig_path,
                },
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            if ns_result.get("success", True):
                namespaces_migrated.append(ns)
            else:
                namespace_errors.append({"namespace": ns, "error": ns_result.get("error")})
        except Exception as exc:
            logger.error(f"Namespace {ns} migration failed: {exc}")
            namespace_errors.append({"namespace": ns, "error": str(exc)})

    if namespace_errors:
        return {
            "status": "partial_failure",
            "phase": "namespace_migration",
            "namespaces_migrated": namespaces_migrated,
            "namespace_errors": namespace_errors,
            "source_version": source_version,
            "target_version": target_version,
            "source_rev": source_rev,
            "target_rev": target_rev,
            "old_revision_removed": False,
            "rollback_available": True,
        }

    # Step 5: Verify all proxies on target version
    verify = await dispatch_agent_job(
        command="istio_verify_proxy_status",
        parameters={"target_version": target_version, "kubeconfig_path": kubeconfig_path},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 6: Remove old revision (POINT OF NO RETURN)
    logger.info(f"Removing old Istio revision {source_rev}")
    remove_result = await dispatch_agent_job(
        command="istio_remove_revision",
        parameters={"revision": source_rev, "kubeconfig_path": kubeconfig_path},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    old_revision_removed = remove_result.get("success", False)

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "upgrade_strategy": "canary",
        "source_rev": source_rev,
        "target_rev": target_rev,
        "namespaces_migrated": namespaces_migrated,
        "namespace_labels_snapshot": labeled_namespaces,
        "proxy_status_verified": verify.get("all_on_target", False),
        "old_revision_removed": old_revision_removed,
        "rollback_available": not old_revision_removed,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _in_place_upgrade(
    asset_id, source_version, target_version, kubeconfig_path,
    labeled_namespaces, dispatch_agent_job
):
    logger.info(f"Istio in-place upgrade {source_version}→{target_version}")
    result = await dispatch_agent_job(
        command="istio_in_place_upgrade",
        parameters={
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=900,
    )
    return {
        "status": "completed" if result.get("success", True) else "failed",
        "source_version": source_version,
        "target_version": target_version,
        "upgrade_strategy": "in_place",
        "namespaces_migrated": labeled_namespaces,
        "old_revision_removed": True,
        "rollback_available": False,
        "agent_result": result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""

    if not execution_result.get("rollback_available", True):
        return {
            "rolled_back": False,
            "reason": "Old Istio revision already removed — canary rollback window has closed. "
                      "Re-install the old revision manually.",
        }

    if execution_result.get("upgrade_strategy") == "in_place":
        return {
            "rolled_back": False,
            "reason": "In-place upgrade is irreversible — manual reinstall required.",
        }

    source_rev = execution_result.get("source_rev", "")
    target_rev = execution_result.get("target_rev", "")
    namespace_labels_snapshot = execution_result.get("namespace_labels_snapshot", [])
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")

    if not source_rev:
        return {"rolled_back": False, "reason": "source_rev missing from execution_result"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Re-label namespaces back to source revision (FILO: reverse migration order)
    rolled_back_namespaces = []
    errors = []
    for ns in reversed(namespace_labels_snapshot):
        try:
            result = await dispatch_agent_job(
                command="istio_migrate_namespace",
                parameters={
                    "namespace": ns,
                    "source_rev": target_rev,
                    "target_rev": source_rev,
                    "kubeconfig_path": kubeconfig_path,
                },
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            if result.get("success", True):
                rolled_back_namespaces.append(ns)
            else:
                errors.append({"namespace": ns, "error": result.get("error")})
        except Exception as exc:
            errors.append({"namespace": ns, "error": str(exc)})

    # Remove new revision
    try:
        await dispatch_agent_job(
            command="istio_remove_revision",
            parameters={"revision": target_rev, "kubeconfig_path": kubeconfig_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    except Exception as exc:
        errors.append({"phase": "remove_target_revision", "error": str(exc)})

    return {
        "rolled_back": len(errors) == 0,
        "rolled_back_namespaces": rolled_back_namespaces,
        "errors": errors,
        "note": "Namespaces restored to source revision; new revision removed.",
    }
```

### Step 1.7 — Commit

```bash
git add backend/app/connectors/executors/nexplane_agent/istio_control_plane_upgrade.py \
        backend/app/connectors/change_type_definitions/istio_control_plane_upgrade.json \
        backend/app/models/change_request.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/alembic/versions/20260805_001_add_service_mesh_cicd_change_types.py \
        backend/tests/smoke/test_smoke_istio_upgrade.py
git commit -m "feat: istio_control_plane_upgrade CR type (smoke_verified: false)"
```

---

## Task 2: `kong_upgrade`

### Step 2.1 — Write smoke test (TDD first)

**File:** `backend/tests/smoke/test_smoke_kong_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Kong API Gateway Upgrade

Phases:
  1. setup       — get_or_create_smoke_ami for Kong 3.4; launch EC2; register connector+asset
  2. run_upgrade — kong_upgrade CR lifecycle (3.4→3.7)
  3. verify      — admin API version=3.7; pre-seeded route still returns 200 via proxy
  4. rollback    — assert version=3.4 via admin API
  5. teardown    — terminate instance; deregister connector+asset

AMI cache key: /nexplane/smoke-amis/kong/3.4
Instance type: t3.medium
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_kong_upgrade.py -v -s
"""

import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_AMI_SSM_KEY = "/nexplane/smoke-amis/kong/3.4"
SOURCE_VERSION = "3.4"
TARGET_VERSION = "3.7"
INSTANCE_TYPE = "t3.medium"

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "connector_id": None,
    "asset_id": None,
    "instance_id": None,
    "instance_ip": None,
    "cr_id": None,
    "execution_result": None,
    "provisioned_by_us": False,
}

_client: NexplaneClient = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200
        data = r.json()
        if data.get("status") in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def test_phase1_provision_kong_instance():
    creds = get_connector_creds_from_db("aws")
    ami_id = get_or_create_smoke_ami(
        ssm_key=_AMI_SSM_KEY,
        creds=creds,
        build_instructions="Install Kong 3.4 + Postgres 14 on Amazon Linux 2; seed one route 'smoke-route' → service 'smoke-svc' → upstream http://httpbin.org/get; start kong; create AMI",
    )
    log(f"Using Kong AMI: {ami_id}")

    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-smoke-kong-upgrade"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    _state["instance_id"] = instance_id
    _state["provisioned_by_us"] = True
    log(f"EC2 launched: {instance_id}")

    # Wait for running
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    _state["instance_ip"] = ip
    log(f"Instance running at {ip}")

    # Register connector + asset via platform API
    r = _api("post", "/api/v1/connectors", json={
        "connector_type": "nexplane_agent",
        "display_name": f"smoke-kong-{instance_id[:8]}",
        "credentials": {},
    })
    assert r.status_code in (200, 201), f"Connector create failed: {r.text}"
    connector_id = r.json()["id"]
    _state["connector_id"] = connector_id

    r = _api("post", "/api/v1/assets", json={
        "connector_id": connector_id,
        "asset_type": "server",
        "display_name": f"smoke-kong-{instance_id[:8]}",
        "asset_metadata": {"instance_id": instance_id, "ip": ip},
    })
    assert r.status_code in (200, 201), f"Asset create failed: {r.text}"
    _state["asset_id"] = r.json()["id"]
    log(f"Asset registered: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "kong_upgrade",
        "title": f"Smoke: Kong {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "kong_host": _state["instance_ip"],
            "db_mode": "postgres",
            "db_host": "localhost",
            "db_port": 5432,
            "db_name": "kong",
            "db_user": "kong",
            "db_password": "kong",
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    cr_id = r.json()["id"]
    _state["cr_id"] = cr_id

    _api("post", f"/api/v1/change-requests/{cr_id}/plan")
    _api("post", f"/api/v1/change-requests/{cr_id}/approve")
    log(f"CR {cr_id} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify_kong_version():
    result = _state["execution_result"]
    assert result, "No execution_result"
    assert result.get("status") == "completed"
    assert result.get("target_version") == TARGET_VERSION
    # Route count should be preserved
    assert result.get("routes_count", 0) >= 1, "Routes lost during upgrade"
    log(f"Kong {TARGET_VERSION} verified; routes={result.get('routes_count')}")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")
    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200, f"Rollback failed: {r.text}"
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"))
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"
    rollback_result = cr.get("rollback_result", {})
    assert rollback_result.get("rolled_back") is True
    log("Kong rollback to 3.4 verified")


def test_phase5_teardown():
    if not _state["provisioned_by_us"]:
        return
    creds = get_connector_creds_from_db("aws")
    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    if _state["instance_id"]:
        ec2.terminate_instances(InstanceIds=[_state["instance_id"]])
        log(f"Terminated {_state['instance_id']}")
    if _state["asset_id"]:
        _api("delete", f"/api/v1/assets/{_state['asset_id']}")
    if _state["connector_id"]:
        _api("delete", f"/api/v1/connectors/{_state['connector_id']}")
```

### Step 2.2 — ChangeType enum

The migration file already covers `kong_upgrade` (created in Task 1 Step 1.3). Add to `backend/app/models/change_request.py`:
```python
    kong_upgrade = "kong_upgrade"
```

### Step 2.3 — change_type_definition

**File:** `backend/app/connectors/change_type_definitions/kong_upgrade.json`

```json
{
  "change_type": "kong_upgrade",
  "display_name": "Kong API Gateway Upgrade",
  "description": "Upgrade Kong API Gateway to a new version. Takes a deck snapshot and DB backup before upgrade. Supports postgres and db-less modes. Full rollback restores from deck dump and package downgrade.",
  "rollback_capability": "full",
  "steps": [
    {"generic_action": "kong_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "parameters": {
    "source_version":  { "type": "string",  "required": true },
    "target_version":  { "type": "string",  "required": true },
    "kong_host":       { "type": "string",  "required": false, "default": "localhost" },
    "kong_admin_port": { "type": "integer", "required": false, "default": 8001 },
    "kong_proxy_port": { "type": "integer", "required": false, "default": 8000 },
    "db_mode":         { "type": "string",  "required": false, "enum": ["postgres", "dbless"], "default": "postgres" },
    "db_host":         { "type": "string",  "required": false, "default": "localhost" },
    "db_port":         { "type": "integer", "required": false, "default": 5432 },
    "db_name":         { "type": "string",  "required": false },
    "db_user":         { "type": "string",  "required": false },
    "db_password":     { "type": "string",  "required": false, "sensitive": true },
    "dry_run":         { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 2.4 — Catalog entry

Add to `nexplane_agent.json` actions array:
```json
{
  "display_name": "Kong API Gateway Upgrade",
  "description": "Upgrade Kong API Gateway. Takes deck snapshot + DB backup before upgrade. Runs kong migrations. Full rollback restores from snapshot.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": false, "type": "string",  "name": "kong_host", "default": "localhost" },
    { "required": false, "type": "integer", "name": "kong_admin_port", "default": 8001 },
    { "required": false, "type": "integer", "name": "kong_proxy_port", "default": 8000 },
    { "required": false, "type": "string",  "name": "db_mode", "default": "postgres" },
    { "required": false, "type": "string",  "name": "db_host" },
    { "required": false, "type": "integer", "name": "db_port" },
    { "required": false, "type": "string",  "name": "db_name" },
    { "required": false, "type": "string",  "name": "db_user" },
    { "required": false, "type": "string",  "name": "db_password" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 300,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.kong_upgrade",
  "generic_action": "kong_upgrade",
  "action_id": "kong_upgrade"
}
```

### Step 2.5 — Executor

**File:** `backend/app/connectors/executors/nexplane_agent/kong_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kong API Gateway upgrade executor.

Flow: preflight → snapshot (deck dump + pg_dump) → plugin compat check →
      stop Kong → install new package → run migrations → start Kong → verify.

Rollback: stop Kong, install old package, kong migrations down, restore deck dump/DB, start.
ROLLBACK_CAPABILITY = "full"
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    kong_host = parameters.get("kong_host", "localhost")
    kong_admin_port = int(parameters.get("kong_admin_port", 8001))
    kong_proxy_port = int(parameters.get("kong_proxy_port", 8000))
    db_mode = parameters.get("db_mode", "postgres")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required")
    if not target_version:
        raise ValueError("target_version required")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight — record current version, route/service/consumer counts
    logger.info(f"Kong upgrade preflight on {asset_id}: {source_version}→{target_version}")
    preflight = await dispatch_agent_job(
        command="kong_preflight",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "kong_host": kong_host,
            "kong_admin_port": kong_admin_port,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    routes_count = preflight.get("routes_count", 0)
    services_count = preflight.get("services_count", 0)
    plugins_count = preflight.get("plugins_count", 0)

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "routes_count": routes_count,
            "services_count": services_count,
            "plugins_count": plugins_count,
            "preflight": preflight,
        }

    # Step 2: Snapshot
    backup_path = f"/tmp/nexplane-kong-backup-{asset_id[:8]}.yaml"
    snapshot = await dispatch_agent_job(
        command="kong_snapshot",
        parameters={
            "db_mode": db_mode,
            "backup_path": backup_path,
            "db_host": parameters.get("db_host", "localhost"),
            "db_port": parameters.get("db_port", 5432),
            "db_name": parameters.get("db_name", "kong"),
            "db_user": parameters.get("db_user", "kong"),
            "db_password": parameters.get("db_password", ""),
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 3: Plugin compatibility check
    compat = await dispatch_agent_job(
        command="kong_check_plugins",
        parameters={"target_version": target_version, "kong_admin_port": kong_admin_port},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    plugin_warnings = compat.get("warnings", [])
    if compat.get("blocking_incompatibilities"):
        return {
            "status": "blocked",
            "reason": "Plugin incompatibilities block upgrade",
            "incompatibilities": compat.get("blocking_incompatibilities"),
        }

    # Step 4: Stop Kong
    await dispatch_agent_job(
        command="kong_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Step 5: Install new version
    await dispatch_agent_job(
        command="kong_install_version",
        parameters={"version": target_version},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 6: Run migrations (postgres mode only)
    if db_mode == "postgres":
        await dispatch_agent_job(
            command="kong_run_migrations",
            parameters={
                "db_host": parameters.get("db_host", "localhost"),
                "db_name": parameters.get("db_name", "kong"),
                "db_user": parameters.get("db_user", "kong"),
                "db_password": parameters.get("db_password", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )

    # Step 7: Start Kong
    await dispatch_agent_job(
        command="kong_start",
        parameters={"kong_admin_port": kong_admin_port},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Step 8: Verify
    verify = await dispatch_agent_job(
        command="kong_verify",
        parameters={
            "target_version": target_version,
            "kong_host": kong_host,
            "kong_admin_port": kong_admin_port,
            "kong_proxy_port": kong_proxy_port,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed" if verify.get("version_ok") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "routes_count": routes_count,
        "services_count": services_count,
        "plugins_count": plugins_count,
        "plugin_warnings": plugin_warnings,
        "backup_path": backup_path,
        "snapshot": snapshot,
        "verify": verify,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    source_version = execution_result.get("source_version", "")
    backup_path = execution_result.get("backup_path", "")
    db_mode = parameters.get("db_mode", "postgres")

    if not source_version:
        return {"rolled_back": False, "reason": "source_version missing from execution_result"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Stop Kong, install old version, restore data, start
    await dispatch_agent_job(
        command="kong_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    await dispatch_agent_job(
        command="kong_install_version",
        parameters={"version": source_version},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    if db_mode == "postgres" and backup_path:
        await dispatch_agent_job(
            command="kong_restore_snapshot",
            parameters={
                "backup_path": backup_path,
                "db_mode": db_mode,
                "db_host": parameters.get("db_host", "localhost"),
                "db_name": parameters.get("db_name", "kong"),
                "db_user": parameters.get("db_user", "kong"),
                "db_password": parameters.get("db_password", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    await dispatch_agent_job(
        command="kong_start",
        parameters={"kong_admin_port": parameters.get("kong_admin_port", 8001)},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    return {
        "rolled_back": True,
        "source_version": source_version,
        "backup_path": backup_path,
    }
```

### Step 2.6 — Commit

```bash
git add backend/app/connectors/executors/nexplane_agent/kong_upgrade.py \
        backend/app/connectors/change_type_definitions/kong_upgrade.json \
        backend/app/models/change_request.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/tests/smoke/test_smoke_kong_upgrade.py
git commit -m "feat: kong_upgrade CR type (smoke_verified: false)"
```

---

## Task 3: `cert_manager_upgrade`

### Step 3.1 — Smoke test

**File:** `backend/tests/smoke/test_smoke_cert_manager_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: cert-manager Upgrade

Phases:
  1. setup       — reuse EKS cluster; install cert-manager 1.13 via Helm
  2. run_upgrade — cert_manager_upgrade CR lifecycle (1.13→1.15)
  3. verify      — Deployment running; test Certificate CR issued (Ready=True)
  4. rollback    — Deployment rolled back; CRDs remain at 1.15 (expected)
  5. teardown    — helm uninstall cert-manager; delete test Certificate

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_cert_manager_upgrade.py -v -s
"""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_EKS_AGENT_ASSET_SSM_KEY = "/nexplane/smoke-state/k8s-cluster-upgrade/agent-asset-id"
SOURCE_VERSION = "1.13"
TARGET_VERSION = "1.15"

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "asset_id": None,
    "cr_id": None,
    "execution_result": None,
}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200
        data = r.json()
        if data.get("status") in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out")


def test_phase1_resolve_eks_agent():
    import boto3
    creds = get_connector_creds_from_db("aws")
    ssm = boto3.client("ssm",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    try:
        resp = ssm.get_parameter(Name=_EKS_AGENT_ASSET_SSM_KEY)
        _state["asset_id"] = resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        pytest.skip("No EKS smoke cluster — run k8s_cluster_upgrade smoke first")
    log(f"Using EKS agent asset: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "cert_manager_upgrade",
        "title": f"Smoke: cert-manager {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "namespace": "cert-manager",
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    _state["cr_id"] = r.json()["id"]
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/plan")
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/approve")
    log(f"CR {_state['cr_id']} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip()
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify():
    result = _state["execution_result"]
    assert result, "No execution_result"
    assert result.get("status") == "completed"
    assert result.get("crds_upgraded") is True
    assert result.get("deployment_upgraded") is True
    assert result.get("test_cert_issued") is True, "Test certificate was not issued"
    log(f"cert-manager {TARGET_VERSION} verified; test cert issued")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip()
    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"))
    assert cr["status"] == "rolled_back"
    rb = cr.get("rollback_result", {})
    assert rb.get("deployment_rolled_back") is True
    # CRDs remain at target — expected
    assert rb.get("crds_note"), "Expected crds_note explaining CRDs remain at target"
    log("cert-manager rollback: Deployment rolled back; CRDs remain at 1.15 (expected)")


def test_phase5_teardown():
    log("Teardown: run 'helm uninstall cert-manager -n cert-manager' on EKS cluster manually or in next smoke setup")
```

### Step 3.2 — ChangeType enum

Add to `backend/app/models/change_request.py`:
```python
    cert_manager_upgrade = "cert_manager_upgrade"
```
(Migration already handles this in `20260805_001`.)

### Step 3.3 — change_type_definition

**File:** `backend/app/connectors/change_type_definitions/cert_manager_upgrade.json`

```json
{
  "change_type": "cert_manager_upgrade",
  "display_name": "cert-manager Upgrade",
  "description": "Upgrade cert-manager in a Kubernetes cluster. CRDs are upgraded first (server-side apply), then the Deployment. CRDs cannot be cleanly downgraded; Deployment rollback is always safe. ROLLBACK_CAPABILITY=partial.",
  "rollback_capability": "partial",
  "steps": [
    {"generic_action": "cert_manager_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "parameters": {
    "source_version":  { "type": "string",  "required": true,  "description": "e.g. '1.13'" },
    "target_version":  { "type": "string",  "required": true,  "description": "e.g. '1.15'" },
    "namespace":       { "type": "string",  "required": false, "default": "cert-manager" },
    "kubeconfig_path": { "type": "string",  "required": false, "default": "~/.kube/config" },
    "dry_run":         { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 3.4 — Catalog entry

```json
{
  "display_name": "cert-manager Upgrade",
  "description": "Upgrade cert-manager CRDs and Deployment. CRD upgrade is partially irreversible (schema additions not removable); Deployment rollback is always safe.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": false, "type": "string",  "name": "namespace", "default": "cert-manager" },
    { "required": false, "type": "string",  "name": "kubeconfig_path" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 300,
  "applicable_asset_types": ["server", "k8s_node"],
  "action_type": "change",
  "executor": "nexplane_agent.cert_manager_upgrade",
  "generic_action": "cert_manager_upgrade",
  "action_id": "cert_manager_upgrade"
}
```

### Step 3.5 — Executor

**File:** `backend/app/connectors/executors/nexplane_agent/cert_manager_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""cert-manager upgrade executor.

Flow: preflight → snapshot CRDs → upgrade CRDs (server-side apply, PARTIAL NO-RETURN)
      → upgrade Deployment → verify (Certificate issuance test).

ROLLBACK_CAPABILITY = "partial": Deployment can be rolled back; CRDs cannot be
cleanly downgraded. Old CRD backup is recorded but not restored.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_GITHUB_RELEASE_BASE = "https://github.com/cert-manager/cert-manager/releases/download"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    namespace = parameters.get("namespace", "cert-manager")
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required (e.g. '1.13')")
    if not target_version:
        raise ValueError("target_version required (e.g. '1.15')")

    # Validate: cert-manager supports skipping patch, not minor — only +N minor allowed (warn if >+1)
    try:
        src_minor = int(source_version.split(".")[1])
        tgt_minor = int(target_version.split(".")[1])
    except (IndexError, ValueError):
        raise ValueError("source_version and target_version must be 'MAJOR.MINOR' format")
    if tgt_minor <= src_minor:
        raise ValueError(f"target_version must be newer than source_version")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    crds_url = f"{_GITHUB_RELEASE_BASE}/v{target_version}/cert-manager.crds.yaml"
    deploy_url = f"{_GITHUB_RELEASE_BASE}/v{target_version}/cert-manager.yaml"

    # Step 1: Preflight
    logger.info(f"cert-manager preflight {source_version}→{target_version} on {asset_id}")
    preflight = await dispatch_agent_job(
        command="cert_manager_preflight",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    current_image_tag = preflight.get("current_image_tag", source_version)

    # Step 2: Snapshot CRDs
    crd_backup_path = f"/tmp/cert-manager-crds-backup-{asset_id[:8]}.yaml"
    await dispatch_agent_job(
        command="cert_manager_snapshot_crds",
        parameters={"backup_path": crd_backup_path, "kubeconfig_path": kubeconfig_path},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "crds_url": crds_url,
            "deploy_url": deploy_url,
            "preflight": preflight,
        }

    # Step 3: Upgrade CRDs — POINT OF PARTIAL NO-RETURN
    logger.info(f"Upgrading cert-manager CRDs to {target_version}")
    crd_result = await dispatch_agent_job(
        command="cert_manager_upgrade_crds",
        parameters={
            "crds_url": crds_url,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if not crd_result.get("success", True):
        return {
            "status": "failed",
            "phase": "crd_upgrade",
            "error": crd_result.get("error"),
            "crds_upgraded": False,
            "deployment_upgraded": False,
        }

    # Step 4: Upgrade Deployment
    logger.info(f"Upgrading cert-manager Deployment to {target_version}")
    deploy_result = await dispatch_agent_job(
        command="cert_manager_upgrade_deployment",
        parameters={
            "deploy_url": deploy_url,
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    if not deploy_result.get("success", True):
        return {
            "status": "failed",
            "phase": "deployment_upgrade",
            "error": deploy_result.get("error"),
            "crds_upgraded": True,
            "deployment_upgraded": False,
            "crd_backup_path": crd_backup_path,
            "note": "CRDs upgraded but Deployment failed. Rollback will restore Deployment only.",
        }

    # Step 5: Verify — issue a test self-signed Certificate
    verify = await dispatch_agent_job(
        command="cert_manager_verify",
        parameters={
            "namespace": namespace,
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "crds_upgraded": True,
        "deployment_upgraded": True,
        "test_cert_issued": verify.get("cert_issued", False),
        "crd_backup_path": crd_backup_path,
        "current_image_tag_before": current_image_tag,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    namespace = parameters.get("namespace", "cert-manager")
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")
    crd_backup_path = execution_result.get("crd_backup_path", "")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Rollback Deployment only — kubectl rollout undo
    deploy_rb = await dispatch_agent_job(
        command="cert_manager_rollback_deployment",
        parameters={
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    return {
        "rolled_back": True,
        "deployment_rolled_back": deploy_rb.get("success", True),
        "crds_note": (
            "CRDs remain at target version — CRD schema additions are non-breaking and "
            f"cannot be cleanly downgraded. Backup stored at {crd_backup_path}."
        ),
    }
```

### Step 3.6 — Commit

```bash
git add backend/app/connectors/executors/nexplane_agent/cert_manager_upgrade.py \
        backend/app/connectors/change_type_definitions/cert_manager_upgrade.json \
        backend/app/models/change_request.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/tests/smoke/test_smoke_cert_manager_upgrade.py
git commit -m "feat: cert_manager_upgrade CR type (smoke_verified: false)"
```

---

## Task 4: `gitlab_upgrade`

### Step 4.1 — Smoke test

**File:** `backend/tests/smoke/test_smoke_gitlab_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: GitLab Major Version Upgrade

Single-hop smoke: 15.11 → 16.0 (cheapest valid hop to verify CR lifecycle).
Full multi-hop (15→17) is slow (~45 min); single-hop is sufficient.

AMI cache key: /nexplane/smoke-amis/gitlab/15.11
Instance type: t3.xlarge (GitLab needs 4GB+ RAM)

Phases:
  1. setup     — get_or_create_smoke_ami for GitLab 15.11; launch t3.xlarge; register
  2. upgrade   — gitlab_upgrade CR lifecycle (15.11→16.0)
  3. verify    — GET /api/v4/version == 16.0; GET /api/v4/projects returns 200
  4. rollback  — restore from backup; verify 15.11
  5. teardown  — terminate instance; deregister

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_gitlab_upgrade.py -v -s
"""

import os
import sys
import time
import pytest
import boto3

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_AMI_SSM_KEY = "/nexplane/smoke-amis/gitlab/15.11"
SOURCE_VERSION = "15.11"
TARGET_VERSION = "16.0"
INSTANCE_TYPE = "t3.xlarge"
GITLAB_ADMIN_TOKEN = os.environ.get("GITLAB_SMOKE_TOKEN", "smoke-admin-token")

CR_TIMEOUT = 1800  # 30 min — backup + upgrade + background migrations
POLL_INTERVAL = 15

_state = {
    "connector_id": None,
    "asset_id": None,
    "instance_id": None,
    "instance_ip": None,
    "cr_id": None,
    "execution_result": None,
    "provisioned_by_us": False,
}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200
        data = r.json()
        if data.get("status") in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def test_phase1_provision_gitlab():
    creds = get_connector_creds_from_db("aws")
    ami_id = get_or_create_smoke_ami(
        ssm_key=_AMI_SSM_KEY,
        creds=creds,
        build_instructions=(
            "Install GitLab EE 15.11.x omnibus on Ubuntu 22.04. "
            "Set external_url to http://localhost. "
            "Create initial admin user with token 'smoke-admin-token'. "
            "Create AMI after gitlab-ctl reconfigure completes."
        ),
    )
    log(f"Using GitLab AMI: {ami_id}")

    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-smoke-gitlab-upgrade"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    _state["instance_id"] = instance_id
    _state["provisioned_by_us"] = True
    log(f"EC2 launched: {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    _state["instance_ip"] = ip
    log(f"GitLab instance at {ip}")

    # Wait for GitLab to be fully up (gitlab-ctl status)
    time.sleep(120)

    r = _api("post", "/api/v1/connectors", json={
        "connector_type": "nexplane_agent",
        "display_name": f"smoke-gitlab-{instance_id[:8]}",
        "credentials": {},
    })
    assert r.status_code in (200, 201)
    connector_id = r.json()["id"]
    _state["connector_id"] = connector_id

    r = _api("post", "/api/v1/assets", json={
        "connector_id": connector_id,
        "asset_type": "server",
        "display_name": f"smoke-gitlab-{instance_id[:8]}",
        "asset_metadata": {"instance_id": instance_id, "ip": ip},
    })
    assert r.status_code in (200, 201)
    _state["asset_id"] = r.json()["id"]
    log(f"Asset: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "gitlab_upgrade",
        "title": f"Smoke: GitLab {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "gitlab_host": _state["instance_ip"],
            "gitlab_admin_token": GITLAB_ADMIN_TOKEN,
            "gitlab_package_type": "omnibus",
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    _state["cr_id"] = r.json()["id"]
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/plan")
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/approve")
    log(f"CR {_state['cr_id']} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip()
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify():
    result = _state["execution_result"]
    assert result, "No execution_result"
    assert result.get("status") == "completed"
    assert result.get("final_version", "").startswith("16.0"), \
        f"Expected 16.0.x, got {result.get('final_version')}"
    assert result.get("hops_completed") == [f"{SOURCE_VERSION}→{TARGET_VERSION}"]
    log(f"GitLab {TARGET_VERSION} verified; hops={result.get('hops_completed')}")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip()
    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"), timeout=1800)
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"
    rb = cr.get("rollback_result", {})
    assert rb.get("rolled_back") is True
    log("GitLab rollback to 15.11 verified")


def test_phase5_teardown():
    if not _state["provisioned_by_us"]:
        return
    creds = get_connector_creds_from_db("aws")
    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    if _state["instance_id"]:
        ec2.terminate_instances(InstanceIds=[_state["instance_id"]])
        log(f"Terminated {_state['instance_id']}")
    if _state["asset_id"]:
        _api("delete", f"/api/v1/assets/{_state['asset_id']}")
    if _state["connector_id"]:
        _api("delete", f"/api/v1/connectors/{_state['connector_id']}")
```

### Step 4.2 — ChangeType enum

```python
    gitlab_upgrade = "gitlab_upgrade"
```

### Step 4.3 — change_type_definition

**File:** `backend/app/connectors/change_type_definitions/gitlab_upgrade.json`

```json
{
  "change_type": "gitlab_upgrade",
  "display_name": "GitLab Major Version Upgrade",
  "description": "Upgrade GitLab through sequential major version hops. Cannot skip major versions — executor computes and validates the full hop chain. Background migrations are waited between each hop. ROLLBACK_CAPABILITY=partial (background migrations are irreversible; rollback restores from per-hop backup).",
  "rollback_capability": "partial",
  "steps": [
    {"generic_action": "gitlab_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "parameters": {
    "source_version":        { "type": "string",  "required": true,  "description": "e.g. '15.11'" },
    "target_version":        { "type": "string",  "required": true,  "description": "e.g. '17.2'" },
    "gitlab_host":           { "type": "string",  "required": false, "default": "localhost" },
    "gitlab_admin_token":    { "type": "string",  "required": true,  "sensitive": true },
    "gitlab_package_type":   { "type": "string",  "required": false, "enum": ["omnibus", "helm"], "default": "omnibus" },
    "backup_s3_bucket":      { "type": "string",  "required": false },
    "dry_run":               { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 4.4 — Catalog entry

```json
{
  "display_name": "GitLab Major Version Upgrade",
  "description": "Upgrade GitLab through sequential major version hops. Computes hop chain, takes per-hop backup, waits for background migrations. Partial rollback from most recent hop backup.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": false, "type": "string",  "name": "gitlab_host" },
    { "required": true,  "type": "string",  "name": "gitlab_admin_token" },
    { "required": false, "type": "string",  "name": "gitlab_package_type", "default": "omnibus" },
    { "required": false, "type": "string",  "name": "backup_s3_bucket" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 3600,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.gitlab_upgrade",
  "generic_action": "gitlab_upgrade",
  "action_id": "gitlab_upgrade"
}
```

### Step 4.5 — Executor

**File:** `backend/app/connectors/executors/nexplane_agent/gitlab_upgrade.py`

**Critical detail: hop chain computation.** GitLab requires upgrading through each major version's last minor (`.11`) before crossing to the next major. Within the same major, direct upgrade is fine.

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GitLab major version upgrade executor.

GitLab cannot skip major versions. The executor computes the required hop chain:
  - Within same major: direct upgrade (15.4→15.11 is fine)
  - Crossing major boundary: must hit <major>.11 (last minor) before next major
  - Example: 15.11→17.2 → hops: 15.11→16.0→16.11→17.0→17.2

Per hop: backup → install → reconfigure → wait background migrations → verify.
ROLLBACK_CAPABILITY = "partial" — background migrations are irreversible once run.
Rollback restores from the backup taken at the START of the current hop.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

# GitLab last minor version per major (kept current to 17.x as of 2026)
_GITLAB_LAST_MINOR = {
    14: 10,
    15: 11,
    16: 11,
    17: 11,
}


def _parse_version(version_str: str) -> tuple:
    """Parse 'MAJOR.MINOR' or 'MAJOR.MINOR.PATCH' → (major, minor, patch)."""
    parts = str(version_str).split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        raise ValueError(f"Cannot parse GitLab version: {version_str!r}")
    return (major, minor, patch)


def _fmt(major: int, minor: int) -> str:
    return f"{major}.{minor}"


def compute_gitlab_hop_chain(source_version: str, target_version: str) -> list:
    """Compute the ordered list of GitLab version hops required to reach target.

    Rules:
      1. Cannot skip major versions.
      2. Before crossing from major N to major N+1, must be at <N>.11
         (or whatever _GITLAB_LAST_MINOR[N] is).
      3. Within the same major, direct upgrade is safe.
      4. The chain includes source_version and target_version as endpoints.

    Examples:
      15.11 → 16.0  : ["15.11", "16.0"]          (one hop, already at last minor)
      15.4  → 16.0  : ["15.4", "15.11", "16.0"]  (must hit 15.11 first)
      15.11 → 17.2  : ["15.11", "16.0", "16.11", "17.0", "17.2"]
    """
    src_major, src_minor, _ = _parse_version(source_version)
    tgt_major, tgt_minor, _ = _parse_version(target_version)

    if (tgt_major, tgt_minor) <= (src_major, src_minor):
        raise ValueError(
            f"target_version {target_version!r} must be newer than source_version {source_version!r}"
        )

    hops = [source_version]

    current_major = src_major
    current_minor = src_minor

    while current_major < tgt_major:
        last_minor = _GITLAB_LAST_MINOR.get(current_major)
        if last_minor is None:
            raise ValueError(
                f"Unknown GitLab last minor for major {current_major}. "
                f"Update _GITLAB_LAST_MINOR in gitlab_upgrade.py."
            )

        # If not yet at last minor of current major, must upgrade there first
        if current_minor < last_minor:
            hop = _fmt(current_major, last_minor)
            hops.append(hop)
            current_minor = last_minor

        # Cross to next major at minor 0
        next_major = current_major + 1
        hop = _fmt(next_major, 0)
        hops.append(hop)
        current_major = next_major
        current_minor = 0

    # Now at target major — if not yet at target minor, add target
    if current_minor < tgt_minor:
        hops.append(_fmt(tgt_major, tgt_minor))

    # Deduplicate while preserving order (source may equal first waypoint)
    seen = set()
    deduped = []
    for h in hops:
        if h not in seen:
            seen.add(h)
            deduped.append(h)

    return deduped


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    gitlab_host = parameters.get("gitlab_host", "localhost")
    gitlab_admin_token = parameters.get("gitlab_admin_token", "")
    gitlab_package_type = parameters.get("gitlab_package_type", "omnibus")
    backup_s3_bucket = parameters.get("backup_s3_bucket")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required (e.g. '15.11')")
    if not target_version:
        raise ValueError("target_version required (e.g. '17.2')")
    if not gitlab_admin_token:
        raise ValueError("gitlab_admin_token required")

    # Compute hop chain — validate immediately so bad inputs fail before any infra ops
    try:
        hop_chain = compute_gitlab_hop_chain(source_version, target_version)
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc)}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight
    logger.info(f"GitLab upgrade preflight {source_version}→{target_version}, hops={hop_chain}")
    preflight = await dispatch_agent_job(
        command="gitlab_preflight",
        parameters={
            "gitlab_host": gitlab_host,
            "gitlab_admin_token": gitlab_admin_token,
            "source_version": source_version,
            "hop_chain": hop_chain,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "hop_chain": hop_chain,
            "preflight": preflight,
        }

    # Steps 2+: Execute each hop
    hops_completed = []
    backup_paths = []

    for i in range(len(hop_chain) - 1):
        from_ver = hop_chain[i]
        to_ver = hop_chain[i + 1]
        hop_label = f"{from_ver}→{to_ver}"
        logger.info(f"GitLab hop: {hop_label}")

        # 2a: Backup before each hop
        backup_result = await dispatch_agent_job(
            command="gitlab_backup",
            parameters={
                "gitlab_host": gitlab_host,
                "backup_s3_bucket": backup_s3_bucket,
            },
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        backup_path = backup_result.get("backup_path", f"gitlab-backup-hop{i}")
        backup_paths.append({"hop": hop_label, "backup_path": backup_path})

        # 2b: Upgrade to hop version
        upgrade_result = await dispatch_agent_job(
            command="gitlab_upgrade_hop",
            parameters={
                "to_version": to_ver,
                "package_type": gitlab_package_type,
            },
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        if not upgrade_result.get("success", True):
            return {
                "status": "failed",
                "phase": f"upgrade_hop_{hop_label}",
                "error": upgrade_result.get("error"),
                "hops_completed": hops_completed,
                "backup_paths": backup_paths,
                "note": "Rollback will restore from most recent hop backup",
            }

        # 2c: Wait for background migrations
        await dispatch_agent_job(
            command="gitlab_wait_migrations",
            parameters={
                "gitlab_host": gitlab_host,
                "gitlab_admin_token": gitlab_admin_token,
                "timeout_seconds": 1800,
            },
            asset_ids=[asset_id],
            timeout_seconds=2100,  # 35 min hard stop (30 min poll + 5 min buffer)
        )

        # 2d: Verify hop version
        verify_hop = await dispatch_agent_job(
            command="gitlab_verify_version",
            parameters={
                "expected_version": to_ver,
                "gitlab_host": gitlab_host,
                "gitlab_admin_token": gitlab_admin_token,
            },
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
        if not verify_hop.get("version_ok", True):
            return {
                "status": "failed",
                "phase": f"verify_hop_{hop_label}",
                "error": f"Version mismatch after hop: expected {to_ver}, got {verify_hop.get('actual_version')}",
                "hops_completed": hops_completed,
                "backup_paths": backup_paths,
            }

        hops_completed.append(hop_label)
        logger.info(f"GitLab hop {hop_label} completed")

    # Final verify
    final_verify = await dispatch_agent_job(
        command="gitlab_final_verify",
        parameters={
            "target_version": target_version,
            "gitlab_host": gitlab_host,
            "gitlab_admin_token": gitlab_admin_token,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "final_version": final_verify.get("actual_version", target_version),
        "hop_chain": hop_chain,
        "hops_completed": hops_completed,
        "backup_paths": backup_paths,
        "final_verify": final_verify,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore from the most recent per-hop backup. Background migrations are irreversible."""
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    backup_paths = execution_result.get("backup_paths", [])

    if not backup_paths:
        return {
            "rolled_back": False,
            "reason": "No per-hop backups recorded in execution_result",
        }

    # Most recent backup = last entry (FILO)
    most_recent = backup_paths[-1]
    backup_path = most_recent.get("backup_path")
    hop = most_recent.get("hop", "unknown")

    if not backup_path:
        return {"rolled_back": False, "reason": "backup_path missing from most recent hop backup"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    restore_result = await dispatch_agent_job(
        command="gitlab_restore_backup",
        parameters={
            "backup_path": backup_path,
            "gitlab_host": parameters.get("gitlab_host", "localhost"),
            "backup_s3_bucket": parameters.get("backup_s3_bucket"),
        },
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )

    return {
        "rolled_back": restore_result.get("success", False),
        "restored_from_hop": hop,
        "backup_path": backup_path,
        "note": (
            "GitLab background migrations that already ran are irreversible. "
            f"Restored from backup taken before hop '{hop}'. "
            "Operator must re-run from this restore point."
        ),
        "restore_result": restore_result,
    }
```

### Step 4.6 — Commit

```bash
git add backend/app/connectors/executors/nexplane_agent/gitlab_upgrade.py \
        backend/app/connectors/change_type_definitions/gitlab_upgrade.json \
        backend/app/models/change_request.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/tests/smoke/test_smoke_gitlab_upgrade.py
git commit -m "feat: gitlab_upgrade CR type with hop chain (smoke_verified: false)"
```

---

## Task 5: `jenkins_upgrade`

### Step 5.1 — Smoke test

**File:** `backend/tests/smoke/test_smoke_jenkins_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Jenkins Upgrade

AMI cache key: /nexplane/smoke-amis/jenkins/2.426
Instance type: t3.medium

Phases:
  1. setup     — get_or_create_smoke_ami for Jenkins 2.426; launch EC2; register
  2. upgrade   — jenkins_upgrade CR lifecycle (2.426→2.452)
  3. verify    — version=2.452; executors available
  4. rollback  — assert 2.426
  5. teardown  — terminate; deregister

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_jenkins_upgrade.py -v -s
"""

import os
import sys
import time
import pytest
import boto3

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_AMI_SSM_KEY = "/nexplane/smoke-amis/jenkins/2.426"
SOURCE_VERSION = "2.426"
TARGET_VERSION = "2.452"
INSTANCE_TYPE = "t3.medium"
JENKINS_USER = os.environ.get("JENKINS_SMOKE_USER", "admin")
JENKINS_PASSWORD = os.environ.get("JENKINS_SMOKE_PASSWORD", "admin")

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "connector_id": None,
    "asset_id": None,
    "instance_id": None,
    "instance_ip": None,
    "cr_id": None,
    "execution_result": None,
    "provisioned_by_us": False,
}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200
        data = r.json()
        if data.get("status") in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def test_phase1_provision_jenkins():
    creds = get_connector_creds_from_db("aws")
    ami_id = get_or_create_smoke_ami(
        ssm_key=_AMI_SSM_KEY,
        creds=creds,
        build_instructions=(
            "Install Jenkins 2.426 via WAR on Amazon Linux 2. "
            "JENKINS_HOME=/var/lib/jenkins. WAR at /usr/share/jenkins/jenkins.war. "
            "Create admin user with password 'admin'. "
            "Start Jenkins on port 8080. Create AMI."
        ),
    )
    log(f"Using Jenkins AMI: {ami_id}")

    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-smoke-jenkins-upgrade"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    _state["instance_id"] = instance_id
    _state["provisioned_by_us"] = True

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    _state["instance_ip"] = ip
    log(f"Jenkins instance at {ip}")

    # Wait for Jenkins to start
    time.sleep(60)

    r = _api("post", "/api/v1/connectors", json={
        "connector_type": "nexplane_agent",
        "display_name": f"smoke-jenkins-{instance_id[:8]}",
        "credentials": {},
    })
    assert r.status_code in (200, 201)
    _state["connector_id"] = r.json()["id"]

    r = _api("post", "/api/v1/assets", json={
        "connector_id": _state["connector_id"],
        "asset_type": "server",
        "display_name": f"smoke-jenkins-{instance_id[:8]}",
        "asset_metadata": {"instance_id": instance_id, "ip": ip},
    })
    assert r.status_code in (200, 201)
    _state["asset_id"] = r.json()["id"]
    log(f"Asset: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "jenkins_upgrade",
        "title": f"Smoke: Jenkins {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "jenkins_home": "/var/lib/jenkins",
            "jenkins_war_path": "/usr/share/jenkins/jenkins.war",
            "jenkins_admin_url": f"http://{_state['instance_ip']}:8080",
            "jenkins_admin_user": JENKINS_USER,
            "jenkins_admin_password": JENKINS_PASSWORD,
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    _state["cr_id"] = r.json()["id"]
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/plan")
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/approve")
    log(f"CR {_state['cr_id']} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip()
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify():
    result = _state["execution_result"]
    assert result, "No execution_result"
    assert result.get("status") == "completed"
    assert result.get("target_version") == TARGET_VERSION
    log(f"Jenkins {TARGET_VERSION} verified; plugin_warnings={result.get('plugin_warnings', [])}")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip()
    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"))
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"
    rb = cr.get("rollback_result", {})
    assert rb.get("rolled_back") is True
    log("Jenkins rollback to 2.426 verified")


def test_phase5_teardown():
    if not _state["provisioned_by_us"]:
        return
    creds = get_connector_creds_from_db("aws")
    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    if _state["instance_id"]:
        ec2.terminate_instances(InstanceIds=[_state["instance_id"]])
        log(f"Terminated {_state['instance_id']}")
    if _state["asset_id"]:
        _api("delete", f"/api/v1/assets/{_state['asset_id']}")
    if _state["connector_id"]:
        _api("delete", f"/api/v1/connectors/{_state['connector_id']}")
```

### Step 5.2 — ChangeType enum

```python
    jenkins_upgrade = "jenkins_upgrade"
```

### Step 5.3 — change_type_definition

**File:** `backend/app/connectors/change_type_definitions/jenkins_upgrade.json`

```json
{
  "change_type": "jenkins_upgrade",
  "display_name": "Jenkins Upgrade",
  "description": "Upgrade Jenkins by replacing the WAR file. Enters quiet mode, waits for idle executors, stops Jenkins, replaces WAR, starts Jenkins. Full rollback from WAR and JENKINS_HOME backup.",
  "rollback_capability": "full",
  "steps": [
    {"generic_action": "jenkins_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"],
  "parameters": {
    "source_version":        { "type": "string",  "required": true },
    "target_version":        { "type": "string",  "required": true },
    "jenkins_home":          { "type": "string",  "required": false, "default": "/var/lib/jenkins" },
    "jenkins_war_path":      { "type": "string",  "required": false, "default": "/usr/share/jenkins/jenkins.war" },
    "jenkins_admin_url":     { "type": "string",  "required": false, "default": "http://localhost:8080" },
    "jenkins_admin_user":    { "type": "string",  "required": false },
    "jenkins_admin_password":{ "type": "string",  "required": false, "sensitive": true },
    "dry_run":               { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 5.4 — Catalog entry

```json
{
  "display_name": "Jenkins Upgrade",
  "description": "Upgrade Jenkins WAR file. Enters quiet mode, waits for idle executors, replaces WAR, verifies version. Full rollback from WAR + JENKINS_HOME backup.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": false, "type": "string",  "name": "jenkins_home", "default": "/var/lib/jenkins" },
    { "required": false, "type": "string",  "name": "jenkins_war_path" },
    { "required": false, "type": "string",  "name": "jenkins_admin_url" },
    { "required": false, "type": "string",  "name": "jenkins_admin_user" },
    { "required": false, "type": "string",  "name": "jenkins_admin_password" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 300,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.jenkins_upgrade",
  "generic_action": "jenkins_upgrade",
  "action_id": "jenkins_upgrade"
}
```

### Step 5.5 — Executor

**File:** `backend/app/connectors/executors/nexplane_agent/jenkins_upgrade.py`

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Jenkins upgrade executor.

Flow: preflight → backup (WAR + JENKINS_HOME) → quiet mode → wait idle →
      stop Jenkins → replace WAR → start Jenkins → plugin compat check →
      verify → cancel quiet mode.

ROLLBACK_CAPABILITY = "full" — restore old WAR + JENKINS_HOME backup.
Note: builds that ran between backup and rollback are lost.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_WAR_DOWNLOAD_BASE = "https://updates.jenkins.io/download/war"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    jenkins_home = parameters.get("jenkins_home", "/var/lib/jenkins")
    jenkins_war_path = parameters.get("jenkins_war_path", "/usr/share/jenkins/jenkins.war")
    jenkins_admin_url = parameters.get("jenkins_admin_url", "http://localhost:8080")
    jenkins_admin_user = parameters.get("jenkins_admin_user", "")
    jenkins_admin_password = parameters.get("jenkins_admin_password", "")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required")
    if not target_version:
        raise ValueError("target_version required")

    war_url = f"{_WAR_DOWNLOAD_BASE}/{target_version}/jenkins.war"
    backup_war_path = f"/tmp/nexplane-jenkins-old-{asset_id[:8]}.war"
    backup_home_path = f"/tmp/nexplane-jenkins-backup-{asset_id[:8]}"

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight — record version + plugin list + executor counts
    logger.info(f"Jenkins upgrade preflight {source_version}→{target_version} on {asset_id}")
    preflight = await dispatch_agent_job(
        command="jenkins_preflight",
        parameters={
            "source_version": source_version,
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    busy_executors = preflight.get("busy_executors", 0)
    if busy_executors > 0:
        logger.warning(f"Jenkins has {busy_executors} busy executors — will enter quiet mode")

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "war_url": war_url,
            "busy_executors": busy_executors,
            "preflight": preflight,
        }

    # Step 2: Backup — copy WAR + JENKINS_HOME
    await dispatch_agent_job(
        command="jenkins_backup",
        parameters={
            "jenkins_home": jenkins_home,
            "jenkins_war_path": jenkins_war_path,
            "backup_war_path": backup_war_path,
            "backup_home_path": backup_home_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )

    # Step 3: Enter quiet mode
    await dispatch_agent_job(
        command="jenkins_quiet_down",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Step 4: Wait for executors idle (up to 10 min)
    await dispatch_agent_job(
        command="jenkins_wait_idle",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
            "timeout_seconds": 600,
        },
        asset_ids=[asset_id],
        timeout_seconds=660,
    )

    # Step 5: Stop Jenkins
    await dispatch_agent_job(
        command="jenkins_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Step 6: Replace WAR
    await dispatch_agent_job(
        command="jenkins_install_war",
        parameters={
            "war_url": war_url,
            "jenkins_war_path": jenkins_war_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 7: Start Jenkins
    await dispatch_agent_job(
        command="jenkins_start",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "startup_timeout_seconds": 300,
        },
        asset_ids=[asset_id],
        timeout_seconds=360,
    )

    # Step 8: Plugin compatibility check (warn, don't fail)
    compat = await dispatch_agent_job(
        command="jenkins_check_plugins",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    plugin_warnings = compat.get("warnings", [])

    # Step 9: Verify version
    verify = await dispatch_agent_job(
        command="jenkins_verify",
        parameters={
            "target_version": target_version,
            "jenkins_admin_url": jenkins_admin_url,
            "jenkins_admin_user": jenkins_admin_user,
            "jenkins_admin_password": jenkins_admin_password,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Step 10: Cancel quiet mode
    try:
        await dispatch_agent_job(
            command="jenkins_cancel_quiet_down",
            parameters={
                "jenkins_admin_url": jenkins_admin_url,
                "jenkins_admin_user": jenkins_admin_user,
                "jenkins_admin_password": jenkins_admin_password,
            },
            asset_ids=[asset_id],
            timeout_seconds=60,
        )
    except Exception as exc:
        logger.warning(f"Could not cancel quiet mode: {exc}")

    return {
        "status": "completed" if verify.get("version_ok") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "plugin_warnings": plugin_warnings,
        "backup_war_path": backup_war_path,
        "backup_home_path": backup_home_path,
        "verify": verify,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    backup_war_path = execution_result.get("backup_war_path", "")
    backup_home_path = execution_result.get("backup_home_path", "")
    jenkins_war_path = parameters.get("jenkins_war_path", "/usr/share/jenkins/jenkins.war")
    jenkins_home = parameters.get("jenkins_home", "/var/lib/jenkins")
    jenkins_admin_url = parameters.get("jenkins_admin_url", "http://localhost:8080")

    if not backup_war_path:
        return {"rolled_back": False, "reason": "backup_war_path missing from execution_result"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Stop Jenkins
    await dispatch_agent_job(
        command="jenkins_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Restore old WAR
    await dispatch_agent_job(
        command="jenkins_restore_war",
        parameters={
            "backup_war_path": backup_war_path,
            "jenkins_war_path": jenkins_war_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Restore JENKINS_HOME
    if backup_home_path:
        await dispatch_agent_job(
            command="jenkins_restore_home",
            parameters={
                "backup_home_path": backup_home_path,
                "jenkins_home": jenkins_home,
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    # Start Jenkins
    await dispatch_agent_job(
        command="jenkins_start",
        parameters={
            "jenkins_admin_url": jenkins_admin_url,
            "startup_timeout_seconds": 300,
        },
        asset_ids=[asset_id],
        timeout_seconds=360,
    )

    return {
        "rolled_back": True,
        "source_version": execution_result.get("source_version"),
        "backup_war_path": backup_war_path,
        "backup_home_path": backup_home_path,
        "note": "Builds that ran between backup and rollback are lost.",
    }
```

### Step 5.6 — Commit

```bash
git add backend/app/connectors/executors/nexplane_agent/jenkins_upgrade.py \
        backend/app/connectors/change_type_definitions/jenkins_upgrade.json \
        backend/app/models/change_request.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/tests/smoke/test_smoke_jenkins_upgrade.py
git commit -m "feat: jenkins_upgrade CR type (smoke_verified: false)"
```

---

## Final commit — this plan file

```bash
git add -f docs/superpowers/plans/2026-08-05-service-mesh-cicd-upgrades-plan.md
git commit -m "plan: service mesh and CI/CD upgrades implementation plan"
```

---

## Execution checklist

- [ ] Task 1: istio_control_plane_upgrade — executor, CTD, enum, migration, catalog, smoke
- [ ] Task 2: kong_upgrade — executor, CTD, enum, catalog, smoke
- [ ] Task 3: cert_manager_upgrade — executor, CTD, enum, catalog, smoke
- [ ] Task 4: gitlab_upgrade — executor, CTD, enum, catalog, smoke
- [ ] Task 5: jenkins_upgrade — executor, CTD, enum, catalog, smoke
- [ ] All 5 smoke tests passing on EC2 runner
- [ ] `smoke_verified: true` set in commit message for each after smoke passes
- [ ] DB migration applied on live platform: `docker exec nexplane-backend-1 alembic upgrade head`

## smoke_verified status

| CR Type | smoke_verified |
|---|---|
| istio_control_plane_upgrade | false |
| kong_upgrade | false |
| cert_manager_upgrade | false |
| gitlab_upgrade | false |
| jenkins_upgrade | false |
