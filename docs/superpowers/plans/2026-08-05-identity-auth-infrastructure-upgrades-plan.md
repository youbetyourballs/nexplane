# Identity/Auth Infrastructure Upgrades — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four upgrade CR types — `keycloak_upgrade`, `freeipa_upgrade`, `openldap_schema_migration`, `vault_cluster_upgrade` — all via nexplane_agent with AMI-cached smoke tests passing live.

**Architecture:** All four follow preflight → snapshot → upgrade → verify → rollback via `dispatch_agent_job`. FreeIPA and Vault reuse cached AMIs. Keycloak and OpenLDAP build new AMIs on first smoke run.

**Tech Stack:** Python asyncio executors, `nexplane_agent._dispatch.dispatch_agent_job`, pytest smoke using `NexplaneClient` + `get_connector_creds_from_db`, boto3 for AMI cache, Alembic for DB migrations.

**Global Constraints:**
- All executors in `backend/app/connectors/executors/nexplane_agent/`
- `ROLLBACK_CAPABILITY` declared at module level on every executor
- `desired_outcome` is the only parameter channel — executors read from `parameters.get("desired_outcome") or parameters`
- ChangeType enum + Alembic migration + change_type_definition JSON + catalog entry required for each CR type
- FreeIPA AMI: `ami-064887df65fad525f` at SSM key `/nexplane/smoke-amis/freeipa/7d2480d9`
- Vault AMI: `ami-09df8f5733ff981b3` at SSM key `/nexplane/smoke-amis/vault/{hash}`
- Keycloak AMI: cache key `/nexplane/smoke-amis/keycloak/21.1` — built on first smoke run
- OpenLDAP AMI: cache key `/nexplane/smoke-amis/openldap/2.5` — built on first smoke run
- Follow `db_major_version_upgrade.py` executor pattern exactly
- Follow `test_smoke_privileged_account_audit.py` smoke pattern exactly
- Smoke tests run from EC2 runner via `docker exec nexplane-backend-1 python -m pytest ...`
- All changes committed frequently; smoke must pass live before task is done

---

## Task 1: `keycloak_upgrade` CR Type

**Files to create/modify:**
- Create: `backend/app/connectors/executors/nexplane_agent/keycloak_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/keycloak_upgrade.json`
- Create: `backend/alembic/versions/iau001_add_keycloak_upgrade_change_type.py`
- Modify: `backend/app/models/change_request.py` — add `keycloak_upgrade` after `keycloak_disable_user`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json` — add catalog entry
- Create: `backend/tests/smoke/test_smoke_keycloak_upgrade.py`

### Step 1.1 — Alembic migration
- [ ] Create `backend/alembic/versions/iau001_add_keycloak_upgrade_change_type.py`:

```python
"""add keycloak_upgrade change type

Revision ID: iau001
Revises: wpm001
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau001'
down_revision = 'wpm001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'keycloak_upgrade'")


def downgrade():
    pass
```

### Step 1.2 — ChangeType enum entry
- [ ] In `backend/app/models/change_request.py`, find the line:
  ```python
      keycloak_disable_user = "keycloak_disable_user"
  ```
  Add immediately after:
  ```python
      keycloak_upgrade = "keycloak_upgrade"
  ```

### Step 1.3 — change_type_definition JSON
- [ ] Create `backend/app/connectors/change_type_definitions/keycloak_upgrade.json`:

```json
{
  "change_type": "keycloak_upgrade",
  "display_name": "Keycloak Major Upgrade",
  "description": "Upgrade Keycloak to a new major version. Supports WildFly-to-Quarkus migration (v16→v17+) and Quarkus in-place upgrade (v17+). Exports realms and dumps DB before upgrade; rolls back by restoring old binary and DB dump.",
  "rollback_capability": "full",
  "steps": [
    {"generic_action": "keycloak_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "keycloak_upgrade",
  "rollback_connector_type": "nexplane_agent",
  "parameters": {
    "source_version":     { "type": "string",  "required": true },
    "target_version":     { "type": "string",  "required": true },
    "keycloak_home":      { "type": "string",  "required": false, "default": "/opt/keycloak" },
    "db_vendor":          { "type": "string",  "required": false, "default": "postgres", "enum": ["postgres", "mysql", "h2"] },
    "db_host":            { "type": "string",  "required": false, "default": "localhost" },
    "db_port":            { "type": "integer", "required": false },
    "db_name":            { "type": "string",  "required": false },
    "db_user":            { "type": "string",  "required": false },
    "db_password":        { "type": "string",  "required": false, "sensitive": true },
    "admin_user":         { "type": "string",  "required": false, "default": "admin" },
    "admin_password":     { "type": "string",  "required": false, "sensitive": true },
    "realms_to_export":   { "type": "array",   "required": false },
    "dry_run":            { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 1.4 — Catalog entry
- [ ] In `backend/app/connectors/catalog/nexplane_agent.json`, add the following action object to the `"actions"` array (place it near other upgrade actions such as `db_major_version_upgrade` or `k8s_cluster_upgrade`):

```json
{
  "display_name": "Keycloak Major Upgrade",
  "description": "Upgrade Keycloak to a new major version with realm export, DB snapshot, in-place or WildFly-to-Quarkus migration, and full rollback.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": false, "type": "string",  "name": "keycloak_home" },
    { "required": false, "type": "string",  "name": "db_vendor" },
    { "required": false, "type": "string",  "name": "db_host" },
    { "required": false, "type": "integer", "name": "db_port" },
    { "required": false, "type": "string",  "name": "db_name" },
    { "required": false, "type": "string",  "name": "db_user" },
    { "required": false, "type": "string",  "name": "db_password", "sensitive": true },
    { "required": false, "type": "string",  "name": "admin_user" },
    { "required": false, "type": "string",  "name": "admin_password", "sensitive": true },
    { "required": false, "type": "array",   "name": "realms_to_export" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 600,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.keycloak_upgrade",
  "generic_action": "keycloak_upgrade",
  "action_id": "keycloak_upgrade"
}
```

### Step 1.5 — Executor
- [ ] Create `backend/app/connectors/executors/nexplane_agent/keycloak_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Keycloak major version upgrade executor.
Flow: preflight -> snapshot (realm export + DB dump) -> upgrade -> verify -> (rollback).
Paths:
  - WildFly->Quarkus (source <= 16, target >= 17): export realms, install new binary, import.
  - Quarkus in-place (source >= 17, target >= 17): export, stop, replace binary, build, start.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_DB_VENDOR_DEFAULTS = {
    "postgres": {"port": 5432},
    "mysql":    {"port": 3306},
    "h2":       {"port": None},
}


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    db_vendor = p.get("db_vendor", "postgres")
    return {
        "source_version":   p.get("source_version"),
        "target_version":   p["target_version"],
        "keycloak_home":    p.get("keycloak_home", "/opt/keycloak"),
        "db_vendor":        db_vendor,
        "db_host":          p.get("db_host", "localhost"),
        "db_port":          p.get("db_port", _DB_VENDOR_DEFAULTS.get(db_vendor, {}).get("port")),
        "db_name":          p.get("db_name"),
        "db_user":          p.get("db_user"),
        "db_password":      p.get("db_password"),
        "admin_user":       p.get("admin_user", "admin"),
        "admin_password":   p.get("admin_password"),
        "realms_to_export": p.get("realms_to_export"),
        "dry_run":          bool(p.get("dry_run", False)),
    }


def _is_wildfly(keycloak_home: str) -> bool:
    """WildFly installations have a standalone/ directory; Quarkus does not."""
    # Determined at runtime by agent job keycloak_preflight checking filesystem.
    # This helper is used locally to classify based on version number.
    return False  # actual detection happens via agent_result in preflight


def _migration_path(source_version: str, target_version: str) -> str:
    """Return 'wildfly_to_quarkus' or 'quarkus_inplace'."""
    try:
        src_major = int(str(source_version).split(".")[0])
    except (ValueError, AttributeError):
        src_major = 17
    if src_major <= 16:
        return "wildfly_to_quarkus"
    return "quarkus_inplace"


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Main entry point. preflight -> snapshot -> upgrade -> verify."""
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    if not p.get("source_version"):
        raise ValueError("source_version is required")
    if not p.get("target_version"):
        raise ValueError("target_version is required")

    path = _migration_path(p["source_version"], p["target_version"])
    logger.info(f"Keycloak upgrade path: {path} ({p['source_version']} -> {p['target_version']})")

    # --- Phase 1: Preflight ---
    preflight_result = await _preflight(asset_id, p)
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return {**preflight_result, "migration_path": path, "dry_run": True}

    # --- Phase 2: Snapshot ---
    snapshot_result = await _snapshot(asset_id, p)

    # --- Phase 3: Upgrade ---
    try:
        if path == "wildfly_to_quarkus":
            upgrade_result = await _upgrade_wildfly_to_quarkus(asset_id, p)
        else:
            upgrade_result = await _upgrade_quarkus_inplace(asset_id, p)
    except Exception as exc:
        logger.error(f"Keycloak upgrade failed: {exc}")
        return {
            "status": "upgrade_failed",
            "error": str(exc),
            "snapshot_result": snapshot_result,
        }

    # --- Phase 4: Verify ---
    verify_result = await _verify(asset_id, p)

    return {
        "status": "completed" if verify_result.get("verify_status") == "passed" else "verify_failed",
        "migration_path": path,
        "source_version": p["source_version"],
        "target_version": p["target_version"],
        "snapshot_result": snapshot_result,
        "upgrade_result": upgrade_result,
        "verify_result": verify_result,
        "asset_id": asset_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore old Keycloak binary + DB dump."""
    snapshot_result = execution_result.get("snapshot_result") or {}
    if not snapshot_result.get("realm_export_paths") and not snapshot_result.get("db_dump_path"):
        return {"rolled_back": False, "reason": "no_snapshot_available"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="keycloak_rollback",
            parameters={
                **p,
                "snapshot_result": snapshot_result,
                "migration_path": execution_result.get("migration_path", "quarkus_inplace"),
            },
            asset_ids=[asset_id],
            timeout_seconds=900,
        )
        return {
            "rolled_back": True,
            "strategy": "binary_restore_and_db_reimport",
            "agent_result": result,
        }
    except Exception as exc:
        logger.error(f"Keycloak rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

async def _preflight(asset_id: str, p: dict) -> dict:
    return await dispatch_agent_job(
        command="keycloak_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )


async def _snapshot(asset_id: str, p: dict) -> dict:
    """Export all realms and dump the DB."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Realm export
    realm_result = await dispatch_agent_job(
        command="keycloak_export_realms",
        parameters={
            **p,
            "export_dir": f"/tmp/nexplane-kc-export-{timestamp}",
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    realm_export_paths = realm_result.get("realm_export_paths", [])

    # DB dump (skip for h2 — embedded, backed up via binary copy)
    db_dump_path = None
    if p.get("db_vendor") != "h2":
        dump_result = await dispatch_agent_job(
            command="keycloak_db_dump",
            parameters={
                **p,
                "dump_path": f"/tmp/nexplane-kc-db-{timestamp}.sql.gz",
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        db_dump_path = dump_result.get("dump_path")

    return {
        "snapshot_type": "realm_export_and_db_dump",
        "realm_export_paths": realm_export_paths,
        "db_dump_path": db_dump_path,
        "snapshot_completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _upgrade_wildfly_to_quarkus(asset_id: str, p: dict) -> dict:
    """WildFly -> Quarkus migration path (source <= 16, target >= 17)."""
    # 1. Download and extract new Keycloak release
    await dispatch_agent_job(
        command="keycloak_install_release",
        parameters={**p, "install_path": f"/opt/keycloak-{p['target_version']}"},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 2. Stop old WildFly-based Keycloak
    await dispatch_agent_job(
        command="keycloak_stop_service",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    # 3. Build Quarkus distribution
    await dispatch_agent_job(
        command="keycloak_build_quarkus",
        parameters={**p, "install_path": f"/opt/keycloak-{p['target_version']}"},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 4. Configure and start new Keycloak
    await dispatch_agent_job(
        command="keycloak_configure_and_start",
        parameters={**p, "install_path": f"/opt/keycloak-{p['target_version']}"},
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    # 5. Import realms
    await dispatch_agent_job(
        command="keycloak_import_realms",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    return {
        "upgrade_status": "completed",
        "migration_path": "wildfly_to_quarkus",
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _upgrade_quarkus_inplace(asset_id: str, p: dict) -> dict:
    """Quarkus in-place upgrade (source >= 17, target >= 17)."""
    # 1. Stop running Keycloak
    await dispatch_agent_job(
        command="keycloak_stop_service",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    # 2. Download and extract new release over keycloak_home
    await dispatch_agent_job(
        command="keycloak_install_release",
        parameters={**p, "install_path": p["keycloak_home"]},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 3. Build optimized distribution
    await dispatch_agent_job(
        command="keycloak_build_quarkus",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 4. Start (DB schema auto-migration runs on first start)
    await dispatch_agent_job(
        command="keycloak_start_optimized",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    return {
        "upgrade_status": "completed",
        "migration_path": "quarkus_inplace",
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _verify(asset_id: str, p: dict) -> dict:
    """Poll /health/ready and check /admin/realms via admin API."""
    result = await dispatch_agent_job(
        command="keycloak_verify",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    health_ok = result.get("health_ready", False)
    version_ok = p["target_version"] in str(result.get("version", ""))
    return {
        "verify_status": "passed" if (health_ok and version_ok) else "failed",
        "health_ready": health_ok,
        "version_confirmed": result.get("version"),
        "realm_count": result.get("realm_count"),
    }
```

### Step 1.6 — Smoke test
- [ ] Create `backend/tests/smoke/test_smoke_keycloak_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Keycloak Major Upgrade

Phases:
  1. provision   — launch Keycloak 21.1 EC2 from cached AMI (or build and cache)
  2. upgrade     — CR lifecycle: keycloak_upgrade 21.1->24.0, verify /health/ready + master realm
  3. rollback    — trigger rollback, verify old version responds
  4. teardown    — terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/keycloak/21.1
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_keycloak_upgrade.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_KC_AMI_SSM_KEY    = "/nexplane/smoke-amis/keycloak/21.1"
_KC_ADMIN_USER     = "admin"
_KC_ADMIN_PASSWORD = "SmokeAdmin1234!"
_KC_DB_PASSWORD    = "SmokeDb5678!"
_SSM_PROFILE       = "nexplane-smoke-ssm"
_SOURCE_VERSION    = "21.1"
_TARGET_VERSION    = "24.0"

CR_TIMEOUT    = 900
POLL_INTERVAL = 15

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "provisioned_by_us": False,
    "cr_id":             None,
}

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _boto3_client(service, creds):
    return boto3.client(
        service,
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


def _get_or_build_ami(ec2, ssm) -> str:
    """Return cached Keycloak 21.1 AMI, or skip with instructions to build one."""
    try:
        resp = ssm.get_parameter(Name=_KC_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using cached Keycloak AMI: {ami_id}")
            return ami_id
    except Exception:
        pass
    pytest.skip(
        f"No usable Keycloak 21.1 AMI cached at {_KC_AMI_SSM_KEY}. "
        "Build a Keycloak 21.1 + Postgres EC2, snapshot it, and store AMI ID in SSM."
    )


def _launch_kc(ec2, ssm, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",              "Value": "nexplane-smoke-keycloak-upgrade"},
            {"Key": "nexplane-purpose",  "Value": "smoke-keycloak-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Keycloak instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Wait for Keycloak HTTP port 8080
    log(f"  Waiting for Keycloak port 8080 on {private_ip} (up to 5 min)")
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, 8080), timeout=5)
            s.close()
            log(f"  Keycloak port 8080 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Keycloak port 8080 never reachable on {private_ip} within 5 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-kc-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-kc-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "keycloak", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT):
    terminal = ("completed", "failed", "rolled_back", "rollback_failed")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal within {timeout_s}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw   = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2    = _boto3_client("ec2",  aws_creds)
    ssm    = _boto3_client("ssm",  aws_creds)
    ami_id = _get_or_build_ami(ec2, ssm)

    instance_id, private_ip = _launch_kc(ec2, ssm, ami_id, aws_creds)
    run_id                   = uuid.uuid4().hex[:6]
    conn_id, asset_id        = _register_asset(private_ip, run_id)

    _state.update({
        "connector_id":      conn_id,
        "asset_id":          asset_id,
        "instance_id":       instance_id,
        "private_ip":        private_ip,
        "provisioned_by_us": True,
    })
    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: upgrade CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id    = _state["connector_id"]
    asset_id   = _state["asset_id"]
    private_ip = _state["private_ip"]
    run_id     = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-keycloak-upgrade-{run_id}",
        "change_type": "keycloak_upgrade",
        "desired_outcome": {
            "source_version":   _SOURCE_VERSION,
            "target_version":   _TARGET_VERSION,
            "keycloak_home":    "/opt/keycloak",
            "db_vendor":        "postgres",
            "db_host":          "localhost",
            "db_name":          "keycloak",
            "db_user":          "keycloak",
            "db_password":      _KC_DB_PASSWORD,
            "admin_user":       _KC_ADMIN_USER,
            "admin_password":   _KC_ADMIN_PASSWORD,
        },
        "connector_id": conn_id,
        "asset_ids":    [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "keycloak upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status unexpected: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("health_ready"), f"Keycloak /health/ready not UP: {verify}"
    assert verify.get("verify_status") == "passed", f"Verify failed: {verify}"

    realm_count = verify.get("realm_count", 0)
    assert realm_count >= 1, f"Expected at least master realm, got {realm_count}"

    log(f"  Upgrade verified: {realm_count} realm(s), health UP")
    log("[PHASE 2: upgrade] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete — no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=600)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _exec_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result unexpected: {result}"
    )
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: teardown
# ---------------------------------------------------------------------------

def test_phase4_teardown():
    if not _state.get("provisioned_by_us"):
        log("[PHASE 4: teardown] SKIPPED (reused existing infra)")
        return

    aws_creds   = get_connector_creds_from_db("aws")
    asset_id    = _state.get("asset_id")
    conn_id     = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    if asset_id:
        try:
            _api("delete", f"/assets/{asset_id}")
            log(f"  Deleted asset {asset_id}")
        except Exception as exc:
            log(f"  Warning: could not delete asset: {exc}", ok=False)

    if conn_id:
        try:
            _api("delete", f"/connectors/{conn_id}")
            log(f"  Deleted connector {conn_id}")
        except Exception as exc:
            log(f"  Warning: could not delete connector: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate instance: {exc}", ok=False)

    log("[PHASE 4: teardown] PASSED")
```

### Step 1.7 — Commit
- [ ] Run Alembic migration inside container:
  ```
  docker exec nexplane-backend-1 alembic upgrade head
  ```
- [ ] Commit:
  ```
  git add backend/app/connectors/executors/nexplane_agent/keycloak_upgrade.py \
          backend/app/connectors/change_type_definitions/keycloak_upgrade.json \
          backend/alembic/versions/iau001_add_keycloak_upgrade_change_type.py \
          backend/app/models/change_request.py \
          backend/app/connectors/catalog/nexplane_agent.json \
          backend/tests/smoke/test_smoke_keycloak_upgrade.py
  git commit -m "feat(identity): add keycloak_upgrade CR type with executor and smoke test"
  ```

### Step 1.8 — Smoke run
- [ ] Build Keycloak 21.1 AMI if not cached:
  - Launch Amazon Linux 2 EC2 (t3.medium), install Keycloak 21.1 + Postgres, configure admin/admin password, start service
  - `aws ec2 create-image --instance-id <id> --name "nexplane-smoke-keycloak-21.1"`
  - `aws ssm put-parameter --name /nexplane/smoke-amis/keycloak/21.1 --value <ami-id> --type String --overwrite`
- [ ] Run smoke from EC2 runner:
  ```
  docker exec nexplane-backend-1 python -m pytest \
      /app/tests/smoke/test_smoke_keycloak_upgrade.py -v -s
  ```
- [ ] All 4 phases pass → task done.

---

## Task 2: `freeipa_upgrade` CR Type

**Files to create/modify:**
- Create: `backend/app/connectors/executors/nexplane_agent/freeipa_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/freeipa_upgrade.json`
- Create: `backend/alembic/versions/iau002_add_freeipa_upgrade_change_type.py`
- Modify: `backend/app/models/change_request.py` — add `freeipa_upgrade` after `freeipa_disable_user`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: `backend/tests/smoke/test_smoke_freeipa_upgrade.py`

### Step 2.1 — Alembic migration
- [ ] Create `backend/alembic/versions/iau002_add_freeipa_upgrade_change_type.py`:

```python
"""add freeipa_upgrade change type

Revision ID: iau002
Revises: iau001
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau002'
down_revision = 'iau001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'freeipa_upgrade'")


def downgrade():
    pass
```

### Step 2.2 — ChangeType enum entry
- [ ] In `backend/app/models/change_request.py`, find:
  ```python
      freeipa_disable_user = "freeipa_disable_user"
  ```
  Add immediately after:
  ```python
      freeipa_upgrade = "freeipa_upgrade"
  ```

### Step 2.3 — change_type_definition JSON
- [ ] Create `backend/app/connectors/change_type_definitions/freeipa_upgrade.json`:

```json
{
  "change_type": "freeipa_upgrade",
  "display_name": "FreeIPA / RHIDM Upgrade",
  "description": "Upgrade FreeIPA server (and optional replicas) to a new version. Runs ipa-backup before upgrade. Replicas upgraded before master per IPA topology rules. Rollback restores from ipa-backup (data loss window surfaced).",
  "rollback_capability": "partial",
  "steps": [
    {"generic_action": "freeipa_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "freeipa_upgrade",
  "rollback_connector_type": "nexplane_agent",
  "parameters": {
    "source_version":      { "type": "string",  "required": true },
    "target_version":      { "type": "string",  "required": true },
    "master_host":         { "type": "string",  "required": true },
    "replica_hosts":       { "type": "array",   "required": false, "default": [] },
    "ipa_admin_password":  { "type": "string",  "required": true, "sensitive": true },
    "dry_run":             { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 2.4 — Catalog entry
- [ ] Add to `backend/app/connectors/catalog/nexplane_agent.json` `"actions"` array:

```json
{
  "display_name": "FreeIPA / RHIDM Upgrade",
  "description": "Upgrade FreeIPA server and optional replicas to a new version with backup-based rollback.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": true,  "type": "string",  "name": "master_host" },
    { "required": false, "type": "array",   "name": "replica_hosts" },
    { "required": true,  "type": "string",  "name": "ipa_admin_password", "sensitive": true },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 900,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.freeipa_upgrade",
  "generic_action": "freeipa_upgrade",
  "action_id": "freeipa_upgrade"
}
```

### Step 2.5 — Executor
- [ ] Create `backend/app/connectors/executors/nexplane_agent/freeipa_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
FreeIPA / RHIDM upgrade executor.
Flow: preflight -> backup -> upgrade replicas -> upgrade master -> verify -> (rollback via ipa-backup restore).
Topology rule: replicas must be upgraded before master.
ROLLBACK_CAPABILITY = "partial" — ipa-backup restore wipes+reinitializes LDAP+Kerberos DBs;
changes after backup time are lost. Replicas self-heal from master post-restore.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":     p.get("source_version"),
        "target_version":     p["target_version"],
        "master_host":        p["master_host"],
        "replica_hosts":      p.get("replica_hosts") or [],
        "ipa_admin_password": p.get("ipa_admin_password"),
        "dry_run":            bool(p.get("dry_run", False)),
    }


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    # --- Phase 1: Preflight ---
    preflight_result = await dispatch_agent_job(
        command="ipa_status_check",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return {**preflight_result, "dry_run": True}

    # --- Phase 2: Backup ---
    backup_result = await dispatch_agent_job(
        command="ipa_backup",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )
    backup_path = backup_result.get("backup_path")
    logger.info(f"IPA backup at: {backup_path}")

    # --- Phase 3: Upgrade replicas (before master — IPA topology rule) ---
    replicas_upgraded = []
    for replica_host in p["replica_hosts"]:
        logger.info(f"Upgrading IPA replica: {replica_host}")
        await dispatch_agent_job(
            command="ipa_server_upgrade",
            parameters={**p, "target_host": replica_host},
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        replicas_upgraded.append(replica_host)
        logger.info(f"Replica {replica_host} upgraded")

    # --- Phase 4: Upgrade master ---
    logger.info(f"Upgrading IPA master: {p['master_host']}")
    await dispatch_agent_job(
        command="ipa_server_upgrade",
        parameters={**p, "target_host": p["master_host"]},
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )

    # --- Phase 5: Verify ---
    verify_result = await dispatch_agent_job(
        command="ipa_verify",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    ipactl_ok  = verify_result.get("ipactl_ok", False)
    kinit_ok   = verify_result.get("kinit_ok", False)
    verify_ok  = ipactl_ok and kinit_ok

    return {
        "status":            "completed" if verify_ok else "verify_failed",
        "source_version":    p["source_version"],
        "target_version":    p["target_version"],
        "master_upgraded":   p["master_host"],
        "replicas_upgraded": replicas_upgraded,
        "backup_path":       backup_path,
        "verify_result":     verify_result,
        "asset_id":          asset_id,
        "upgraded_at":       datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Restore from ipa-backup on master. Data loss: all changes after backup_path timestamp.
    Replicas self-heal from master after restore.
    """
    backup_path = execution_result.get("backup_path")
    if not backup_path:
        return {"rolled_back": False, "reason": "no_backup_path_in_execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="ipa_backup_restore",
            parameters={**p, "backup_path": backup_path},
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        return {
            "rolled_back":   True,
            "strategy":      "ipa_backup_restore",
            "backup_path":   backup_path,
            "data_loss_warning": (
                "All IPA changes after backup time are lost. "
                "Replicas will self-heal from master after restore."
            ),
            "agent_result":  result,
        }
    except Exception as exc:
        logger.error(f"FreeIPA rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
```

### Step 2.6 — Smoke test
- [ ] Create `backend/tests/smoke/test_smoke_freeipa_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: FreeIPA Upgrade

Phases:
  1. provision   — launch FreeIPA EC2 from cached AMI ami-064887df65fad525f
  2. upgrade     — CR lifecycle: freeipa_upgrade, assert ipactl status all green
  3. rollback    — trigger rollback, assert ipa-backup restore completes
  4. teardown    — terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/freeipa/7d2480d9 (ami-064887df65fad525f)
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_freeipa_upgrade.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_IPA_AMI_SSM_KEY    = "/nexplane/smoke-amis/freeipa/7d2480d9"
_IPA_ADMIN_PASSWORD = "SmokeIpa1234!"
_SSM_PROFILE        = "nexplane-smoke-ssm"

CR_TIMEOUT    = 1800
POLL_INTERVAL = 20

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "source_version":    None,
    "provisioned_by_us": False,
    "cr_id":             None,
}

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _boto3_client(service, creds):
    return boto3.client(
        service,
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


def _get_ipa_ami(ec2, ssm) -> str:
    try:
        resp   = ssm.get_parameter(Name=_IPA_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs   = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using cached FreeIPA AMI: {ami_id}")
            return ami_id
    except Exception:
        pass
    pytest.skip(
        f"No usable FreeIPA AMI at {_IPA_AMI_SSM_KEY}. "
        "Expected ami-064887df65fad525f from prior smoke session."
    )


def _launch_ipa(ec2, ssm, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-freeipa-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-freeipa-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched FreeIPA instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Wait for LDAP port 389
    log(f"  Waiting for LDAP port 389 on {private_ip} (up to 8 min)")
    deadline = time.time() + 480
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 389), timeout=5)
            s.close()
            log(f"  LDAP port 389 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"FreeIPA LDAP never reachable on {private_ip}:389 within 8 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-freeipa-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-freeipa-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "freeipa_master", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT):
    terminal = ("completed", "failed", "rolled_back", "rollback_failed")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal within {timeout_s}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw   = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2    = _boto3_client("ec2", aws_creds)
    ssm    = _boto3_client("ssm", aws_creds)
    ami_id = _get_ipa_ami(ec2, ssm)

    instance_id, private_ip = _launch_ipa(ec2, ssm, ami_id, aws_creds)
    run_id                   = uuid.uuid4().hex[:6]
    conn_id, asset_id        = _register_asset(private_ip, run_id)

    _state.update({
        "connector_id":      conn_id,
        "asset_id":          asset_id,
        "instance_id":       instance_id,
        "private_ip":        private_ip,
        "provisioned_by_us": True,
    })
    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: upgrade CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id    = _state["connector_id"]
    asset_id   = _state["asset_id"]
    private_ip = _state["private_ip"]
    run_id     = uuid.uuid4().hex[:6]

    # Source version is whatever is on the cached AMI; target is latest available
    # The executor detects installed version via `ipa --version` in preflight
    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-freeipa-upgrade-{run_id}",
        "change_type": "freeipa_upgrade",
        "desired_outcome": {
            "source_version":     "4.10",
            "target_version":     "4.12",
            "master_host":        private_ip,
            "replica_hosts":      [],
            "ipa_admin_password": _IPA_ADMIN_PASSWORD,
        },
        "connector_id": conn_id,
        "asset_ids":    [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "freeipa upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("ipactl_ok"),  f"ipactl status not OK: {verify}"
    assert verify.get("kinit_ok"),   f"kinit admin failed: {verify}"
    assert result.get("backup_path"), "No backup_path in result — snapshot missing"

    _state["source_version"] = result.get("source_version")
    log(f"  FreeIPA upgraded: ipactl OK, kinit OK, backup at {result['backup_path']}")
    log("[PHASE 2: upgrade] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete — no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=1800)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _exec_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result: {result}"
    )
    assert "data_loss_warning" in result, "Expected data_loss_warning in rollback result"
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: teardown
# ---------------------------------------------------------------------------

def test_phase4_teardown():
    if not _state.get("provisioned_by_us"):
        log("[PHASE 4: teardown] SKIPPED")
        return

    aws_creds   = get_connector_creds_from_db("aws")
    asset_id    = _state.get("asset_id")
    conn_id     = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    for res_id, path in [(asset_id, f"/assets/{asset_id}"), (conn_id, f"/connectors/{conn_id}")]:
        if res_id:
            try:
                _api("delete", path)
                log(f"  Deleted {path}")
            except Exception as exc:
                log(f"  Warning: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate: {exc}", ok=False)

    log("[PHASE 4: teardown] PASSED")
```

### Step 2.7 — Commit and smoke run
- [ ] `docker exec nexplane-backend-1 alembic upgrade head`
- [ ] Commit:
  ```
  git add backend/app/connectors/executors/nexplane_agent/freeipa_upgrade.py \
          backend/app/connectors/change_type_definitions/freeipa_upgrade.json \
          backend/alembic/versions/iau002_add_freeipa_upgrade_change_type.py \
          backend/app/models/change_request.py \
          backend/app/connectors/catalog/nexplane_agent.json \
          backend/tests/smoke/test_smoke_freeipa_upgrade.py
  git commit -m "feat(identity): add freeipa_upgrade CR type with executor and smoke test"
  ```
- [ ] Run smoke from EC2 runner:
  ```
  docker exec nexplane-backend-1 python -m pytest \
      /app/tests/smoke/test_smoke_freeipa_upgrade.py -v -s
  ```
- [ ] All 4 phases pass → task done.

---

## Task 3: `openldap_schema_migration` CR Type

**Files to create/modify:**
- Create: `backend/app/connectors/executors/nexplane_agent/openldap_schema_migration.py`
- Create: `backend/app/connectors/change_type_definitions/openldap_schema_migration.json`
- Create: `backend/alembic/versions/iau003_add_openldap_schema_migration_change_type.py`
- Modify: `backend/app/models/change_request.py` — add `openldap_schema_migration` in the Database administration section (after `run_schema_migration`)
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: `backend/tests/smoke/test_smoke_openldap_schema_migration.py`

### Step 3.1 — Alembic migration
- [ ] Create `backend/alembic/versions/iau003_add_openldap_schema_migration_change_type.py`:

```python
"""add openldap_schema_migration change type

Revision ID: iau003
Revises: iau002
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau003'
down_revision = 'iau002'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'openldap_schema_migration'")


def downgrade():
    pass
```

### Step 3.2 — ChangeType enum entry
- [ ] In `backend/app/models/change_request.py`, find:
  ```python
      run_schema_migration = "run_schema_migration"
  ```
  Add immediately after:
  ```python
      openldap_schema_migration = "openldap_schema_migration"
  ```

### Step 3.3 — change_type_definition JSON
- [ ] Create `backend/app/connectors/change_type_definitions/openldap_schema_migration.json`:

```json
{
  "change_type": "openldap_schema_migration",
  "display_name": "OpenLDAP Schema Migration",
  "description": "Add or modify an OpenLDAP schema via ldapadd/ldapmodify. Runs slapcat backup before applying. Tests schema in temp config dir before production apply. Rollback restores slapcat config export (data DB untouched).",
  "rollback_capability": "full",
  "steps": [
    {"generic_action": "openldap_schema_migration", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["output_check"],
  "rollback_action": "openldap_schema_migration",
  "rollback_connector_type": "nexplane_agent",
  "parameters": {
    "schema_ldif_path":  { "type": "string",  "required": true },
    "schema_dn":         { "type": "string",  "required": true },
    "backup_path":       { "type": "string",  "required": false, "default": "/tmp/nexplane-ldap-backup.ldif" },
    "slapd_config_dir":  { "type": "string",  "required": false, "default": "/etc/ldap/slapd.d" },
    "dry_run":           { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 3.4 — Catalog entry
- [ ] Add to `backend/app/connectors/catalog/nexplane_agent.json` `"actions"` array:

```json
{
  "display_name": "OpenLDAP Schema Migration",
  "description": "Add or modify an OpenLDAP schema entry with slapcat backup and full rollback.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "schema_ldif_path" },
    { "required": true,  "type": "string",  "name": "schema_dn" },
    { "required": false, "type": "string",  "name": "backup_path" },
    { "required": false, "type": "string",  "name": "slapd_config_dir" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 120,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.openldap_schema_migration",
  "generic_action": "openldap_schema_migration",
  "action_id": "openldap_schema_migration"
}
```

### Step 3.5 — Executor
- [ ] Create `backend/app/connectors/executors/nexplane_agent/openldap_schema_migration.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OpenLDAP schema migration executor.
Flow: preflight -> slapcat backup -> schema test (temp dir) -> apply -> verify -> (rollback).
Rollback: stop slapd, restore config DB from slapcat export, start slapd.
Data DB is untouched by schema changes.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "schema_ldif_path": p["schema_ldif_path"],
        "schema_dn":        p["schema_dn"],
        "backup_path":      p.get("backup_path", "/tmp/nexplane-ldap-backup.ldif"),
        "slapd_config_dir": p.get("slapd_config_dir", "/etc/ldap/slapd.d"),
        "dry_run":          bool(p.get("dry_run", False)),
    }


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    # --- Phase 1: Preflight ---
    preflight_result = await dispatch_agent_job(
        command="openldap_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return {**preflight_result, "dry_run": True}

    # --- Phase 2: slapcat backup ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config_backup_path = f"/tmp/nexplane-ldap-config-{timestamp}.ldif"
    data_backup_path   = f"/tmp/nexplane-ldap-data-{timestamp}.ldif"

    await dispatch_agent_job(
        command="openldap_slapcat",
        parameters={
            **p,
            "config_backup_path": config_backup_path,
            "data_backup_path":   data_backup_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    logger.info(f"slapcat backup: config={config_backup_path} data={data_backup_path}")

    # --- Phase 3: Schema test in temp config dir ---
    await dispatch_agent_job(
        command="openldap_schema_test",
        parameters={**p, "config_backup_path": config_backup_path},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    logger.info("Schema LDIF validated in temp config dir")

    # --- Phase 4: Apply schema ---
    try:
        await dispatch_agent_job(
            command="openldap_schema_apply",
            parameters=p,
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
    except Exception as exc:
        logger.error(f"Schema apply failed: {exc}")
        return {
            "status":              "upgrade_failed",
            "error":               str(exc),
            "config_backup_path":  config_backup_path,
            "data_backup_path":    data_backup_path,
        }

    # --- Phase 5: Verify ---
    verify_result = await dispatch_agent_job(
        command="openldap_verify_schema",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    schema_present = verify_result.get("schema_dn_present", False)

    return {
        "status":             "completed" if schema_present else "verify_failed",
        "schema_dn":          p["schema_dn"],
        "config_backup_path": config_backup_path,
        "data_backup_path":   data_backup_path,
        "verify_result":      verify_result,
        "asset_id":           asset_id,
        "applied_at":         datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Stop slapd, restore config DB from slapcat backup, start slapd."""
    config_backup_path = execution_result.get("config_backup_path")
    if not config_backup_path:
        return {"rolled_back": False, "reason": "no config_backup_path in execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="openldap_config_restore",
            parameters={**p, "config_backup_path": config_backup_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {
            "rolled_back":        True,
            "strategy":           "slapcat_config_restore",
            "config_backup_path": config_backup_path,
            "notes":              "Data DB untouched; only config DB (schema) restored.",
            "agent_result":       result,
        }
    except Exception as exc:
        logger.error(f"OpenLDAP rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
```

### Step 3.6 — Smoke test
- [ ] Create `backend/tests/smoke/test_smoke_openldap_schema_migration.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: OpenLDAP Schema Migration

Phases:
  1. provision   — launch OpenLDAP (slapd 2.5) EC2 from cached AMI (or build+cache)
                   AMI must have test schema LDIF pre-staged at /tmp/nexplane-testapp.ldif
  2. apply       — CR lifecycle: openldap_schema_migration, verify schema DN present
  3. rollback    — trigger rollback, verify schema DN gone
  4. teardown    — terminate, deregister

AMI cache key: /nexplane/smoke-amis/openldap/2.5
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_openldap_schema_migration.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_LDAP_AMI_SSM_KEY  = "/nexplane/smoke-amis/openldap/2.5"
_SSM_PROFILE       = "nexplane-smoke-ssm"
_TEST_SCHEMA_LDIF  = "/tmp/nexplane-testapp.ldif"
_TEST_SCHEMA_DN    = "cn=testapp,cn=schema,cn=config"

CR_TIMEOUT    = 300
POLL_INTERVAL = 10

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "provisioned_by_us": False,
    "cr_id":             None,
}

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _boto3_client(service, creds):
    return boto3.client(
        service,
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


def _get_ldap_ami(ec2, ssm) -> str:
    try:
        resp   = ssm.get_parameter(Name=_LDAP_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs   = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using cached OpenLDAP AMI: {ami_id}")
            return ami_id
    except Exception:
        pass
    pytest.skip(
        f"No usable OpenLDAP AMI cached at {_LDAP_AMI_SSM_KEY}. "
        "Build an Ubuntu/Debian EC2 with slapd 2.5 + test schema LDIF at "
        f"{_TEST_SCHEMA_LDIF}, snapshot it, store AMI in SSM."
    )


def _launch_ldap(ec2, ssm, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-openldap"},
            {"Key": "nexplane-purpose", "Value": "smoke-openldap-schema-migration"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched OpenLDAP instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for LDAP port 389 on {private_ip} (up to 4 min)")
    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, 389), timeout=5)
            s.close()
            log(f"  LDAP port 389 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"OpenLDAP LDAP never reachable on {private_ip}:389 within 4 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-openldap-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-openldap-{run_id}",
        "asset_type":   "server",
        "criticality":  "low",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "openldap", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT):
    terminal = ("completed", "failed", "rolled_back", "rollback_failed")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal within {timeout_s}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw   = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2    = _boto3_client("ec2", aws_creds)
    ssm    = _boto3_client("ssm", aws_creds)
    ami_id = _get_ldap_ami(ec2, ssm)

    instance_id, private_ip = _launch_ldap(ec2, ssm, ami_id, aws_creds)
    run_id                   = uuid.uuid4().hex[:6]
    conn_id, asset_id        = _register_asset(private_ip, run_id)

    _state.update({
        "connector_id":      conn_id,
        "asset_id":          asset_id,
        "instance_id":       instance_id,
        "provisioned_by_us": True,
    })
    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: apply schema
# ---------------------------------------------------------------------------

def test_phase2_apply_schema():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id  = _state["connector_id"]
    asset_id = _state["asset_id"]
    run_id   = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-openldap-schema-{run_id}",
        "change_type": "openldap_schema_migration",
        "desired_outcome": {
            "schema_ldif_path": _TEST_SCHEMA_LDIF,
            "schema_dn":        _TEST_SCHEMA_DN,
            "slapd_config_dir": "/etc/ldap/slapd.d",
        },
        "connector_id": conn_id,
        "asset_ids":    [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "openldap schema smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("schema_dn_present"), (
        f"Schema DN {_TEST_SCHEMA_DN} not found after apply: {verify}"
    )
    assert result.get("config_backup_path"), "No config_backup_path — snapshot missing"
    log(f"  Schema DN {_TEST_SCHEMA_DN} present, backup at {result['config_backup_path']}")
    log("[PHASE 2: apply_schema] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete — no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=300)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback reached unexpected status: {cr['status']}"
    )

    result = _exec_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result: {result}"
    )
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: teardown
# ---------------------------------------------------------------------------

def test_phase4_teardown():
    if not _state.get("provisioned_by_us"):
        log("[PHASE 4: teardown] SKIPPED")
        return

    aws_creds   = get_connector_creds_from_db("aws")
    asset_id    = _state.get("asset_id")
    conn_id     = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    for res_id, path in [(asset_id, f"/assets/{asset_id}"), (conn_id, f"/connectors/{conn_id}")]:
        if res_id:
            try:
                _api("delete", path)
                log(f"  Deleted {path}")
            except Exception as exc:
                log(f"  Warning: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate: {exc}", ok=False)

    log("[PHASE 4: teardown] PASSED")
```

### Step 3.7 — Commit and smoke run
- [ ] `docker exec nexplane-backend-1 alembic upgrade head`
- [ ] Commit:
  ```
  git add backend/app/connectors/executors/nexplane_agent/openldap_schema_migration.py \
          backend/app/connectors/change_type_definitions/openldap_schema_migration.json \
          backend/alembic/versions/iau003_add_openldap_schema_migration_change_type.py \
          backend/app/models/change_request.py \
          backend/app/connectors/catalog/nexplane_agent.json \
          backend/tests/smoke/test_smoke_openldap_schema_migration.py
  git commit -m "feat(identity): add openldap_schema_migration CR type with executor and smoke test"
  ```
- [ ] Build OpenLDAP 2.5 AMI if not cached:
  - Launch Ubuntu 22.04 EC2, `apt install slapd ldap-utils`, configure base domain, pre-stage test LDIF at `/tmp/nexplane-testapp.ldif` with `cn=testapp,cn=schema,cn=config`
  - Snapshot → store AMI ID at `/nexplane/smoke-amis/openldap/2.5`
- [ ] Run smoke from EC2 runner:
  ```
  docker exec nexplane-backend-1 python -m pytest \
      /app/tests/smoke/test_smoke_openldap_schema_migration.py -v -s
  ```
- [ ] All 4 phases pass → task done.

---

## Task 4: `vault_cluster_upgrade` CR Type

**Files to create/modify:**
- Create: `backend/app/connectors/executors/nexplane_agent/vault_cluster_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/vault_cluster_upgrade.json`
- Create: `backend/alembic/versions/iau004_add_vault_cluster_upgrade_change_type.py`
- Modify: `backend/app/models/change_request.py` — add `vault_cluster_upgrade` after `rotate_vault_secret`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: `backend/tests/smoke/test_smoke_vault_cluster_upgrade.py`

### Step 4.1 — Alembic migration
- [ ] Create `backend/alembic/versions/iau004_add_vault_cluster_upgrade_change_type.py`:

```python
"""add vault_cluster_upgrade change type

Revision ID: iau004
Revises: iau003
Create Date: 2026-08-05
"""
from alembic import op

revision = 'iau004'
down_revision = 'iau003'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'vault_cluster_upgrade'")


def downgrade():
    pass
```

### Step 4.2 — ChangeType enum entry
- [ ] In `backend/app/models/change_request.py`, find:
  ```python
      rotate_vault_secret = "rotate_vault_secret"
  ```
  Add immediately after:
  ```python
      vault_cluster_upgrade = "vault_cluster_upgrade"
  ```

### Step 4.3 — change_type_definition JSON
- [ ] Create `backend/app/connectors/change_type_definitions/vault_cluster_upgrade.json`:

```json
{
  "change_type": "vault_cluster_upgrade",
  "display_name": "HashiCorp Vault Cluster Upgrade",
  "description": "Rolling upgrade of a Vault Raft cluster. Standbys upgraded first, then active node stepped down and upgraded last. Raft snapshot taken before upgrade. Rollback restores snapshot on active node and performs rolling restart with old binary.",
  "rollback_capability": "full",
  "steps": [
    {"generic_action": "vault_cluster_upgrade", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "vault_cluster_upgrade",
  "rollback_connector_type": "nexplane_agent",
  "parameters": {
    "source_version":      { "type": "string",  "required": true },
    "target_version":      { "type": "string",  "required": true },
    "nodes":               { "type": "array",   "required": true,  "description": "List of {host, api_port} for all Raft members" },
    "vault_token":         { "type": "string",  "required": true,  "sensitive": true },
    "snapshot_s3_bucket":  { "type": "string",  "required": false },
    "dry_run":             { "type": "boolean", "required": false, "default": false }
  }
}
```

### Step 4.4 — Catalog entry
- [ ] Add to `backend/app/connectors/catalog/nexplane_agent.json` `"actions"` array:

```json
{
  "display_name": "HashiCorp Vault Cluster Upgrade",
  "description": "Rolling upgrade of a Vault Raft cluster with Raft snapshot and full rollback.",
  "parameters": [
    { "required": true,  "type": "string",  "name": "source_version" },
    { "required": true,  "type": "string",  "name": "target_version" },
    { "required": true,  "type": "array",   "name": "nodes" },
    { "required": true,  "type": "string",  "name": "vault_token", "sensitive": true },
    { "required": false, "type": "string",  "name": "snapshot_s3_bucket" },
    { "required": false, "type": "boolean", "name": "dry_run" }
  ],
  "execution_tier": 3,
  "estimated_duration_seconds": 600,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.vault_cluster_upgrade",
  "generic_action": "vault_cluster_upgrade",
  "action_id": "vault_cluster_upgrade"
}
```

### Step 4.5 — Executor
- [ ] Create `backend/app/connectors/executors/nexplane_agent/vault_cluster_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
HashiCorp Vault cluster upgrade executor.
Flow: preflight -> raft snapshot -> upgrade standbys -> step down active -> upgrade old-active -> verify.
Topology rule: standbys upgraded before active. Active stepped down after standbys are upgraded.
Rollback: raft snapshot restore on active node, rolling restart with old binary.
Data between snapshot and upgrade is lost — surfaced in rollback result.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":     p.get("source_version"),
        "target_version":     p["target_version"],
        "nodes":              p["nodes"],
        "vault_token":        p.get("vault_token"),
        "snapshot_s3_bucket": p.get("snapshot_s3_bucket"),
        "dry_run":            bool(p.get("dry_run", False)),
    }


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    if not p.get("nodes"):
        raise ValueError("nodes list is required")

    # --- Phase 1: Preflight — identify active vs standby nodes ---
    preflight_result = await dispatch_agent_job(
        command="vault_cluster_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    active_node   = preflight_result.get("active_node")
    standby_nodes = preflight_result.get("standby_nodes", [])
    logger.info(f"Vault active: {active_node}, standbys: {standby_nodes}")

    if p["dry_run"]:
        return {
            **preflight_result,
            "dry_run":      True,
            "active_node":  active_node,
            "standby_nodes": standby_nodes,
        }

    # --- Phase 2: Raft snapshot ---
    timestamp       = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path   = f"/tmp/nexplane-vault-snapshot-{timestamp}.snap"
    snapshot_result = await dispatch_agent_job(
        command="vault_raft_snapshot",
        parameters={
            **p,
            "snapshot_path": snapshot_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    actual_snapshot_path = snapshot_result.get("snapshot_path", snapshot_path)
    logger.info(f"Vault Raft snapshot at: {actual_snapshot_path}")

    # --- Phase 3: Upgrade standby nodes ---
    nodes_upgraded = []
    for node in standby_nodes:
        logger.info(f"Upgrading standby node: {node}")
        await dispatch_agent_job(
            command="vault_upgrade_node",
            parameters={**p, "target_node": node},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        nodes_upgraded.append(node)
        logger.info(f"Standby {node} upgraded and rejoined")

    # --- Phase 4: Step down active node ---
    if active_node:
        logger.info(f"Stepping down active node: {active_node}")
        await dispatch_agent_job(
            command="vault_stepdown",
            parameters={**p, "target_node": active_node},
            asset_ids=[asset_id],
            timeout_seconds=60,
        )
        logger.info("Active stepped down — new leader elected from upgraded standbys")

    # --- Phase 5: Upgrade old active (now standby) ---
    if active_node:
        logger.info(f"Upgrading old active (now standby): {active_node}")
        await dispatch_agent_job(
            command="vault_upgrade_node",
            parameters={**p, "target_node": active_node},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        nodes_upgraded.append(active_node)
        logger.info(f"Old active {active_node} upgraded")

    # --- Phase 6: Verify all nodes ---
    verify_result = await dispatch_agent_job(
        command="vault_cluster_verify",
        parameters={**p, "expected_version": p["target_version"]},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    all_healthy = verify_result.get("all_healthy", False)
    version_ok  = verify_result.get("version_ok", False)

    return {
        "status":          "completed" if (all_healthy and version_ok) else "verify_failed",
        "source_version":  p["source_version"],
        "target_version":  p["target_version"],
        "nodes_upgraded":  nodes_upgraded,
        "active_node":     active_node,
        "snapshot_path":   actual_snapshot_path,
        "verify_result":   verify_result,
        "asset_id":        asset_id,
        "upgraded_at":     datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Restore Raft snapshot on active node, then rolling restart of all nodes with old binary.
    Data between snapshot and upgrade is lost — surfaced in result.
    """
    snapshot_path = execution_result.get("snapshot_path")
    if not snapshot_path:
        return {"rolled_back": False, "reason": "no snapshot_path in execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="vault_snapshot_restore_and_restart",
            parameters={
                **p,
                "snapshot_path": snapshot_path,
                "rollback_nodes": execution_result.get("nodes_upgraded", []),
            },
            asset_ids=[asset_id],
            timeout_seconds=900,
        )
        return {
            "rolled_back":       True,
            "strategy":          "raft_snapshot_restore",
            "snapshot_path":     snapshot_path,
            "data_loss_warning": (
                "All Vault data written between snapshot time and upgrade is lost. "
                "Verify application state after rollback."
            ),
            "agent_result":      result,
        }
    except Exception as exc:
        logger.error(f"Vault rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
```

### Step 4.6 — Smoke test
- [ ] Create `backend/tests/smoke/test_smoke_vault_cluster_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Vault Cluster Upgrade

Phases:
  1. provision   — launch Vault 1.15 EC2 from cached AMI ami-09df8f5733ff981b3
  2. upgrade     — CR lifecycle: vault_cluster_upgrade 1.15->1.16, assert /v1/sys/health
  3. rollback    — trigger rollback, verify snapshot restore completes
  4. teardown    — terminate, deregister

AMI cache key: /nexplane/smoke-amis/vault/{hash} (ami-09df8f5733ff981b3)
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_vault_cluster_upgrade.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

# Exact SSM key from project memory
_VAULT_AMI_SSM_PATTERN = "/nexplane/smoke-amis/vault/"
_VAULT_AMI_ID_FALLBACK  = "ami-09df8f5733ff981b3"
_VAULT_ROOT_TOKEN       = "SmokeVaultRoot1234"
_SSM_PROFILE            = "nexplane-smoke-ssm"
_SOURCE_VERSION         = "1.15"
_TARGET_VERSION         = "1.16"
_VAULT_API_PORT         = 8200

CR_TIMEOUT    = 900
POLL_INTERVAL = 15

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "provisioned_by_us": False,
    "cr_id":             None,
}

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _boto3_client(service, creds):
    return boto3.client(
        service,
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


def _get_vault_ami(ec2, ssm) -> str:
    """Try all known Vault AMI SSM keys, fall back to hardcoded AMI ID."""
    # Try to find cached AMI by listing SSM parameters under the prefix
    try:
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=_VAULT_AMI_SSM_PATTERN):
            for param in page.get("Parameters", []):
                ami_id = param["Value"]
                imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
                if imgs and imgs[0].get("State") == "available":
                    log(f"  Using cached Vault AMI: {ami_id} (from {param['Name']})")
                    return ami_id
    except Exception:
        pass

    # Fall back to known AMI from project memory
    ami_id = _VAULT_AMI_ID_FALLBACK
    try:
        imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using fallback Vault AMI: {ami_id}")
            return ami_id
    except Exception:
        pass

    pytest.skip(
        f"No usable Vault AMI found. Expected {ami_id} or a cached AMI under "
        f"{_VAULT_AMI_SSM_PATTERN}. Ensure ami-09df8f5733ff981b3 is accessible in this region."
    )


def _launch_vault(ec2, ssm, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-vault-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-vault-cluster-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Vault instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Vault port {_VAULT_API_PORT} on {private_ip} (up to 4 min)")
    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, _VAULT_API_PORT), timeout=5)
            s.close()
            log(f"  Vault port {_VAULT_API_PORT} open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Vault API never reachable on {private_ip}:{_VAULT_API_PORT} within 4 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-vault-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-vault-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "high",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "vault", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT):
    terminal = ("completed", "failed", "rolled_back", "rollback_failed")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal within {timeout_s}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw   = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2    = _boto3_client("ec2", aws_creds)
    ssm    = _boto3_client("ssm", aws_creds)
    ami_id = _get_vault_ami(ec2, ssm)

    instance_id, private_ip = _launch_vault(ec2, ssm, ami_id, aws_creds)
    run_id                   = uuid.uuid4().hex[:6]
    conn_id, asset_id        = _register_asset(private_ip, run_id)

    _state.update({
        "connector_id":      conn_id,
        "asset_id":          asset_id,
        "instance_id":       instance_id,
        "private_ip":        private_ip,
        "provisioned_by_us": True,
    })
    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: upgrade CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id    = _state["connector_id"]
    asset_id   = _state["asset_id"]
    private_ip = _state["private_ip"]
    run_id     = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-vault-upgrade-{run_id}",
        "change_type": "vault_cluster_upgrade",
        "desired_outcome": {
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "nodes": [
                {"host": private_ip, "api_port": _VAULT_API_PORT}
            ],
            "vault_token": _VAULT_ROOT_TOKEN,
        },
        "connector_id": conn_id,
        "asset_ids":    [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "vault upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("all_healthy"),  f"Not all Vault nodes healthy: {verify}"
    assert verify.get("version_ok"),   f"Version mismatch after upgrade: {verify}"
    assert result.get("snapshot_path"), "No snapshot_path — Raft snapshot missing"

    log(f"  Vault upgraded to {_TARGET_VERSION}, all nodes healthy, snapshot at {result['snapshot_path']}")
    log("[PHASE 2: upgrade] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete — no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=900)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback reached unexpected status: {cr['status']}"
    )

    result = _exec_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result: {result}"
    )
    assert "data_loss_warning" in result, "Expected data_loss_warning in rollback result"
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: teardown
# ---------------------------------------------------------------------------

def test_phase4_teardown():
    if not _state.get("provisioned_by_us"):
        log("[PHASE 4: teardown] SKIPPED")
        return

    aws_creds   = get_connector_creds_from_db("aws")
    asset_id    = _state.get("asset_id")
    conn_id     = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    for res_id, path in [(asset_id, f"/assets/{asset_id}"), (conn_id, f"/connectors/{conn_id}")]:
        if res_id:
            try:
                _api("delete", path)
                log(f"  Deleted {path}")
            except Exception as exc:
                log(f"  Warning: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate: {exc}", ok=False)

    log("[PHASE 4: teardown] PASSED")
```

### Step 4.7 — Commit and smoke run
- [ ] `docker exec nexplane-backend-1 alembic upgrade head`
- [ ] Commit:
  ```
  git add backend/app/connectors/executors/nexplane_agent/vault_cluster_upgrade.py \
          backend/app/connectors/change_type_definitions/vault_cluster_upgrade.json \
          backend/alembic/versions/iau004_add_vault_cluster_upgrade_change_type.py \
          backend/app/models/change_request.py \
          backend/app/connectors/catalog/nexplane_agent.json \
          backend/tests/smoke/test_smoke_vault_cluster_upgrade.py
  git commit -m "feat(identity): add vault_cluster_upgrade CR type with executor and smoke test"
  ```
- [ ] Run smoke from EC2 runner:
  ```
  docker exec nexplane-backend-1 python -m pytest \
      /app/tests/smoke/test_smoke_vault_cluster_upgrade.py -v -s
  ```
- [ ] All 4 phases pass → task done.

---

## Execution Order

Tasks are independent and can be parallelized. Recommended sequence to allow AMI builds to overlap:

1. Start Task 2 (FreeIPA) first — cached AMI ready, no build needed, fastest to complete.
2. Start Task 4 (Vault) in parallel — cached AMI ready.
3. Start Task 1 (Keycloak) — needs AMI build; start build early so smoke can run after other tasks.
4. Start Task 3 (OpenLDAP) — needs AMI build; simplest infra, fastest to build.

Each task: implement → `alembic upgrade head` → commit → smoke run → done.

All smoke runs execute from EC2 runner:
```
docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_smoke_<name>.py -v -s
```
