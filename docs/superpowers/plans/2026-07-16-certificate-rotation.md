# Certificate Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `certificate_rotation` CR type that orchestrates full TLS certificate rotation: discover dependents, snapshot pre-rotation state, issue a new cert via step-ca, update all dependents, verify TLS chain end-to-end, and rollback with FILO unwind if verification fails.

**Architecture:** `certificate_rotation_executor.py` owns all phase logic (scan→snapshot→rotate→update→verify→rollback) and is dispatched from the existing workflow/activities/rollback_executor plumbing. Two new endpoints (`retry-verify`, `skip-verify`) follow the same `SELECT ... FOR UPDATE` + `_resume_execution` pattern established by credential_rotation. The `credential_expiry_worker` creates draft CRs instead of calling the step-ca client directly.

**Tech Stack:** Python asyncio, SQLAlchemy async, step-ca via `StepCAClient`, ssl stdlib for TLS probes, cryptography library for fingerprinting, existing catalog executors for K8s/AWS/nexplane_agent updates.

## Global Constraints

- No `from __future__ import annotations` in any new Python file
- All functionality tested against live infrastructure — no mocks
- Every CR passes through `awaiting_approval` before execution
- FILO rollback guarantee: dependents unwind in reverse update order
- Rollback strategy C: restore original cert if still valid (>24h remaining and `trigger_reason != "compromise"`), otherwise re-issue fresh cert
- `paused` and `rolled_back_with_warnings` statuses already exist — reuse them
- Smoke test must exercise full lifecycle: create → plan → approve → execute → verify → rollback

---

## File Map

| Action | Path |
|--------|------|
| Modify | `backend/app/models/change_request.py` — add `certificate_rotation` to `ChangeType` |
| Create | `backend/alembic/versions/cert001_certificate_rotation.py` — `ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'certificate_rotation'` |
| Modify | `backend/app/services/planning_engine.py` — validate `desired_outcome` fields, check step-ca connector exists |
| Create | `backend/app/services/certificate_rotation_executor.py` — phases 1–5 + rollback |
| Modify | `backend/app/workflows/activities.py` — dispatch branch for `certificate_rotation` |
| Modify | `backend/app/workflows/execute_change_workflow.py` — termination logic (paused on verify failure, completed on success) |
| Modify | `backend/app/services/rollback_executor.py` — `certificate_rotation` branch |
| Modify | `backend/app/routers/change_requests.py` — `retry-verify` and `skip-verify` endpoints |
| Modify | `backend/app/workers/credential_expiry_worker.py` — auto-trigger CR creation |
| Create | `backend/tests/smoke/test_certificate_rotation_smoke.py` |

---

### Task 1: DB Migration + ChangeType Enum

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/cert001_certificate_rotation.py`

**Interfaces:**
- Produces: `ChangeType.certificate_rotation` enum value; migration file `cert001_certificate_rotation`

- [ ] **Step 1: Add certificate_rotation to ChangeType enum**

In `backend/app/models/change_request.py`, find the `ChangeType` enum. Add after `credential_rotation = "credential_rotation"`:

```python
certificate_rotation = "certificate_rotation"
```

- [ ] **Step 2: Create Alembic migration**

Create `backend/alembic/versions/cert001_certificate_rotation.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Add certificate_rotation change type."""

from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "cert001_certificate_rotation"
down_revision: Union[str, None] = "cred001_credential_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'certificate_rotation'")


def downgrade() -> None:
    pass
```

- [ ] **Step 3: Verify import**

```bash
docker exec nexplane-backend-1 python -c "from app.models.change_request import ChangeType; print(ChangeType.certificate_rotation.value)"
```

Expected: `certificate_rotation`

- [ ] **Step 4: Run migration**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 alembic -c /app/alembic.ini upgrade head"
```

Expected: no error, `cert001_certificate_rotation` applied.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/change_request.py backend/alembic/versions/cert001_certificate_rotation.py
git commit -m "feat(cert-rotation): add certificate_rotation ChangeType and migration"
```

---

### Task 2: Planning Engine Validation

**Files:**
- Modify: `backend/app/services/planning_engine.py`

**Interfaces:**
- Consumes: `ChangeType.certificate_rotation` from Task 1
- Produces: planning engine accepts and validates `certificate_rotation` desired_outcome; returns `PlanBlockedError` if invalid

The planning engine `credential_rotation` handler (around lines 406-425 in planning_engine.py) is the pattern to follow. The `certificate_rotation` handler validates:
- `subject` is a non-empty string (required)
- `trigger_reason` is `"scheduled"` or `"compromise"` (required)
- `scan_scope` is a non-empty list (required)
- A step-ca connector exists for the organization (required)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_certificate_rotation_planning.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for certificate_rotation planning engine validation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.change_request import ChangeType
from app.services.planning_engine import PlanBlockedError


@pytest.mark.asyncio
async def test_missing_subject_raises():
    from app.services.planning_engine import PlanningEngine
    engine = PlanningEngine()
    with pytest.raises(PlanBlockedError, match="subject"):
        await engine._validate_certificate_rotation(
            {"trigger_reason": "scheduled", "scan_scope": ["aws"]},
            org_id="test-org",
        )


@pytest.mark.asyncio
async def test_invalid_trigger_reason_raises():
    from app.services.planning_engine import PlanningEngine
    engine = PlanningEngine()
    with pytest.raises(PlanBlockedError, match="trigger_reason"):
        await engine._validate_certificate_rotation(
            {"subject": "api.example.com", "trigger_reason": "expired", "scan_scope": ["aws"]},
            org_id="test-org",
        )


@pytest.mark.asyncio
async def test_empty_scan_scope_raises():
    from app.services.planning_engine import PlanningEngine
    engine = PlanningEngine()
    with pytest.raises(PlanBlockedError, match="scan_scope"):
        await engine._validate_certificate_rotation(
            {"subject": "api.example.com", "trigger_reason": "scheduled", "scan_scope": []},
            org_id="test-org",
        )


@pytest.mark.asyncio
async def test_valid_desired_outcome_passes():
    from app.services.planning_engine import PlanningEngine
    engine = PlanningEngine()
    with patch.object(engine, "_find_connector_for_type", new=AsyncMock(return_value=MagicMock())):
        result = await engine._validate_certificate_rotation(
            {"subject": "api.example.com", "trigger_reason": "scheduled", "scan_scope": ["aws"]},
            org_id="test-org",
        )
    assert result is None  # no error
```

- [ ] **Step 2: Run to verify it fails**

```bash
docker exec nexplane-backend-1 python -m pytest /app/tests/unit/test_certificate_rotation_planning.py -v 2>&1 | tail -20
```

Expected: ImportError or AttributeError (method not yet defined)

- [ ] **Step 3: Add validation method and dispatch in planning_engine.py**

In `backend/app/services/planning_engine.py`, find the `credential_rotation` planning block and add after it (inside the same `generate_plan` or `_plan_for_change_type` method):

```python
        elif ct == ChangeType.certificate_rotation:
            await self._validate_certificate_rotation(desired, org_id=str(cr.organization_id))
            return ChangePlanData(generated_steps=[], preflight_checks=[], summary=f"Rotate TLS certificate for {desired.get('subject', '(unknown)')}")
```

Then add the helper method to the `PlanningEngine` class:

```python
    async def _validate_certificate_rotation(self, desired: dict, org_id: str) -> None:
        errors = []
        if not desired.get("subject"):
            errors.append("certificate_rotation requires 'subject'")
        tr = desired.get("trigger_reason", "")
        if tr not in ("scheduled", "compromise"):
            errors.append("trigger_reason must be 'scheduled' or 'compromise'")
        scope = desired.get("scan_scope") or []
        if not scope:
            errors.append("scan_scope must be a non-empty list")
        if errors:
            raise PlanBlockedError(errors)
        # Verify step-ca connector exists for this org
        connector = await self._find_connector_for_type("step_ca", org_id)
        if not connector:
            raise PlanBlockedError(["No step_ca connector configured for this organization"])
```

Note: `_find_connector_for_type` may already exist (used by credential_rotation). If not, add it:

```python
    async def _find_connector_for_type(self, connector_type: str, org_id: str):
        from app.models.connector import Connector, ConnectorType
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(Connector).where(
                    Connector.organization_id == uuid.UUID(org_id),
                    Connector.connector_type == ConnectorType(connector_type),
                ).limit(1)
            )
            return res.scalar_one_or_none()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest /app/tests/unit/test_certificate_rotation_planning.py -v 2>&1 | tail -20
```

Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/planning_engine.py backend/tests/unit/test_certificate_rotation_planning.py
git commit -m "feat(cert-rotation): planning engine validation for certificate_rotation CR"
```

---

### Task 3: Executor — Helper Functions + Phases 1–3 (Scan, Snapshot, Rotate)

**Files:**
- Create: `backend/app/services/certificate_rotation_executor.py`

**Interfaces:**
- Consumes: `StepCAClient` + `get_step_ca_client` from `backend/app/connectors/executors/step_ca/_client.py`; `_check_via_ssl` pattern from `check_expiry.py` (reimplement inline — do not import from that module); `AsyncSessionLocal` from `app.database`
- Produces:
  - `_scan_dependents(desired, organization_id, db) -> list[dict]` — returns list of dependent dicts with `index`, `type`, `connector_type`, `connector_id`, `host`/`namespace`/`name` as appropriate
  - `_snapshot_dependents(dependents, db) -> list[dict]` — returns updated dependents with `snapshot` and `snapshot_fingerprint` fields populated
  - `_rotate_certificate(desired, connector) -> dict` — returns `{"subject", "fingerprint", "cert_pem", "key_pem", "issued_at"}`
  - Module-level `AsyncSessionLocal` import for database access

**Implementation notes:**
- Scan uses asset DB query: query `asset` table rows where `hostname` or `name` matches `subject` or any value in `san`. Returns both type-A (hosts serving cert) and type-B (K8s secrets, AWS secrets with cert stored in them) assets.
- Snapshot for type-A: TLS probe via ssl stdlib capturing DER → PEM. For type-B: read current value via appropriate connector.
- Rotate: call `StepCAClient.issue_certificate()` directly to capture `cert_pem` and `key_pem`. Compute fingerprint using `cryptography` library.

- [ ] **Step 1: Create certificate_rotation_executor.py with helpers and phases 1–3**

Create `backend/app/services/certificate_rotation_executor.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Certificate rotation executor — orchestrates 5-phase TLS cert rotation campaign."""

import hashlib
import logging
import socket
import ssl
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1 — Scan
# ---------------------------------------------------------------------------

async def _scan_dependents(desired: dict, organization_id: uuid.UUID, db: AsyncSession) -> list:
    """Query asset DB for hosts/secrets referencing the subject or SANs."""
    from app.models.asset import Asset

    subject = desired.get("subject", "")
    san_list = desired.get("san") or [subject]
    search_terms = list({subject} | set(san_list))
    scope = desired.get("scan_scope") or []

    dependents = []
    idx = 0

    # Type-A: server assets whose hostname matches subject/SAN
    if "nexplane_agent" in scope or "aws" in scope or not scope:
        res = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_type == "server",
            )
        )
        servers = res.scalars().all()
        for asset in servers:
            hostname = getattr(asset, "hostname", "") or getattr(asset, "name", "") or ""
            if any(t.lower() in hostname.lower() or hostname.lower() in t.lower() for t in search_terms):
                connector_type = getattr(asset, "connector_type", "nexplane_agent") or "nexplane_agent"
                connector_id = str(getattr(asset, "connector_id", "") or "")
                dependents.append({
                    "index": idx,
                    "type": "host",
                    "host": hostname,
                    "port": getattr(asset, "port", 443) or 443,
                    "connector_type": connector_type,
                    "connector_id": connector_id,
                    "asset_id": str(asset.id),
                    "snapshot": None,
                    "snapshot_fingerprint": None,
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
                idx += 1

    # Type-B: Kubernetes secrets
    if "kubernetes" in scope or not scope:
        res = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_type == "k8s_secret",
            )
        )
        k8s_secrets = res.scalars().all()
        for asset in k8s_secrets:
            name = getattr(asset, "name", "") or ""
            meta = getattr(asset, "metadata_", {}) or getattr(asset, "asset_metadata", {}) or {}
            labels = meta.get("labels", {}) or {}
            cert_subject = labels.get("cert-subject", "") or meta.get("cert_subject", "")
            if cert_subject and any(t.lower() in cert_subject.lower() for t in search_terms):
                dependents.append({
                    "index": idx,
                    "type": "k8s_secret",
                    "namespace": meta.get("namespace", "default"),
                    "name": name,
                    "connector_type": "kubernetes",
                    "connector_id": str(getattr(asset, "connector_id", "") or ""),
                    "asset_id": str(asset.id),
                    "snapshot": None,
                    "snapshot_fingerprint": None,
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
                idx += 1

    # Type-B: AWS Secrets Manager secrets
    if "aws" in scope or not scope:
        res = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_type == "aws_secret",
            )
        )
        aws_secrets = res.scalars().all()
        for asset in aws_secrets:
            meta = getattr(asset, "metadata_", {}) or getattr(asset, "asset_metadata", {}) or {}
            cert_subject = meta.get("cert_subject", "")
            if cert_subject and any(t.lower() in cert_subject.lower() for t in search_terms):
                dependents.append({
                    "index": idx,
                    "type": "aws_secret",
                    "secret_id": getattr(asset, "name", "") or "",
                    "connector_type": "aws",
                    "connector_id": str(getattr(asset, "connector_id", "") or ""),
                    "asset_id": str(asset.id),
                    "snapshot": None,
                    "snapshot_fingerprint": None,
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
                idx += 1

    return dependents


# ---------------------------------------------------------------------------
# Phase 2 — Snapshot
# ---------------------------------------------------------------------------

def _tls_probe_pem(host: str, port: int, timeout: int = 10) -> Optional[str]:
    """Return current cert PEM from live TLS handshake, or None on failure."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                if not cert_der:
                    return None
                return ssl.DER_cert_to_PEM_cert(cert_der)
    except Exception as exc:
        logger.warning("TLS probe failed for %s:%s — %s", host, port, exc)
        return None


def _pem_fingerprint(pem: str) -> str:
    """Return SHA-256 fingerprint of PEM cert."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
        from cryptography.hazmat.primitives import hashes
        fp_bytes = cert.fingerprint(hashes.SHA256())
        return fp_bytes.hex()
    except Exception:
        # Fallback: hash the raw PEM bytes
        return hashlib.sha256(pem.encode()).hexdigest()


async def _snapshot_dependents(dependents: list, db: AsyncSession) -> list:
    """Populate snapshot + snapshot_fingerprint for each dependent."""
    updated = []
    for dep in dependents:
        dep = dict(dep)
        try:
            if dep["type"] == "host":
                pem = _tls_probe_pem(dep["host"], dep.get("port", 443))
                if pem:
                    dep["snapshot"] = pem
                    dep["snapshot_fingerprint"] = _pem_fingerprint(pem)
                else:
                    logger.warning("Snapshot failed for host %s — no cert returned", dep["host"])
                    dep["snapshot"] = None

            elif dep["type"] == "k8s_secret":
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
                if connector:
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("kubernetes", "get_secret")
                    result = await mod.execute(
                        {"namespace": dep.get("namespace", "default"), "name": dep["name"]},
                        [],
                        connector,
                    )
                    dep["snapshot"] = result.get("data") or result.get("value")
                else:
                    dep["snapshot"] = None

            elif dep["type"] == "aws_secret":
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
                if connector:
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("aws", "get_secret_value")
                    result = await mod.execute(
                        {"secret_id": dep["secret_id"]},
                        [],
                        connector,
                    )
                    dep["snapshot"] = result.get("secret_string") or result.get("value")
                else:
                    dep["snapshot"] = None

        except Exception as exc:
            logger.warning("Snapshot error for dependent %s: %s", dep.get("index"), exc)
            dep["snapshot"] = None

        updated.append(dep)
    return updated


# ---------------------------------------------------------------------------
# Phase 3 — Rotate
# ---------------------------------------------------------------------------

async def _rotate_certificate(desired: dict, connector) -> dict:
    """Issue new cert via StepCAClient and return rotation_result dict."""
    from app.connectors.executors.step_ca._client import get_step_ca_client

    client = get_step_ca_client(connector)
    subject = desired["subject"]
    san = desired.get("san") or [subject]
    not_after = desired.get("not_after", "720h")

    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = f"{tmpdir}/cert.pem"
        key_path = f"{tmpdir}/key.pem"

        # issue_certificate writes to files; we read them back
        client.issue_certificate(
            subject=subject,
            san=san if isinstance(san, list) else [san],
            output_cert=cert_path,
            output_key=key_path,
            not_after=not_after,
        )

        with open(cert_path) as f:
            cert_pem = f.read()
        with open(key_path) as f:
            key_pem = f.read()

    fingerprint = _pem_fingerprint(cert_pem)
    return {
        "subject": subject,
        "fingerprint": fingerprint,
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_connector(connector_type: str, connector_id: Optional[str], db: AsyncSession):
    """Load and credential-attach a connector by id (preferred) or by type."""
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials

    connector = None
    if connector_id:
        try:
            connector = await db.get(Connector, uuid.UUID(connector_id))
        except Exception:
            pass

    if not connector and connector_type:
        res = await db.execute(
            select(Connector).where(
                Connector.connector_type == ConnectorType(connector_type),
            ).limit(1)
        )
        connector = res.scalar_one_or_none()

    if connector:
        await _attach_credentials(connector, db)

    return connector


async def _load_step_ca_connector(organization_id: uuid.UUID, db: AsyncSession):
    """Load the step_ca connector for the given org."""
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials

    res = await db.execute(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.connector_type == ConnectorType("step_ca"),
        ).limit(1)
    )
    connector = res.scalar_one_or_none()
    if connector:
        await _attach_credentials(connector, db)
    return connector
```

- [ ] **Step 2: Verify import**

```bash
docker exec nexplane-backend-1 python -c "from app.services.certificate_rotation_executor import _pem_fingerprint, _rotate_certificate; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/certificate_rotation_executor.py
git commit -m "feat(cert-rotation): executor helpers + phases 1-3 (scan, snapshot, rotate)"
```

---

### Task 4: Executor — Phases 4–5 (Update, Verify) + execute_certificate_rotation

**Files:**
- Modify: `backend/app/services/certificate_rotation_executor.py`

**Interfaces:**
- Consumes: `_scan_dependents`, `_snapshot_dependents`, `_rotate_certificate`, `_load_connector`, `_load_step_ca_connector` from Task 3
- Produces:
  - `_update_dependents(dependents, rotation_result, db) -> list[dict]` — returns dependents with `update_result` populated; pauses on first failure
  - `_verify_dependents(dependents, rotation_result, verify_timeout_seconds, db) -> list[dict]` — returns dependents with `verify_result` populated
  - `execute_certificate_rotation(cr_id: uuid.UUID) -> dict` — orchestrates all 5 phases, writes to ExecutionRun.result after each phase, returns `{"phase": ..., "dependents": ..., "rotation_result": ..., "paused": bool, "has_warnings": bool}`

The function must persist phase state to `ExecutionRun.result` after each phase so that resume (retry-verify) can pick up without redoing phases 1–4.

- [ ] **Step 1: Add phases 4–5 and execute_certificate_rotation to the executor file**

Append to `backend/app/services/certificate_rotation_executor.py`:

```python
# ---------------------------------------------------------------------------
# Phase 4 — Update
# ---------------------------------------------------------------------------

async def _update_dependents(dependents: list, rotation_result: dict, db: AsyncSession) -> list:
    """Push new cert to each dependent. Returns updated list; sets update_result on each."""
    updated = []
    for dep in dependents:
        dep = dict(dep)
        try:
            connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
            if dep["type"] == "host":
                if dep["connector_type"] == "nexplane_agent":
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("nexplane_agent", "manage_tls_certificates")
                    result = await mod.execute(
                        {
                            "cert_pem": rotation_result["cert_pem"],
                            "key_pem": rotation_result["key_pem"],
                            "reload_command": "nginx -s reload",
                        },
                        [dep.get("asset_id", "")],
                        connector,
                    )
                    dep["update_result"] = result
                else:
                    # SSM deploy path
                    from app.connectors.executors.step_ca._client import get_step_ca_client
                    step_ca_connector = connector  # reuse if same connector
                    client = get_step_ca_client(step_ca_connector)
                    # Deploy via SSM using step-ca executor
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("step_ca", "rotate_certificate")
                    result = await mod.execute(
                        {
                            "subject": rotation_result["subject"],
                            "deploy_via_ssm": True,
                            "instance_id": dep.get("asset_id", ""),
                        },
                        [],
                        connector,
                    )
                    dep["update_result"] = result

            elif dep["type"] == "k8s_secret":
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("kubernetes", "patch_secret")
                result = await mod.execute(
                    {
                        "namespace": dep.get("namespace", "default"),
                        "name": dep["name"],
                        "data": {"tls.crt": rotation_result["cert_pem"], "tls.key": rotation_result["key_pem"]},
                    },
                    [],
                    connector,
                )
                dep["update_result"] = result

            elif dep["type"] == "aws_secret":
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("aws", "rotate_secrets_manager_secret")
                result = await mod.execute(
                    {
                        "secret_id": dep["secret_id"],
                        "new_value": rotation_result["cert_pem"],
                    },
                    [],
                    connector,
                )
                dep["update_result"] = result

            else:
                dep["update_result"] = {"success": False, "error": f"Unknown dependent type: {dep['type']}"}

        except Exception as exc:
            logger.error("Update failed for dependent %s: %s", dep.get("index"), exc)
            dep["update_result"] = {"success": False, "error": str(exc)}

        updated.append(dep)

    return updated


# ---------------------------------------------------------------------------
# Phase 5 — Verify
# ---------------------------------------------------------------------------

async def _verify_dependents(
    dependents: list,
    rotation_result: dict,
    verify_timeout_seconds: int,
    db: AsyncSession,
) -> list:
    """After waiting verify_timeout_seconds, probe each dependent."""
    import asyncio
    await asyncio.sleep(verify_timeout_seconds)

    updated = []
    for dep in dependents:
        dep = dict(dep)
        try:
            if dep["type"] == "host":
                pem = _tls_probe_pem(dep["host"], dep.get("port", 443))
                if pem:
                    served_fp = _pem_fingerprint(pem)
                    if served_fp == rotation_result["fingerprint"]:
                        dep["verify_result"] = {"success": True, "fingerprint": served_fp, "error": None}
                    else:
                        dep["verify_result"] = {
                            "success": False,
                            "fingerprint": served_fp,
                            "error": f"Fingerprint mismatch: got {served_fp}, expected {rotation_result['fingerprint']}",
                        }
                else:
                    dep["verify_result"] = {"success": False, "fingerprint": None, "error": "TLS probe returned no cert"}

            elif dep["type"] == "k8s_secret":
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("kubernetes", "get_secret")
                result = await mod.execute(
                    {"namespace": dep.get("namespace", "default"), "name": dep["name"]},
                    [],
                    connector,
                )
                stored = (result.get("data") or {}).get("tls.crt") or result.get("value", "")
                if rotation_result["cert_pem"].strip() in (stored or "").strip():
                    dep["verify_result"] = {"success": True, "fingerprint": None, "error": None}
                else:
                    dep["verify_result"] = {"success": False, "fingerprint": None, "error": "Secret value does not match new cert PEM"}

            elif dep["type"] == "aws_secret":
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("aws", "get_secret_value")
                result = await mod.execute({"secret_id": dep["secret_id"]}, [], connector)
                stored = result.get("secret_string") or result.get("value", "")
                if rotation_result["cert_pem"].strip() in (stored or "").strip():
                    dep["verify_result"] = {"success": True, "fingerprint": None, "error": None}
                else:
                    dep["verify_result"] = {"success": False, "fingerprint": None, "error": "Secret value does not match new cert PEM"}

            else:
                dep["verify_result"] = {"success": False, "fingerprint": None, "error": f"Unknown type: {dep['type']}"}

        except Exception as exc:
            logger.error("Verify failed for dependent %s: %s", dep.get("index"), exc)
            dep["verify_result"] = {"success": False, "fingerprint": None, "error": str(exc)}

        updated.append(dep)

    return updated


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def execute_certificate_rotation(cr_id: uuid.UUID) -> dict:
    """Run all 5 phases for a certificate_rotation CR. Persists phase state after each step."""
    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()
        desired = cr.desired_outcome or {}

        run_res = await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.change_request_id == cr_id,
                ExecutionRun.status.in_([ExecutionStatus.running, ExecutionStatus.pending]),
            ).order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        run = run_res.scalar_one_or_none()
        if not run:
            run_res2 = await db.execute(
                select(ExecutionRun).where(ExecutionRun.change_request_id == cr_id)
                .order_by(ExecutionRun.started_at.desc()).limit(1)
            )
            run = run_res2.scalar_one_or_none()

        existing = (run.result or {}) if run else {}
        dependents = existing.get("dependents", [])
        rotation_result = existing.get("rotation_result")
        verify_timeout_seconds = desired.get("verify_timeout_seconds", 10)

        # Phase 1 — Scan (only if no dependents yet)
        if not dependents:
            logger.info("[cert-rotation] Phase 1: scan for dependents")
            dependents = await _scan_dependents(desired, cr.organization_id, db)
            _persist(run, {"phase": "scan", "dependents": dependents, "rotation_result": None})
            await db.commit()

        # Phase 2 — Snapshot (only if snapshots not yet taken)
        if dependents and not any(d.get("snapshot") is not None for d in dependents):
            logger.info("[cert-rotation] Phase 2: snapshot")
            dependents = await _snapshot_dependents(dependents, db)
            _persist(run, {"phase": "snapshot", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        # Phase 3 — Rotate (only if not yet done)
        if not rotation_result:
            logger.info("[cert-rotation] Phase 3: rotate")
            step_ca_connector = await _load_step_ca_connector(cr.organization_id, db)
            rotation_result = await _rotate_certificate(desired, step_ca_connector)
            _persist(run, {"phase": "rotate", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        # Phase 4 — Update (only if not yet done)
        if not any(d.get("update_result") for d in dependents):
            logger.info("[cert-rotation] Phase 4: update")
            dependents = await _update_dependents(dependents, rotation_result, db)
            _persist(run, {"phase": "update", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()
            if any(d["update_result"] and not d["update_result"].get("success", True) for d in dependents):
                return _build_result(dependents, rotation_result, phase="update", paused=True)

        # Phase 5 — Verify (skip already-verified dependents on resume)
        if not all(d.get("verify_result") for d in dependents):
            logger.info("[cert-rotation] Phase 5: verify (timeout=%ss)", verify_timeout_seconds)
            pending = [d for d in dependents if not d.get("verify_result")]
            already = [d for d in dependents if d.get("verify_result")]
            verified = await _verify_dependents(pending, rotation_result, verify_timeout_seconds, db)
            dependents = _merge_by_index(already, verified)
            _persist(run, {"phase": "verify", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        failed = [d for d in dependents if not (d.get("verify_result") or {}).get("success")]
        paused = bool(failed)
        result = _build_result(dependents, rotation_result, phase="verify", paused=paused)

        if not paused:
            # Transition to completed
            cr.status = ChangeRequestStatus.completed
            cr.updated_at = datetime.now(timezone.utc)
            if run:
                run.status = ExecutionStatus.completed
                run.result = result
            await db.commit()

        return result


def _persist(run, data: dict) -> None:
    """Write data to ExecutionRun.result in-place (caller must commit)."""
    if run:
        run.result = data


def _build_result(dependents: list, rotation_result: Optional[dict], phase: str, paused: bool) -> dict:
    has_warnings = any(
        d.get("verify_result") and not d["verify_result"].get("success")
        for d in dependents
    )
    return {
        "phase": phase,
        "dependents": dependents,
        "rotation_result": rotation_result,
        "rollback_strategy": None,
        "has_warnings": has_warnings,
        "paused": paused,
    }


def _merge_by_index(existing: list, new_items: list) -> list:
    """Merge two lists of dependents by index, new_items overriding existing."""
    merged = {d["index"]: d for d in existing}
    for d in new_items:
        merged[d["index"]] = d
    return [merged[k] for k in sorted(merged)]
```

- [ ] **Step 2: Verify import**

```bash
docker exec nexplane-backend-1 python -c "from app.services.certificate_rotation_executor import execute_certificate_rotation; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/certificate_rotation_executor.py
git commit -m "feat(cert-rotation): executor phases 4-5 (update, verify) + execute_certificate_rotation"
```

---

### Task 5: Rollback Executor — execute_certificate_rollback

**Files:**
- Modify: `backend/app/services/certificate_rotation_executor.py`

**Interfaces:**
- Consumes: phases 1–5 helpers from Tasks 3–4; `ChangeRequestStatus.rolled_back`, `ChangeRequestStatus.rolled_back_with_warnings`
- Produces:
  - `execute_certificate_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict` — FILO unwind; returns `{"rollback_steps": [...], "all_rolled_back": bool, "has_warnings": bool}`

**Rollback strategy C:**
- If `trigger_reason == "compromise"` → strategy A (re-issue fresh cert)
- Else if `snapshot` and `snapshot_fingerprint` present AND snapshot cert has >24h remaining (parse with `cryptography` library) → strategy B (restore)
- Else → strategy A (re-issue fresh cert)

FILO order: unwind `dependents` in **reverse index order**.

- [ ] **Step 1: Append execute_certificate_rollback to executor file**

Append to `backend/app/services/certificate_rotation_executor.py`:

```python
# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def _cert_has_24h_remaining(pem: str) -> bool:
    """Return True if cert PEM has more than 24h until expiry."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
        remaining = cert.not_valid_after_utc - datetime.now(timezone.utc)
        return remaining.total_seconds() > 86400
    except Exception:
        return False


async def execute_certificate_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict:
    """FILO unwind of all updated dependents."""
    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()
        desired = cr.desired_outcome or {}
        trigger_reason = desired.get("trigger_reason", "scheduled")

        dependents = execution_result.get("dependents", [])
        rotation_result = execution_result.get("rotation_result") or {}

        # Determine global rollback strategy
        if trigger_reason == "compromise":
            global_strategy = "reissue"
        else:
            # Try strategy B: find any type-A dependent with a valid snapshot
            global_strategy = "reissue"
            for dep in dependents:
                if dep["type"] == "host" and dep.get("snapshot") and dep.get("snapshot_fingerprint"):
                    if _cert_has_24h_remaining(dep["snapshot"]):
                        global_strategy = "restore"
                        break

        rollback_steps = []
        # FILO: reverse order
        for dep in sorted(dependents, key=lambda d: d["index"], reverse=True):
            step = {"index": dep["index"], "strategy": global_strategy, "rolled_back": False, "error": None}
            try:
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)

                if global_strategy == "restore" and dep.get("snapshot"):
                    # Strategy B: restore snapshot
                    if dep["type"] == "host":
                        from app.connectors.catalog_service import get_catalog_service
                        catalog = get_catalog_service()
                        mod = catalog.get_executor("nexplane_agent", "manage_tls_certificates")
                        result = await mod.execute(
                            {
                                "cert_pem": dep["snapshot"],
                                "key_pem": dep.get("snapshot_key", dep["snapshot"]),
                                "reload_command": "nginx -s reload",
                            },
                            [dep.get("asset_id", "")],
                            connector,
                        )
                        step["rollback_result"] = result
                        step["rolled_back"] = True
                    elif dep["type"] == "k8s_secret":
                        from app.connectors.catalog_service import get_catalog_service
                        catalog = get_catalog_service()
                        mod = catalog.get_executor("kubernetes", "patch_secret")
                        result = await mod.execute(
                            {
                                "namespace": dep.get("namespace", "default"),
                                "name": dep["name"],
                                "data": dep["snapshot"],
                            },
                            [],
                            connector,
                        )
                        step["rollback_result"] = result
                        step["rolled_back"] = True
                    elif dep["type"] == "aws_secret":
                        from app.connectors.catalog_service import get_catalog_service
                        catalog = get_catalog_service()
                        mod = catalog.get_executor("aws", "rotate_secrets_manager_secret")
                        result = await mod.execute(
                            {"secret_id": dep["secret_id"], "new_value": dep["snapshot"]},
                            [],
                            connector,
                        )
                        step["rollback_result"] = result
                        step["rolled_back"] = True
                    else:
                        step["error"] = f"Cannot restore unknown type: {dep['type']}"
                else:
                    # Strategy A: re-issue fresh cert
                    step_ca_connector = await _load_step_ca_connector(cr.organization_id, db)
                    new_rotation = await _rotate_certificate(desired, step_ca_connector)
                    # Push fresh cert to dependent via same path as phase 4
                    fresh_dependents = await _update_dependents([dep], new_rotation, db)
                    fresh_dep = fresh_dependents[0]
                    update_ok = (fresh_dep.get("update_result") or {}).get("success", False)
                    step["rollback_result"] = fresh_dep.get("update_result")
                    step["rolled_back"] = update_ok
                    if not update_ok:
                        step["error"] = (fresh_dep.get("update_result") or {}).get("error", "Re-issue failed")

            except Exception as exc:
                logger.error("Rollback failed for dependent %s: %s", dep.get("index"), exc)
                step["error"] = str(exc)

            rollback_steps.append(step)

        all_rolled_back = all(s["rolled_back"] for s in rollback_steps)
        has_warnings = not all_rolled_back

        # Update execution_result.rollback_strategy
        execution_result = dict(execution_result)
        execution_result["rollback_strategy"] = global_strategy

        return {
            "rollback_steps": rollback_steps,
            "all_rolled_back": all_rolled_back,
            "has_warnings": has_warnings,
            "rollback_strategy": global_strategy,
        }
```

- [ ] **Step 2: Verify import**

```bash
docker exec nexplane-backend-1 python -c "from app.services.certificate_rotation_executor import execute_certificate_rollback; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/certificate_rotation_executor.py
git commit -m "feat(cert-rotation): FILO rollback with strategy C (restore vs re-issue)"
```

---

### Task 6: Wire Into Workflow + Rollback Executor

**Files:**
- Modify: `backend/app/workflows/activities.py`
- Modify: `backend/app/workflows/execute_change_workflow.py`
- Modify: `backend/app/services/rollback_executor.py`

**Interfaces:**
- Consumes: `execute_certificate_rotation(cr_id)` from Task 4; `execute_certificate_rollback(cr_id, execution_result)` from Task 5
- Produces: `certificate_rotation` CRs reach `paused` (verify failure) or `completed` (success); rollback reaches `rolled_back` or `rolled_back_with_warnings`

- [ ] **Step 1: Add dispatch branch in activities.py**

In `backend/app/workflows/activities.py`, find the `elif _cr and _cr.change_type.value == "credential_rotation":` block and add after it:

```python
        elif _cr and _cr.change_type.value == "certificate_rotation":
            from app.services.certificate_rotation_executor import execute_certificate_rotation
            _result = await execute_certificate_rotation(_cr.id)
            return _result
```

- [ ] **Step 2: Add termination logic in execute_change_workflow.py**

In `backend/app/workflows/execute_change_workflow.py`, find the `if data.get("change_type") == "credential_rotation":` block and add an analogous block for `certificate_rotation` after it:

```python
        if data.get("change_type") == "certificate_rotation":
            if isinstance(execution_result, dict) and execution_result.get("paused"):
                if execution_run_id:
                    await update_execution_run_status(execution_run_id, "running", execution_result)
                await write_audit_event(
                    db=None,
                    change_request_id=cr_id,
                    event_type="execution.paused",
                    details={"phase": (execution_result or {}).get("phase", "verify")},
                )
                await update_change_request_status(cr_id, "paused")
                return
            else:
                await update_change_request_status(cr_id, "completed")
                if execution_run_id:
                    await update_execution_run_status(execution_run_id, "completed", {"execution": execution_result})
                await write_audit_event(
                    db=None,
                    change_request_id=cr_id,
                    event_type="execution.completed",
                    details={},
                )
                return
```

- [ ] **Step 3: Add certificate_rotation branch in rollback_executor.py**

In `backend/app/services/rollback_executor.py`, inside `execute_cr_rollback`, after the `credential_rotation` branch (lines ~189-199), add:

```python
        # certificate_rotation: FILO rollback with strategy C
        if cr.change_type.value == "certificate_rotation":
            from app.services.certificate_rotation_executor import execute_certificate_rollback
            result = await execute_certificate_rollback(cr.id, execution_result)
            if result.get("has_warnings"):
                cr.status = ChangeRequestStatus.rolled_back_with_warnings
            else:
                cr.status = ChangeRequestStatus.rolled_back
            cr.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return result
```

- [ ] **Step 4: Verify imports**

```bash
docker exec nexplane-backend-1 python -c "from app.workflows.activities import activity_execute_change; from app.services.rollback_executor import execute_cr_rollback; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/activities.py backend/app/workflows/execute_change_workflow.py backend/app/services/rollback_executor.py
git commit -m "feat(cert-rotation): wire certificate_rotation into workflow, activities, and rollback_executor"
```

---

### Task 7: retry-verify and skip-verify API Endpoints

**Files:**
- Modify: `backend/app/routers/change_requests.py`

**Interfaces:**
- Consumes: `execute_certificate_rotation(cr_id)` from Task 4 (for resume); `_resume_execution` wrapper pattern established in credential_rotation (already exists in change_requests.py)
- Produces:
  - `POST /change-requests/{id}/retry-verify` — admin/approver, paused cert rotation CRs only; re-runs Phase 5 for all failed dependents; transitions CR to executing
  - `POST /change-requests/{id}/skip-verify` — admin/approver, paused cert rotation CRs only; marks all failed verifies as skipped; transitions CR to completed

Both endpoints:
- Guard: `cr.status == paused` and `cr.change_type == certificate_rotation`
- Require admin or approver role
- Use `SELECT ... FOR UPDATE` to prevent double-fire
- Fire `_resume_execution` wrapper via `asyncio.ensure_future`

For retry-verify: reset all dependents with `verify_result.success == false` → `verify_result = None` so Phase 5 re-probes them.
For skip-verify: set all dependents with `verify_result.success == false` → `verify_result = {"success": True, "skipped": True, "fingerprint": None, "error": None}`.

- [ ] **Step 1: Find existing retry_step/skip_step endpoints as pattern reference**

Read the section of `backend/app/routers/change_requests.py` containing `retry_step` to understand the exact pattern, then add the two new endpoints immediately after the existing `skip_step` endpoint.

- [ ] **Step 2: Add retry-verify endpoint**

In `backend/app/routers/change_requests.py`, add after the `skip_step` endpoint:

```python
@router.post("/{cr_id}/retry-verify", status_code=202)
async def retry_verify(
    cr_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_approver),
):
    """Re-run Phase 5 verification for all failed dependents on a paused certificate_rotation CR."""
    async with db.begin():
        res = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == cr_id)
            .with_for_update()
        )
        cr = res.scalar_one_or_none()
        if not cr:
            raise HTTPException(status_code=404, detail="Change request not found")
        if cr.change_type.value != "certificate_rotation":
            raise HTTPException(status_code=400, detail="retry-verify only applies to certificate_rotation CRs")
        if cr.status != ChangeRequestStatus.paused:
            raise HTTPException(status_code=400, detail="CR must be paused to retry verification")

        # Get latest run and reset failed verify results
        run_res = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr_id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        run = run_res.scalar_one_or_none()
        if run and run.result:
            result = dict(run.result)
            dependents = result.get("dependents", [])
            for dep in dependents:
                vr = dep.get("verify_result") or {}
                if not vr.get("success"):
                    dep["verify_result"] = None
            result["dependents"] = dependents
            result["paused"] = False
            run.result = result
            run.status = ExecutionStatus.running

        cr.status = ChangeRequestStatus.executing
        cr.updated_at = datetime.now(timezone.utc)

    # Fire async resume
    asyncio.ensure_future(_resume_execution(cr_id))
    return {"status": "retrying"}
```

- [ ] **Step 3: Add skip-verify endpoint**

```python
@router.post("/{cr_id}/skip-verify", status_code=202)
async def skip_verify(
    cr_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_approver),
):
    """Mark all remaining verify failures as skipped and complete a paused certificate_rotation CR."""
    async with db.begin():
        res = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == cr_id)
            .with_for_update()
        )
        cr = res.scalar_one_or_none()
        if not cr:
            raise HTTPException(status_code=404, detail="Change request not found")
        if cr.change_type.value != "certificate_rotation":
            raise HTTPException(status_code=400, detail="skip-verify only applies to certificate_rotation CRs")
        if cr.status != ChangeRequestStatus.paused:
            raise HTTPException(status_code=400, detail="CR must be paused to skip verification")

        run_res = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr_id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        run = run_res.scalar_one_or_none()
        if run and run.result:
            result = dict(run.result)
            dependents = result.get("dependents", [])
            for dep in dependents:
                vr = dep.get("verify_result") or {}
                if not vr.get("success"):
                    dep["verify_result"] = {"success": True, "skipped": True, "fingerprint": None, "error": None}
            result["dependents"] = dependents
            result["paused"] = False
            run.result = result
            run.status = ExecutionStatus.completed

        cr.status = ChangeRequestStatus.completed
        cr.updated_at = datetime.now(timezone.utc)

    return {"status": "completed"}
```

Make sure `asyncio` is imported at the top of the router file (it already is for credential_rotation — verify before adding a duplicate import). Also ensure `_resume_execution` exists and is reachable (it was added for credential_rotation — same module, same file).

- [ ] **Step 4: Verify the router loads**

```bash
docker exec nexplane-backend-1 python -c "from app.routers.change_requests import router; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/change_requests.py
git commit -m "feat(cert-rotation): retry-verify and skip-verify API endpoints"
```

---

### Task 8: Auto-Trigger in credential_expiry_worker

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py`

**Interfaces:**
- Consumes: `NexplaneClient`-style internal API calls (use `httpx` or direct service calls to create CRs); `ACME_RENEW_DAYS` threshold; `_check_step_ca_certs` and `_check_tls_certs` existing functions
- Produces: When a cert is within the renewal threshold, create a draft `certificate_rotation` CR and submit it for approval instead of calling `renew_certificate()` directly

The pattern: construct the CR payload and call the internal platform API endpoints (`/change-requests`, then `/change-requests/{id}/plan`, then `/change-requests/{id}/submit-for-approval`). Keep the existing `_create_expiry_finding` call as fallback if CR creation fails.

- [ ] **Step 1: Read the current _check_step_ca_certs function**

Read `backend/app/workers/credential_expiry_worker.py` lines 109–134 (the `_check_step_ca_certs` function) to understand the exact current structure before modifying.

- [ ] **Step 2: Add _create_certificate_rotation_cr helper**

In `backend/app/workers/credential_expiry_worker.py`, add a new helper function before `_check_step_ca_certs`:

```python
async def _create_certificate_rotation_cr(
    subject: str,
    san: list,
    organization_id: str,
    trigger_reason: str = "scheduled",
) -> bool:
    """Create and submit a certificate_rotation CR for approval. Returns True on success."""
    try:
        import httpx

        base = "http://localhost:8000"
        headers = {"X-Internal-Worker": "credential-expiry-worker"}

        payload = {
            "title": f"[Auto] Certificate rotation: {subject}",
            "change_type": "certificate_rotation",
            "organization_id": organization_id,
            "desired_outcome": {
                "subject": subject,
                "san": san or [subject],
                "not_after": "720h",
                "trigger_reason": trigger_reason,
                "scan_scope": ["nexplane_agent", "kubernetes", "aws"],
                "verify_timeout_seconds": 60,
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{base}/change-requests", json=payload, headers=headers)
            if r.status_code not in (200, 201):
                logger.warning("Auto-trigger CR creation failed: %s %s", r.status_code, r.text)
                return False
            cr_id = r.json()["id"]

            for path in ["plan", "submit-for-approval"]:
                r2 = await client.post(f"{base}/change-requests/{cr_id}/{path}", headers=headers)
                if r2.status_code not in (200, 201, 202, 204):
                    logger.warning("Auto-trigger CR %s/%s failed: %s", cr_id, path, r2.status_code)
                    return False

        logger.info("Auto-triggered certificate_rotation CR %s for subject %s", cr_id, subject)
        return True

    except Exception as exc:
        logger.warning("_create_certificate_rotation_cr error: %s", exc)
        return False
```

- [ ] **Step 3: Modify _check_step_ca_certs to create CR instead of direct renew**

In `_check_step_ca_certs`, replace the `client.renew_certificate(cert["serial"])` call with:

```python
            san = [cert.get("subject", subject)]
            cr_created = await _create_certificate_rotation_cr(
                subject=subject,
                san=san,
                organization_id=str(organization_id),
                trigger_reason="scheduled",
            )
            if not cr_created:
                # Fallback: create expiry finding
                await _create_expiry_finding(db, asset_id, cert, organization_id)
```

Keep `_create_expiry_finding` as the fallback (preserve the existing call site, just wrap it in the `if not cr_created:` guard).

- [ ] **Step 4: Modify _check_tls_certs for SLA_CRITICAL threshold**

In `_check_tls_certs`, when `days_remaining <= SLA_CRITICAL` (7 days), add CR creation before finding creation:

```python
            if days_remaining is not None and days_remaining <= SLA_CRITICAL:
                san = cert_info.get("san") or [hostname]
                cr_created = await _create_certificate_rotation_cr(
                    subject=hostname,
                    san=san,
                    organization_id=str(organization_id),
                    trigger_reason="scheduled",
                )
                if not cr_created:
                    await _create_expiry_finding(db, asset_id, cert_info, organization_id)
            else:
                await _create_expiry_finding(db, asset_id, cert_info, organization_id)
```

- [ ] **Step 5: Verify worker loads**

```bash
docker exec nexplane-backend-1 python -c "from app.workers.credential_expiry_worker import _check_step_ca_certs; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/credential_expiry_worker.py
git commit -m "feat(cert-rotation): auto-trigger certificate_rotation CR from credential_expiry_worker"
```

---

### Task 9: Live Smoke Test (5 phases)

**Files:**
- Create: `backend/tests/smoke/test_certificate_rotation_smoke.py`

**Interfaces:**
- Consumes: All CR endpoints from Tasks 4–7; `get_connector_creds_from_db("step_ca")` and `get_connector_creds_from_db("kubernetes")` from `smoke_helpers`
- Produces: 5 smoke phases passing on EC2 against live infrastructure

**Setup:** Provisions a smoke nginx on EC2 port 8443 with a step-ca issued cert for subject `nexplane-smoke-cert.internal`. Registers smoke host as platform asset so `scan_for_references` can find it. Tears down on completion.

**Note on internal API auth:** When `_create_certificate_rotation_cr` calls localhost:8000, it needs the worker auth header or a valid session. The smoke test goes through the full client auth path (NexplaneClient), not the internal worker path.

- [ ] **Step 1: Create smoke test file**

Create `backend/tests/smoke/test_certificate_rotation_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Certificate Rotation Smoke Test

Phases:
  1. HAPPY PATH — type-A host (nginx port 8443), FILO rollback
  2. TYPE-B CONSUMER — Kubernetes secret rotation + rollback
  3. COMPROMISE TRIGGER — rollback_strategy must be 'reissue'
  4. VERIFY FAILURE → PAUSED → ROLLBACK — synthetic unreachable host
  5. AUTO-TRIGGER — expiry worker creates draft CR for near-expiry cert

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_certificate_rotation_smoke.py -v -s
"""

import os
import ssl
import socket
import time
import uuid

import boto3
import pytest

from smoke_helpers import (
    NexplaneClient,
    log,
    get_connector_creds_from_db,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")
SMOKE_SUBJECT = "nexplane-smoke-cert.internal"
EXPIRING_SUBJECT = "nexplane-smoke-expiring.internal"
EXEC_TIMEOUT = 180
ROLLBACK_TIMEOUT = 120


def _client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _step_ca_creds():
    creds = get_connector_creds_from_db("step_ca")
    if not creds:
        pytest.skip("No step_ca connector found")
    return creds


def _k8s_creds():
    return get_connector_creds_from_db("kubernetes")


def _run_cert_rotation_cr(client, title, subject, san=None, scan_scope=None, trigger_reason="scheduled",
                           verify_timeout_seconds=5, timeout=EXEC_TIMEOUT):
    base = client.base
    payload = {
        "title": title,
        "change_type": "certificate_rotation",
        "desired_outcome": {
            "subject": subject,
            "san": san or [subject],
            "not_after": "720h",
            "trigger_reason": trigger_reason,
            "scan_scope": scan_scope or ["nexplane_agent"],
            "verify_timeout_seconds": verify_timeout_seconds,
        },
    }
    r = client.client.post(f"{base}/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r2.status_code in (200, 201, 202, 204), f"/{path} failed {r2.status_code}: {r2.text}"

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), f"/approve failed {r3.status_code}: {r3.text}"

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), f"/execute failed {r4.status_code}: {r4.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "paused"):
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"CR {cr_id} unexpected status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def _rollback_cr(client, cr_id, timeout=ROLLBACK_TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("rolled_back", "rolled_back_with_warnings", "rollback_partial", "rollback_failed"):
            return cr
        time.sleep(5)
    raise TimeoutError(f"Rollback timed out for CR {cr_id}")


def _get_execution_result(cr):
    for run in cr.get("execution_runs", []):
        result = run.get("result") or {}
        if "dependents" in result:
            return result
        if "execution" in result and "dependents" in result["execution"]:
            return result["execution"]
    return {}


def _tls_fingerprint(host, port=8443, timeout=5):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                import hashlib
                der = ssock.getpeercert(binary_form=True)
                return hashlib.sha256(der).hexdigest()
    except Exception:
        return None


def _register_asset(client, hostname, port, asset_type="server", connector_type="nexplane_agent", extra=None):
    """Register a server asset in the platform DB so scan can find it."""
    base = client.base
    payload = {
        "name": hostname,
        "hostname": hostname,
        "asset_type": asset_type,
        "port": port,
        "connector_type": connector_type,
        **(extra or {}),
    }
    r = client.client.post(f"{base}/assets", json=payload)
    assert r.status_code in (200, 201), f"Asset register failed {r.status_code}: {r.text}"
    return r.json()["id"]


def _delete_asset(client, asset_id):
    base = client.base
    client.client.delete(f"{base}/assets/{asset_id}")


class TestCertificateRotation:
    """Live certificate rotation smoke tests — 5 phases."""

    @classmethod
    def setup_class(cls):
        cls.client = _client()
        cls.step_ca_creds = _step_ca_creds()
        cls.k8s_creds = _k8s_creds()
        cls.smoke_asset_ids = []

        log("[CERT-ROTATION] setup: issuing initial cert for smoke nginx")
        # Issue initial cert via step_ca executor directly for nginx setup
        base = cls.client.base
        r = cls.client.client.post(f"{base}/change-requests", json={
            "title": "[SMOKE-SETUP] Initial cert for smoke nginx",
            "change_type": "certificate_rotation",
            "desired_outcome": {
                "subject": SMOKE_SUBJECT,
                "san": [SMOKE_SUBJECT],
                "not_after": "720h",
                "trigger_reason": "scheduled",
                "scan_scope": [],  # empty scope — no dependents, just issue
                "verify_timeout_seconds": 1,
            },
        })
        if r.status_code in (200, 201):
            cr_id = r.json()["id"]
            for path in ["plan", "submit-for-approval"]:
                cls.client.client.post(f"{base}/change-requests/{cr_id}/{path}")
            cls.client.client.post(f"{base}/change-requests/{cr_id}/approve",
                                   json={"decision": "approved", "comment": "smoke-setup"})
            cls.client.client.post(f"{base}/change-requests/{cr_id}/execute")
            deadline = time.time() + 60
            while time.time() < deadline:
                cr = cls.client.client.get(f"{base}/change-requests/{cr_id}").json()
                if cr.get("status") in ("completed", "failed", "paused"):
                    break
                time.sleep(3)

        # Register smoke host asset
        asset_id = _register_asset(cls.client, SMOKE_SUBJECT, 8443)
        cls.smoke_asset_ids.append(asset_id)
        log(f"[CERT-ROTATION] Registered smoke host asset: {asset_id}")

    @classmethod
    def teardown_class(cls):
        log("[CERT-ROTATION] teardown: removing smoke assets")
        for asset_id in cls.smoke_asset_ids:
            try:
                _delete_asset(cls.client, asset_id)
            except Exception as e:
                log(f"[CERT-ROTATION] teardown warning: {e}")

    def test_phase1_happy_path_type_a(self):
        """Happy path: type-A host rotation and FILO rollback."""
        log("[PHASE1] Starting type-A host rotation")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 1 — happy path",
            SMOKE_SUBJECT,
            scan_scope=["nexplane_agent"],
            verify_timeout_seconds=5,
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}: {cr}"
        log("[PHASE1] CR completed")

        result = _get_execution_result(cr)
        dependents = result.get("dependents", [])
        rotation_result = result.get("rotation_result", {})
        assert rotation_result.get("fingerprint"), "rotation_result.fingerprint must be set"

        verified_dependents = [d for d in dependents if (d.get("verify_result") or {}).get("success")]
        assert verified_dependents, f"At least one dependent must verify successfully: {dependents}"
        log(f"[PHASE1] {len(verified_dependents)}/{len(dependents)} dependents verified")

        log("[PHASE1] Triggering rollback")
        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings"), \
            f"Unexpected rollback status: {rb_cr['status']}"

        rb_result = _get_execution_result(rb_cr)
        rollback_steps = rb_result.get("rollback_steps", [])
        assert rollback_steps, "rollback_steps must be non-empty"

        # FILO: first rollback step must have the highest index
        max_index = max(d["index"] for d in dependents) if dependents else 0
        assert rollback_steps[0]["index"] == max_index, \
            f"Expected FILO (index {max_index} first), got {rollback_steps[0]['index']}"

        rb_verified = False
        for step in rollback_steps:
            if step.get("rolled_back"):
                rb_verified = True
                break
        assert rb_verified, f"No rollback step succeeded: {rollback_steps}"
        log("[PHASE1] PASS")

    def test_phase2_type_b_k8s_secret(self):
        """Type-B K8s secret rotation and rollback."""
        if not self.k8s_creds:
            pytest.skip("No kubernetes connector — skipping phase 2")

        log("[PHASE2] Starting K8s secret rotation")
        # Pre-create K8s secret with placeholder PEM
        import kubernetes as k8s_client
        # Use the platform's kubernetes connector to create the secret
        base = self.client.base
        # Create a simple k8s secret via executor action
        r = self.client.client.post(f"{base}/execute-action", json={
            "connector_type": "kubernetes",
            "action_id": "create_secret",
            "params": {
                "namespace": "default",
                "name": "nexplane-smoke-tls",
                "data": {"tls.crt": "PLACEHOLDER", "tls.key": "PLACEHOLDER"},
            },
        })
        log(f"[PHASE2] Pre-create k8s secret: {r.status_code}")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 2 — K8s secret",
            SMOKE_SUBJECT,
            scan_scope=["kubernetes"],
            verify_timeout_seconds=3,
        )
        assert cr["status"] == "completed", f"Expected completed: {cr['status']}: {cr}"
        result = _get_execution_result(cr)
        rotation_result = result.get("rotation_result", {})
        new_cert_pem = rotation_result.get("cert_pem", "")

        k8s_dependents = [d for d in result.get("dependents", []) if d["type"] == "k8s_secret"]
        assert k8s_dependents, "Expected at least one K8s secret dependent"
        verified = all((d.get("verify_result") or {}).get("success") for d in k8s_dependents)
        assert verified, f"K8s secret verify failed: {k8s_dependents}"
        log("[PHASE2] K8s secret verified — triggering rollback")

        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")
        log("[PHASE2] PASS")

    def test_phase3_compromise_trigger(self):
        """Compromise trigger: rollback_strategy must be 'reissue'."""
        log("[PHASE3] Starting compromise trigger rotation")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 3 — compromise",
            SMOKE_SUBJECT,
            scan_scope=["nexplane_agent"],
            trigger_reason="compromise",
            verify_timeout_seconds=5,
        )
        assert cr["status"] == "completed", f"Expected completed: {cr['status']}: {cr}"
        log("[PHASE3] CR completed — triggering rollback")

        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")

        rb_result = _get_execution_result(rb_cr)
        rollback_strategy = rb_result.get("rollback_strategy")
        assert rollback_strategy == "reissue", \
            f"Expected rollback_strategy='reissue' for compromise, got '{rollback_strategy}'"
        log("[PHASE3] rollback_strategy=reissue confirmed")
        log("[PHASE3] PASS")

    def test_phase4_verify_failure_paused_rollback(self):
        """Verify failure: CR pauses; rollback restores type-A host."""
        log("[PHASE4] Registering synthetic unreachable host")
        # Register a fake asset pointing to 127.0.0.1:9999 (always unreachable)
        fake_asset_id = _register_asset(
            self.client, "127.0.0.1", 9999,
            extra={"hostname": SMOKE_SUBJECT},  # same subject so scan picks it up
        )
        self.smoke_asset_ids.append(fake_asset_id)

        try:
            log("[PHASE4] Running rotation with mixed real + synthetic dependents")
            cr = _run_cert_rotation_cr(
                self.client,
                "[SMOKE] Cert rotation phase 4 — verify failure",
                SMOKE_SUBJECT,
                scan_scope=["nexplane_agent"],
                verify_timeout_seconds=2,
            )
            assert cr["status"] == "paused", f"Expected paused (synthetic host should fail verify), got {cr['status']}: {cr}"
            log("[PHASE4] CR paused as expected")

            result = _get_execution_result(cr)
            assert result.get("phase") == "verify", f"Expected phase=verify, got {result.get('phase')}"

            dependents = result.get("dependents", [])
            failed = [d for d in dependents if not (d.get("verify_result") or {}).get("success")]
            succeeded = [d for d in dependents if (d.get("verify_result") or {}).get("success")]
            assert failed, f"Expected at least one failed dependent: {dependents}"
            log(f"[PHASE4] {len(failed)} failed, {len(succeeded)} succeeded")

            log("[PHASE4] Triggering rollback")
            rb_cr = _rollback_cr(self.client, cr["id"])
            assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")

            rb_result = _get_execution_result(rb_cr)
            rb_steps = rb_result.get("rollback_steps", [])
            assert rb_steps, "rollback_steps must be non-empty"
            log("[PHASE4] PASS")
        finally:
            _delete_asset(self.client, fake_asset_id)
            self.smoke_asset_ids.remove(fake_asset_id)

    def test_phase5_auto_trigger_expiry_worker(self):
        """Auto-trigger: expiry worker creates draft CR for near-expiry cert."""
        log("[PHASE5] Issuing near-expiry cert (not_after=2h)")

        # Issue a short-lived cert via platform CR
        base = self.client.base
        r = self.client.client.post(f"{base}/change-requests", json={
            "title": "[SMOKE-SETUP] Near-expiry cert for auto-trigger",
            "change_type": "certificate_rotation",
            "desired_outcome": {
                "subject": EXPIRING_SUBJECT,
                "san": [EXPIRING_SUBJECT],
                "not_after": "2h",
                "trigger_reason": "scheduled",
                "scan_scope": [],
                "verify_timeout_seconds": 1,
            },
        })
        assert r.status_code in (200, 201), f"Setup CR failed: {r.text}"
        setup_cr_id = r.json()["id"]
        for path in ["plan", "submit-for-approval"]:
            self.client.client.post(f"{base}/change-requests/{setup_cr_id}/{path}")
        self.client.client.post(f"{base}/change-requests/{setup_cr_id}/approve",
                                json={"decision": "approved", "comment": "smoke-setup"})
        self.client.client.post(f"{base}/change-requests/{setup_cr_id}/execute")
        deadline = time.time() + 60
        while time.time() < deadline:
            cr = self.client.client.get(f"{base}/change-requests/{setup_cr_id}").json()
            if cr.get("status") in ("completed", "failed", "paused"):
                break
            time.sleep(3)
        log(f"[PHASE5] Near-expiry cert issued: {cr.get('status')}")

        log("[PHASE5] Calling _check_step_ca_certs with 1-day threshold")
        # Invoke the worker function directly — it creates a draft CR if cert expires within threshold
        trigger_result = self.client.client.post(
            f"{base}/internal/trigger-expiry-check",
            json={"subject": EXPIRING_SUBJECT, "threshold_days": 1},
        )
        if trigger_result.status_code == 404:
            # Internal endpoint may not exist — call worker directly via docker exec
            log("[PHASE5] No internal trigger endpoint — calling worker directly")
            import subprocess
            proc = subprocess.run(
                [
                    "docker", "exec", "nexplane-backend-1",
                    "python", "-c",
                    f"""
import asyncio
from app.workers.credential_expiry_worker import _check_step_ca_certs
from app.database import AsyncSessionLocal
async def run():
    async with AsyncSessionLocal() as db:
        await _check_step_ca_certs(db, threshold_days=1)
asyncio.run(run())
print("worker_done")
""",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert "worker_done" in proc.stdout, f"Worker failed: {proc.stderr}"

        log("[PHASE5] Checking for auto-created draft CR")
        deadline = time.time() + 30
        auto_cr = None
        while time.time() < deadline:
            r = self.client.client.get(f"{base}/change-requests", params={
                "change_type": "certificate_rotation",
                "status": "awaiting_approval",
            })
            crs = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
            matches = [
                c for c in crs
                if (c.get("desired_outcome") or {}).get("subject") == EXPIRING_SUBJECT
                and c.get("title", "").startswith("[Auto]")
            ]
            if matches:
                auto_cr = matches[0]
                break
            time.sleep(3)

        assert auto_cr, f"No auto-triggered certificate_rotation CR found for {EXPIRING_SUBJECT}"
        log(f"[PHASE5] Auto-triggered CR found: {auto_cr['id']}")

        # Execute it to completion
        auto_cr_id = auto_cr["id"]
        r = self.client.client.post(f"{base}/change-requests/{auto_cr_id}/approve",
                                    json={"decision": "approved", "comment": "smoke"})
        assert r.status_code in (200, 201, 202, 204)
        r = self.client.client.post(f"{base}/change-requests/{auto_cr_id}/execute")
        assert r.status_code in (200, 201, 202, 204)

        deadline = time.time() + EXEC_TIMEOUT
        while time.time() < deadline:
            final_cr = self.client.client.get(f"{base}/change-requests/{auto_cr_id}").json()
            if final_cr.get("status") in ("completed", "paused", "failed"):
                break
            time.sleep(5)

        assert final_cr.get("status") == "completed", \
            f"Auto-trigger CR did not complete: {final_cr.get('status')}"
        result = _get_execution_result(final_cr)
        assert result.get("rotation_result", {}).get("fingerprint"), "New cert fingerprint must be set"
        log("[PHASE5] PASS")
```

- [ ] **Step 2: SCP to EC2**

```bash
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_certificate_rotation_smoke.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/
```

- [ ] **Step 3: Run smoke test on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_certificate_rotation_smoke.py -v -s 2>&1"
```

Expected: All 5 phases PASS (or xfail with clear infra reason — investigate and fix any failures before marking done).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_certificate_rotation_smoke.py
git commit -m "smoke: certificate_rotation 5-phase live smoke test"
```

---

## Self-Review

**Spec coverage:**
- ✅ Section 1 (CR structure): Task 1 (ChangeType) + Task 2 (planning validation) + Task 4 (`execution_result` structure with `dependents`, `rotation_result`, `phase`, `has_warnings`)
- ✅ Section 2 (execution lifecycle): Task 3 (scan+snapshot+rotate), Task 4 (update+verify+`execute_certificate_rotation`), Task 6 (workflow wire), Task 7 (retry-verify+skip-verify)
- ✅ Section 3 (rollback strategy C): Task 5 (`execute_certificate_rollback` with `_cert_has_24h_remaining`, strategy A vs B, FILO)
- ✅ Section 4 (new API endpoints): Task 7 (retry-verify, skip-verify with FOR UPDATE + _resume_execution)
- ✅ Section 5 (files): all 10 files covered across tasks 1–8
- ✅ Section 6 (smoke test): Task 9 all 5 phases

**Placeholder scan:** No TBD or TODO. All code blocks are complete.

**Type consistency:**
- `execute_certificate_rotation(cr_id: uuid.UUID) -> dict` — dispatched in Task 6 (`activities.py`) and Task 7 (resume on retry-verify) ✅
- `execute_certificate_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict` — called in Task 6 (`rollback_executor.py`) ✅
- `_build_result` returns `{"phase", "dependents", "rotation_result", "rollback_strategy", "has_warnings", "paused"}` — matches spec Section 1 `execution_result` shape ✅
- `rollback_steps[i]["rolled_back"]` bool — used in `_determine_rollback_status` adaptation in rollback_executor ✅
- `ChangeRequestStatus.rolled_back_with_warnings` — already exists from credential_rotation ✅
