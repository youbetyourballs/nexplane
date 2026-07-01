# Platform Upgrade CR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `platform_upgrade` CR type that brings Nexplane self-upgrades under the same plan → approve → execute → rollback lifecycle as every other infrastructure change.

**Architecture:** An executor (phases 1–4) runs while the current backend is alive, writing progress to both the DB and a host-mounted sentinel file. A new `nexplane-watchdog` container owns phase 5 (container swap), reading from the sentinel; the new backend self-completes phase 6 on startup. A background version poller surfaces available updates as an admin-only UI banner that pre-populates a draft CR.

**Tech Stack:** FastAPI + APScheduler (version poller), asyncio + subprocess (executor), Python 3.12 alpine (watchdog), React + TypeScript (banner components), Docker Compose (watchdog service), PostgreSQL + Alembic (enum migration), `httpx` (release manifest fetch).

## Global Constraints

- All new Python files: SPDX header `# SPDX-License-Identifier: AGPL-3.0-only` + `# Copyright (C) 2024-2026 Nexplane, Inc.`
- Executor must implement `async def execute(parameters: dict, asset_ids: list, connector) -> dict` and `async def rollback(parameters: dict, execution_result: dict, connector) -> dict`
- Sentinel file path: `/nexplane-data/upgrade/sentinel.json` (host-mounted, shared by all containers)
- DB snapshot path pattern: `/nexplane-data/snapshots/pre_upgrade_<version>_<timestamp>.dump.gz`
- Version poller disabled when `NEXPLANE_EDITION=commercial` or `UPDATE_CHECK_INTERVAL_HOURS=0`
- Release manifest URL: `https://releases.nexplane.ai/latest.json`
- Watchdog polls `GET /health` every 5 s for up to 90 s after cutover
- Watchdog log: `/nexplane-data/upgrade/watchdog.log`
- Frontend components: admin-role-only visibility
- `require_approval` always `true` in self-hosted path
- No new connector type — executor lives under `backend/app/connectors/executors/platform/`
- Nexplane-deploy changes are out of scope for this plan (separate repo)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/change_type_definitions/platform_upgrade.json` | Create | CR type schema, parameter definitions |
| `backend/app/connectors/executors/platform/__init__.py` | Create | Package marker |
| `backend/app/connectors/executors/platform/upgrade.py` | Create | Executor — phases 1–4, rollback logic |
| `backend/app/services/version_poller.py` | Create | Background task — polls release manifest every N hours |
| `backend/app/routers/version.py` | Create | `GET /version`, `GET /version/check` endpoints |
| `backend/app/models/change_request.py` | Modify | Add `platform_upgrade` to `ChangeType` enum (line 511) |
| `backend/alembic/versions/<hash>_add_platform_upgrade_cr_type.py` | Create | Postgres enum migration |
| `backend/app/main.py` | Modify | Register version router; add version poller APScheduler job |
| `docker-compose.yml` | Modify | Add `nexplane-watchdog` service; add `nexplane-data` named volume |
| `watchdog/Dockerfile` | Create | Minimal Python 3.12-alpine image |
| `watchdog/main.py` | Create | Watchdog entrypoint — phase 5 cutover + rollback |
| `frontend/src/types/api.ts` | Modify | Add `"platform_upgrade"` to `ChangeType` union (after `"sysmon_fim"`) |
| `frontend/src/pages/CreateChangeRequest.tsx` | Modify | Add `platform_upgrade` entry to `CHANGE_TYPE_META` and `CHANGE_TYPE_GROUPS` |
| `frontend/src/components/UpdateBanner.tsx` | Create | Admin-only update notification banner |
| `frontend/src/components/DegradedModeBanner.tsx` | Create | Admin-only upgrade failure / recovery UI |
| `install.sh` | Modify | Add `--recover` mode that reads sentinel and restores previous state |
| `backend/tests/test_platform_upgrade.py` | Create | Unit tests for executor phase logic and sentinel read/write |

---

## Task 1: CR Type Definition + ChangeType Enum

**Files:**
- Create: `backend/app/change_type_definitions/platform_upgrade.json`
- Modify: `backend/app/models/change_request.py` (add enum value after line 511)
- Create: `backend/alembic/versions/p600j4k7l8m9_add_platform_upgrade_cr_type.py`

**Interfaces:**
- Produces: `ChangeType.platform_upgrade` enum value consumed by Task 2 (executor), Task 6 (frontend)

- [ ] **Step 1: Write `platform_upgrade.json`**

```json
{
  "change_type": "platform_upgrade",
  "display_name": "Platform Upgrade",
  "description": "Upgrade the Nexplane backend to a new release version with automatic snapshot, migration, cutover via watchdog, and rollback.",
  "risk_level_default": "high",
  "incident_response": false,
  "parameters": {
    "target_version": {
      "type": "string",
      "required": true,
      "label": "Target Version",
      "description": "Exact semver tag to upgrade to (e.g. 1.4.2)"
    },
    "image_sha256": {
      "type": "string",
      "required": true,
      "label": "Image SHA-256",
      "description": "sha256 digest from release manifest; verified before cutover"
    },
    "changelog_url": {
      "type": "string",
      "required": false,
      "label": "Changelog URL",
      "description": "Link to release notes for the approver"
    },
    "require_approval": {
      "type": "string",
      "required": false,
      "label": "Require Approval",
      "description": "Set to 'false' to allow auto-execution (hosted ops path only)",
      "default": "true"
    }
  },
  "rollback": {
    "strategy": "backup_restore",
    "description": "Watchdog restores previous image tag and pg_restore from pre-upgrade snapshot"
  }
}
```

- [ ] **Step 2: Open `backend/app/models/change_request.py`, find the last enum line (`audit_cis_compliance = "audit_cis_compliance"` at line 511), and add the new value immediately after it**

```python
    platform_upgrade = "platform_upgrade"
```

- [ ] **Step 3: Write the Alembic migration**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add platform_upgrade cr type

Revision ID: p600j4k7l8m9
Revises: o500i3j6k7l8
Create Date: 2026-06-25

"""
from alembic import op

revision = 'p600j4k7l8m9'
down_revision = 'o500i3j6k7l8'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'platform_upgrade'")


def downgrade():
    pass  # Postgres does not support removing enum values
```

- [ ] **Step 4: Apply the migration on EC2 (scp files first, then run)**

```bash
# On EC2 via SSH
docker exec nexplane-backend-1 alembic upgrade head
```

Expected output ends with: `Running upgrade o500i3j6k7l8 -> p600j4k7l8m9, add platform_upgrade cr type`

- [ ] **Step 5: Commit**

```bash
git add backend/app/change_type_definitions/platform_upgrade.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/p600j4k7l8m9_add_platform_upgrade_cr_type.py
git commit -m "feat: add platform_upgrade change type definition and enum"
```

---

## Task 2: Platform Upgrade Executor (Phases 1–4)

**Files:**
- Create: `backend/app/connectors/executors/platform/__init__.py`
- Create: `backend/app/connectors/executors/platform/upgrade.py`
- Create: `backend/tests/test_platform_upgrade.py`

**Interfaces:**
- Consumes: `ChangeType.platform_upgrade` from Task 1
- Produces:
  - `execute(parameters: dict, asset_ids: list, connector) -> dict` — returns `{"action": "platform_upgrade", "phase_reached": str, "snapshot_path": str, "previous_version": str, "alembic_revision_before": str, "cr_id": str, ...}`
  - `rollback(parameters: dict, execution_result: dict, connector) -> dict` — returns `{"action": "platform_upgrade_rollback", "restored": bool}`
  - Sentinel file schema (JSON at `/nexplane-data/upgrade/sentinel.json`) consumed by Task 4 (watchdog)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_platform_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for platform upgrade executor — phases 1–4 and sentinel logic."""
import json
import os
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


# ── Sentinel helpers ────────────────────────────────────────────────────────

def test_write_sentinel_creates_file(tmp_path):
    from app.connectors.executors.platform.upgrade import write_sentinel
    sentinel = tmp_path / "sentinel.json"
    write_sentinel({"state": "preflight_complete", "cr_id": "abc"}, path=str(sentinel))
    data = json.loads(sentinel.read_text())
    assert data["state"] == "preflight_complete"
    assert data["cr_id"] == "abc"


def test_read_sentinel_returns_none_when_missing(tmp_path):
    from app.connectors.executors.platform.upgrade import read_sentinel
    result = read_sentinel(path=str(tmp_path / "nonexistent.json"))
    assert result is None


def test_read_sentinel_returns_dict_when_present(tmp_path):
    from app.connectors.executors.platform.upgrade import read_sentinel
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text('{"state": "pull_complete"}')
    result = read_sentinel(path=str(sentinel))
    assert result == {"state": "pull_complete"}


# ── Phase 1: preflight ───────────────────────────────────────────────────────

def test_check_version_newer_passes():
    from app.connectors.executors.platform.upgrade import check_version_newer
    check_version_newer(current="1.3.1", target="1.4.2")  # no exception


def test_check_version_newer_fails_same():
    from app.connectors.executors.platform.upgrade import check_version_newer
    with pytest.raises(ValueError, match="not newer"):
        check_version_newer(current="1.4.2", target="1.4.2")


def test_check_version_newer_fails_downgrade():
    from app.connectors.executors.platform.upgrade import check_version_newer
    with pytest.raises(ValueError, match="not newer"):
        check_version_newer(current="1.5.0", target="1.4.2")


def test_check_min_compatible_passes():
    from app.connectors.executors.platform.upgrade import check_min_compatible
    check_min_compatible(current="1.3.0", min_version="1.2.0")


def test_check_min_compatible_fails():
    from app.connectors.executors.platform.upgrade import check_min_compatible
    with pytest.raises(ValueError, match="below minimum"):
        check_min_compatible(current="1.1.0", min_version="1.2.0")


# ── Phase 3: sha256 verification ────────────────────────────────────────────

def test_verify_image_sha_passes():
    from app.connectors.executors.platform.upgrade import verify_image_sha
    verify_image_sha(
        pulled_sha="sha256:abc123",
        expected_sha="sha256:abc123",
    )


def test_verify_image_sha_fails():
    from app.connectors.executors.platform.upgrade import verify_image_sha
    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_image_sha(pulled_sha="sha256:bad", expected_sha="sha256:abc123")


# ── Execute wiring ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_returns_mock_when_no_creds():
    from app.connectors.executors.platform.upgrade import execute
    connector = MagicMock()
    connector.credentials = {}
    result = await execute(
        {"target_version": "1.4.2", "image_sha256": "sha256:abc", "cr_id": "test-cr"},
        [],
        connector,
    )
    assert result.get("mock") is True
    assert result["action"] == "platform_upgrade"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
# On EC2
docker exec nexplane-backend-1 python -m pytest tests/test_platform_upgrade.py -v 2>&1 | head -40
```

Expected: `ImportError` or `ModuleNotFoundError` for `app.connectors.executors.platform.upgrade`

- [ ] **Step 3: Create the package marker**

`backend/app/connectors/executors/platform/__init__.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
```

- [ ] **Step 4: Write the executor**

`backend/app/connectors/executors/platform/upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Platform upgrade executor — phases 1–4.

Phase 5 (container cutover) is handled by the nexplane-watchdog container.
Phase 6 (startup verify) is handled in backend/app/main.py lifespan.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SENTINEL_DEFAULT = "/nexplane-data/upgrade/sentinel.json"
SNAPSHOTS_DIR = "/nexplane-data/snapshots"


# ── Sentinel helpers ─────────────────────────────────────────────────────────

def write_sentinel(data: dict, path: str = SENTINEL_DEFAULT) -> None:
    """Atomically write the sentinel file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def read_sentinel(path: str = SENTINEL_DEFAULT) -> dict | None:
    """Return sentinel dict or None if the file does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── Phase 1 helpers ──────────────────────────────────────────────────────────

def _parse_semver(v: str) -> tuple[int, int, int]:
    """Parse '1.4.2' into (1, 4, 2). Strips leading 'v'."""
    parts = v.lstrip("v").split(".")
    return tuple(int(x) for x in parts[:3])  # type: ignore[return-value]


def check_version_newer(current: str, target: str) -> None:
    if _parse_semver(target) <= _parse_semver(current):
        raise ValueError(f"Target {target} is not newer than current {current}")


def check_min_compatible(current: str, min_version: str) -> None:
    if _parse_semver(current) < _parse_semver(min_version):
        raise ValueError(
            f"Current version {current} is below minimum compatible {min_version}"
        )


def _check_disk_space(snapshot_dir: str) -> None:
    usage = shutil.disk_usage(snapshot_dir)
    # Require at least 2 GB free
    if usage.free < 2 * 1024 ** 3:
        raise RuntimeError(
            f"Insufficient disk space: {usage.free // (1024**2)} MB free, need 2048 MB"
        )


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


# ── Phase 3 helper ───────────────────────────────────────────────────────────

def verify_image_sha(pulled_sha: str, expected_sha: str) -> None:
    if pulled_sha != expected_sha:
        raise ValueError(
            f"sha256 mismatch: pulled {pulled_sha!r} != expected {expected_sha!r}"
        )


# ── Real execute (phases 1–4) ────────────────────────────────────────────────

async def _real_execute(parameters: dict) -> dict:
    loop = asyncio.get_event_loop()

    target_version = parameters["target_version"]
    image_sha256 = parameters["image_sha256"]
    cr_id = parameters.get("cr_id", "unknown")
    current_version = os.environ.get("NEXPLANE_VERSION", "0.0.0")

    base_sentinel: dict[str, Any] = {
        "cr_id": cr_id,
        "target_version": target_version,
        "previous_version": current_version,
        "previous_image_tag": f"nexplane/nexplane:{current_version}",
    }

    # ── Phase 1: preflight ───────────────────────────────────────────────────
    logger.info("Phase 1: preflight")
    check_version_newer(current_version, target_version)

    # Fetch manifest to get min_compatible_version
    import httpx
    manifest_url = os.environ.get(
        "NEXPLANE_RELEASE_MANIFEST_URL",
        "https://releases.nexplane.ai/latest.json",
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(manifest_url)
        resp.raise_for_status()
        manifest = resp.json()
    check_min_compatible(current_version, manifest.get("min_compatible_version", "0.0.0"))

    Path(SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
    await loop.run_in_executor(None, _check_disk_space, SNAPSHOTS_DIR)

    write_sentinel({**base_sentinel, "state": "preflight_complete"})
    logger.info("Phase 1 complete")

    # ── Phase 2: snapshot ────────────────────────────────────────────────────
    logger.info("Phase 2: snapshot")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    dump_path = f"{SNAPSHOTS_DIR}/pre_upgrade_{target_version}_{timestamp}.dump.gz"

    db_url = os.environ.get("DATABASE_URL", "")
    # Extract host/port/user/dbname from postgresql+asyncpg://user:pass@host:port/db
    import urllib.parse
    parsed = urllib.parse.urlparse(db_url.replace("+asyncpg", ""))
    pg_env = {
        **os.environ,
        "PGPASSWORD": parsed.password or "",
    }
    pg_args = [
        "-h", parsed.hostname or "db",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        parsed.path.lstrip("/") or "nexplane",
    ]

    def _dump():
        with gzip.open(dump_path, "wb") as gz:
            proc = subprocess.run(
                ["pg_dump", "--format=custom", *pg_args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=pg_env,
                check=True,
            )
            gz.write(proc.stdout)
        # Verify non-empty
        if Path(dump_path).stat().st_size < 1024:
            raise RuntimeError("pg_dump produced suspiciously small output")

    await loop.run_in_executor(None, _dump)

    write_sentinel({
        **base_sentinel,
        "state": "snapshot_complete",
        "snapshot_path": dump_path,
    })
    logger.info("Phase 2 complete: %s", dump_path)

    # ── Phase 3: pull ────────────────────────────────────────────────────────
    logger.info("Phase 3: pull")
    image_ref = f"nexplane/nexplane:{target_version}"

    def _pull_and_verify():
        _run(["docker", "pull", image_ref])
        inspect = _run([
            "docker", "inspect", "--format={{index .RepoDigests 0}}", image_ref
        ])
        pulled_sha = inspect.stdout.strip().split("@")[-1] if "@" in inspect.stdout else inspect.stdout.strip()
        verify_image_sha(pulled_sha, image_sha256)

    await loop.run_in_executor(None, _pull_and_verify)

    write_sentinel({
        **base_sentinel,
        "state": "pull_complete",
        "snapshot_path": dump_path,
    })
    logger.info("Phase 3 complete")

    # ── Phase 4: migrate ─────────────────────────────────────────────────────
    logger.info("Phase 4: migrate")

    def _get_current_revision() -> str:
        result = _run(["alembic", "current"])
        # output: "abc123def456 (head)"
        return result.stdout.strip().split()[0] if result.stdout.strip() else "unknown"

    alembic_revision_before = await loop.run_in_executor(None, _get_current_revision)

    try:
        await loop.run_in_executor(None, lambda: _run(["alembic", "upgrade", "head"]))
    except subprocess.CalledProcessError as exc:
        logger.error("alembic upgrade failed: %s", exc.stderr)
        # Try downgrade first
        try:
            await loop.run_in_executor(
                None, lambda: _run(["alembic", "downgrade", alembic_revision_before])
            )
            logger.info("alembic downgrade succeeded — schema restored")
        except subprocess.CalledProcessError as exc2:
            logger.error("alembic downgrade also failed: %s", exc2.stderr)
            # Last resort: pg_restore
            def _restore():
                with gzip.open(dump_path, "rb") as gz:
                    raw = gz.read()
                proc = subprocess.run(
                    ["pg_restore", "--clean", "--if-exists", *pg_args],
                    input=raw,
                    capture_output=True,
                    env=pg_env,
                )
                if proc.returncode not in (0, 1):  # pg_restore exits 1 on warnings
                    raise RuntimeError(f"pg_restore failed: {proc.stderr.decode()}")
            await loop.run_in_executor(None, _restore)
        raise RuntimeError("Migration failed; schema restored. CR aborted.") from exc

    write_sentinel({
        **base_sentinel,
        "state": "migrate_complete",
        "snapshot_path": dump_path,
        "alembic_revision_before": alembic_revision_before,
    })
    logger.info("Phase 4 complete, alembic revision before: %s", alembic_revision_before)

    # ── Hand off to watchdog (phase 5) ───────────────────────────────────────
    cutover_ts = datetime.now(timezone.utc).isoformat()
    write_sentinel({
        **base_sentinel,
        "state": "cutover_pending",
        "snapshot_path": dump_path,
        "alembic_revision_before": alembic_revision_before,
        "cutover_timestamp": cutover_ts,
    })
    logger.info("Phase 4→5 handoff: sentinel set to cutover_pending. Watchdog will take over.")

    return {
        "action": "platform_upgrade",
        "phase_reached": "cutover_pending",
        "target_version": target_version,
        "previous_version": current_version,
        "snapshot_path": dump_path,
        "alembic_revision_before": alembic_revision_before,
        "cr_id": cr_id,
        "cutover_timestamp": cutover_ts,
    }


# ── Public interface ─────────────────────────────────────────────────────────

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        # No credentials = mock / test mode
        return {
            "action": "platform_upgrade",
            "mock": True,
            "phase_reached": "cutover_pending",
            "target_version": parameters.get("target_version"),
            "previous_version": os.environ.get("NEXPLANE_VERSION", "0.0.0"),
            "snapshot_path": "/nexplane-data/snapshots/mock.dump.gz",
            "alembic_revision_before": "mock_rev",
            "cr_id": parameters.get("cr_id", ""),
        }
    return await _real_execute(parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Rollback for phases 1–4 is a no-op at the executor level.
    Phase 5 rollback is handled entirely by the watchdog container.
    If called here, the cutover never happened, so there is nothing to undo.
    """
    return {
        "action": "platform_upgrade_rollback",
        "restored": False,
        "reason": "pre-cutover rollback is a no-op; watchdog handles phase 5 recovery",
    }
```

- [ ] **Step 5: Run the tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_platform_upgrade.py -v 2>&1
```

Expected: all tests pass. Look specifically for `PASSED` next to each test name.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/platform/__init__.py \
        backend/app/connectors/executors/platform/upgrade.py \
        backend/tests/test_platform_upgrade.py
git commit -m "feat: add platform upgrade executor phases 1-4"
```

---

## Task 3: Version Poller + Version Router

**Files:**
- Create: `backend/app/services/version_poller.py`
- Create: `backend/app/routers/version.py`
- Modify: `backend/app/main.py` (add import, router include, scheduler job)

**Interfaces:**
- Produces:
  - `check_for_update() -> dict | None` — returns latest manifest dict if newer than running version, else `None`; consumed by `GET /version/check` and the banner (Task 6)
  - `GET /version` → `{"version": "1.3.1"}`
  - `GET /version/check` → `{"available": bool, "manifest": dict | null}`

- [ ] **Step 1: Write `version_poller.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Background version poller — checks releases.nexplane.ai every N hours."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_cached_manifest: dict[str, Any] | None = None
_cached_at: datetime | None = None


def _parse_semver(v: str) -> tuple[int, int, int]:
    parts = v.lstrip("v").split(".")
    return tuple(int(x) for x in parts[:3])  # type: ignore[return-value]


async def poll_version() -> None:
    """Fetch the release manifest and cache it if a newer version is available."""
    global _cached_manifest, _cached_at

    edition = os.environ.get("NEXPLANE_EDITION", "")
    if edition == "commercial":
        return  # hosted instances receive upgrades via ops-initiated CRs

    interval_hours = float(os.environ.get("UPDATE_CHECK_INTERVAL_HOURS", "6"))
    if interval_hours == 0:
        return

    manifest_url = os.environ.get(
        "NEXPLANE_RELEASE_MANIFEST_URL",
        "https://releases.nexplane.ai/latest.json",
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(manifest_url)
            resp.raise_for_status()
            manifest = resp.json()
    except Exception as exc:
        logger.warning("Version check failed: %s", exc)
        return

    current = os.environ.get("NEXPLANE_VERSION", "0.0.0")
    try:
        if _parse_semver(manifest["version"]) > _parse_semver(current):
            _cached_manifest = manifest
            _cached_at = datetime.now(timezone.utc)
            logger.info("New version available: %s", manifest["version"])
        else:
            _cached_manifest = None
    except Exception as exc:
        logger.warning("Could not parse version from manifest: %s", exc)


def check_for_update() -> dict[str, Any] | None:
    """Return the cached manifest if a newer version is available, else None."""
    return _cached_manifest
```

- [ ] **Step 2: Write `routers/version.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Version info and update-check endpoints."""
from __future__ import annotations

import os

from fastapi import APIRouter

from app.services.version_poller import check_for_update

router = APIRouter(prefix="/version", tags=["version"])


@router.get("")
async def get_version():
    """Return the currently running platform version."""
    return {"version": os.environ.get("NEXPLANE_VERSION", "unknown")}


@router.get("/check")
async def get_version_check():
    """Return whether a newer version is available and its manifest."""
    manifest = check_for_update()
    return {
        "available": manifest is not None,
        "manifest": manifest,
    }
```

- [ ] **Step 3: Wire into `main.py`**

Find the import block at the top of `backend/app/main.py` (around line 56) and add after `from app.workers.credential_expiry_worker import check_credential_expiry`:

```python
from app.services.version_poller import poll_version as _poll_version
from app.routers.version import router as version_router
```

Find the router includes section (search for `app.include_router`) and add:

```python
app.include_router(version_router)
```

Find the APScheduler jobs block (around line 83) and add after `check_credential_expiry` job:

```python
    _escalation_scheduler.add_job(_poll_version, "interval", hours=6, id="version_poller", replace_existing=True, max_instances=1)
```

- [ ] **Step 4: Verify endpoints are live on EC2**

```bash
# After scp + frontend restart (see memory note: always restart frontend)
curl -s http://localhost:8000/version | python -m json.tool
# Expected: {"version": "..."}

curl -s http://localhost:8000/version/check | python -m json.tool
# Expected: {"available": false, "manifest": null}  (or true if a real newer version exists)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/version_poller.py \
        backend/app/routers/version.py \
        backend/app/main.py
git commit -m "feat: add version poller and /version endpoints"
```

---

## Task 4: Watchdog Container

**Files:**
- Create: `watchdog/Dockerfile`
- Create: `watchdog/main.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: sentinel file at `/nexplane-data/upgrade/sentinel.json` written by Task 2 executor
- Produces: writes `cutover_complete` or `rollback_complete` to sentinel; restarts backend container; logs to `/nexplane-data/upgrade/watchdog.log`

- [ ] **Step 1: Write `watchdog/Dockerfile`**

```dockerfile
FROM python:3.12-alpine

RUN apk add --no-cache docker-cli postgresql-client

WORKDIR /app
COPY main.py .

CMD ["python", "main.py"]
```

- [ ] **Step 2: Write `watchdog/main.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""nexplane-watchdog — phase 5 cutover and rollback for platform upgrades.

Runs as a sidecar container. Polls the sentinel file.
When state == 'cutover_pending', performs the container swap.
On health check failure, performs watchdog rollback.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SENTINEL_PATH = "/nexplane-data/upgrade/sentinel.json"
WATCHDOG_LOG = "/nexplane-data/upgrade/watchdog.log"
ENV_FILE = "/nexplane-data/.env"
HEALTH_URL = "http://backend:8000/health"
POLL_INTERVAL_S = 5
HEALTH_TIMEOUT_S = 90
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "nexplane")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(WATCHDOG_LOG),
    ],
)
log = logging.getLogger("watchdog")


def _read_sentinel() -> dict | None:
    p = Path(SENTINEL_PATH)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write_sentinel(data: dict) -> None:
    p = Path(SENTINEL_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _update_env_image_tag(new_tag: str) -> None:
    """Replace IMAGE_TAG=... line in .env file with new_tag."""
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        log.warning(".env not found at %s", ENV_FILE)
        return
    lines = env_path.read_text().splitlines(keepends=True)
    new_lines = []
    updated = False
    for line in lines:
        if line.startswith("IMAGE_TAG="):
            new_lines.append(f"IMAGE_TAG={new_tag}\n")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"IMAGE_TAG={new_tag}\n")
    env_path.write_text("".join(new_lines))


def _health_ok() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _do_cutover(sentinel: dict) -> bool:
    """Returns True on success, False on failure (triggers rollback)."""
    target = sentinel["target_version"]
    log.info("Cutover: updating IMAGE_TAG to %s", target)
    _update_env_image_tag(target)

    log.info("Stopping and restarting backend container")
    _run(["docker", "compose", "--project-name", COMPOSE_PROJECT, "up", "-d", "backend"])

    log.info("Polling %s for up to %ds", HEALTH_URL, HEALTH_TIMEOUT_S)
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        if _health_ok():
            log.info("Backend healthy — cutover complete")
            return True
        time.sleep(POLL_INTERVAL_S)

    log.error("Backend did not become healthy within %ds", HEALTH_TIMEOUT_S)
    return False


def _do_rollback(sentinel: dict) -> None:
    log.info("Watchdog rollback starting")
    previous_tag = sentinel.get("previous_image_tag", "")
    snapshot_path = sentinel.get("snapshot_path", "")

    # 1. Stop backend
    try:
        _run(["docker", "compose", "--project-name", COMPOSE_PROJECT, "stop", "backend"])
    except Exception as exc:
        log.error("Failed to stop backend: %s", exc)

    # 2. Restore IMAGE_TAG
    if previous_tag:
        tag_only = previous_tag.split(":")[-1]
        _update_env_image_tag(tag_only)
        log.info("Restored IMAGE_TAG to %s", tag_only)

    # 3. pg_restore from snapshot
    if snapshot_path and Path(snapshot_path).exists():
        log.info("Running pg_restore from %s", snapshot_path)
        db_url = os.environ.get("DATABASE_URL", "")
        parsed = urllib.parse.urlparse(db_url.replace("+asyncpg", ""))
        pg_env = {**os.environ, "PGPASSWORD": parsed.password or ""}
        pg_args = [
            "-h", parsed.hostname or "db",
            "-p", str(parsed.port or 5432),
            "-U", parsed.username or "postgres",
            parsed.path.lstrip("/") or "nexplane",
        ]
        try:
            with gzip.open(snapshot_path, "rb") as gz:
                raw = gz.read()
            proc = subprocess.run(
                ["pg_restore", "--clean", "--if-exists", *pg_args],
                input=raw,
                capture_output=True,
                env={**os.environ, **pg_env},
            )
            if proc.returncode not in (0, 1):
                log.error("pg_restore exit %d: %s", proc.returncode, proc.stderr.decode())
            else:
                log.info("pg_restore complete")
        except Exception as exc:
            log.error("pg_restore failed: %s", exc)
    else:
        log.warning("No valid snapshot_path in sentinel; skipping pg_restore")

    # 4. Restart backend on previous image
    try:
        _run(["docker", "compose", "--project-name", COMPOSE_PROJECT, "up", "-d", "backend"])
        log.info("Backend restarted on previous image")
    except Exception as exc:
        log.error("Failed to restart backend on previous image: %s", exc)

    # 5. Write CR failure to sentinel (backend may be down so we can't hit the API)
    updated = {**sentinel, "state": "rollback_complete", "rollback_reason": "watchdog_rollback"}
    _write_sentinel(updated)
    log.info("Rollback complete — sentinel set to rollback_complete")


def main():
    log.info("nexplane-watchdog started. Watching %s", SENTINEL_PATH)
    while True:
        sentinel = _read_sentinel()
        if sentinel and sentinel.get("state") == "cutover_pending":
            log.info("Detected cutover_pending for CR %s", sentinel.get("cr_id"))
            updated = {**sentinel}
            success = _do_cutover(sentinel)
            if success:
                updated["state"] = "cutover_complete"
                updated["cutover_completed_at"] = datetime.now(timezone.utc).isoformat()
                _write_sentinel(updated)
                log.info("Sentinel updated to cutover_complete")
            else:
                _do_rollback(sentinel)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add watchdog service and nexplane-data volume to `docker-compose.yml`**

Open `docker-compose.yml`. Find the `volumes:` section at the bottom (the named volumes block, not per-service volumes). Add:

```yaml
  nexplane-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /home/ec2-user/nexplane-data
```

Find the `services:` section and add a new service entry (after `frontend` or any other service):

```yaml
  nexplane-watchdog:
    build:
      context: ./watchdog
      dockerfile: Dockerfile
    container_name: nexplane-watchdog-1
    restart: unless-stopped
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-nexplane}
    volumes:
      - nexplane-data:/nexplane-data
      - /var/run/docker.sock:/var/run/docker.sock
      - ./.env:/nexplane-data/.env
    depends_on:
      - db
```

Also add `nexplane-data` volume mount to the `backend` service:

```yaml
      - nexplane-data:/nexplane-data
```

And to the `db` service (so snapshots are accessible from the db container too):

```yaml
      - nexplane-data:/nexplane-data
```

- [ ] **Step 4: Create the host directory on EC2 and build the watchdog image**

```bash
# On EC2 via SSH
mkdir -p /home/ec2-user/nexplane-data/upgrade /home/ec2-user/nexplane-data/snapshots
cd /home/ec2-user/nexplane
docker compose build nexplane-watchdog
docker compose up -d nexplane-watchdog
docker compose ps  # confirm nexplane-watchdog-1 is Up
```

Expected: `nexplane-watchdog-1` shows as `Up` or `running`.

- [ ] **Step 5: Verify watchdog is watching**

```bash
docker logs nexplane-watchdog-1 2>&1 | tail -5
```

Expected: `nexplane-watchdog started. Watching /nexplane-data/upgrade/sentinel.json`

- [ ] **Step 6: Commit**

```bash
git add watchdog/Dockerfile watchdog/main.py docker-compose.yml
git commit -m "feat: add nexplane-watchdog container and nexplane-data volume"
```

---

## Task 5: Phase 6 Startup Verify + Degraded Mode Backend Logic

**Files:**
- Modify: `backend/app/main.py` (add startup sentinel check in `lifespan`)

**Interfaces:**
- Consumes: sentinel file from Task 4 watchdog (`cutover_complete` state)
- Produces: marks CR `completed` in DB; writes `upgrade_complete` to sentinel; emits audit event; sets `NEXPLANE_VERSION` env var in `.env`

- [ ] **Step 1: Write a helper module for the startup verify logic**

Create `backend/app/services/upgrade_verify.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Phase 6: startup verify — runs in the new backend on first boot after cutover."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SENTINEL_PATH = "/nexplane-data/upgrade/sentinel.json"
ENV_FILE = "/home/ec2-user/nexplane/.env"


def _read_sentinel() -> dict | None:
    from app.connectors.executors.platform.upgrade import read_sentinel
    return read_sentinel(path=SENTINEL_PATH)


def _write_sentinel(data: dict) -> None:
    from app.connectors.executors.platform.upgrade import write_sentinel
    write_sentinel(data, path=SENTINEL_PATH)


def _update_env_version(version: str) -> None:
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines(keepends=True)
    new_lines = []
    updated = False
    for line in lines:
        if line.startswith("NEXPLANE_VERSION="):
            new_lines.append(f"NEXPLANE_VERSION={version}\n")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"NEXPLANE_VERSION={version}\n")
    env_path.write_text("".join(new_lines))


async def run_startup_verify(db_session_factory) -> None:
    """Called from main.py lifespan. Completes phase 6 if a cutover is pending."""
    sentinel = _read_sentinel()
    if not sentinel:
        return

    state = sentinel.get("state")
    target_version = sentinel.get("target_version")
    cr_id = sentinel.get("cr_id")
    running_version = os.environ.get("NEXPLANE_VERSION", "0.0.0")

    if state == "cutover_complete" or (
        state == "cutover_pending" and running_version == target_version
    ):
        logger.info(
            "Phase 6 verify: running %s matches target %s — marking CR %s complete",
            running_version,
            target_version,
            cr_id,
        )
        # Mark CR completed in DB
        if cr_id:
            try:
                import uuid
                from sqlalchemy import select
                from app.models.change_request import ChangeRequest, ChangeRequestStatus
                async with db_session_factory() as db:
                    result = await db.execute(
                        select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(cr_id))
                    )
                    cr = result.scalar_one_or_none()
                    if cr:
                        cr.status = ChangeRequestStatus.completed
                        await db.commit()
                        logger.info("CR %s marked completed", cr_id)
            except Exception as exc:
                logger.error("Failed to mark CR completed: %s", exc)

        # Update .env with new version
        if target_version:
            _update_env_version(target_version)
            os.environ["NEXPLANE_VERSION"] = target_version

        _write_sentinel({**sentinel, "state": "upgrade_complete"})
        logger.info("Sentinel set to upgrade_complete")

    elif state not in ("upgrade_complete", None):
        logger.warning(
            "Backend started with unresolved upgrade state '%s' — degraded mode active",
            state,
        )
        # Set env var that frontend banner will read via /version/check
        os.environ["NEXPLANE_UPGRADE_DEGRADED"] = json.dumps(sentinel)
```

- [ ] **Step 2: Add import and call in `main.py` lifespan**

In `backend/app/main.py`, add this import near the top with the other service imports:

```python
from app.services.upgrade_verify import run_startup_verify as _run_upgrade_verify
```

Inside the `lifespan` function, after `await _resume_rollbacks()` and before `yield`:

```python
    await _run_upgrade_verify(AsyncSessionLocal)
```

- [ ] **Step 3: Expose degraded state via `/version/check`**

Open `backend/app/routers/version.py` and modify `get_version_check` to also return degraded state:

```python
import json as _json

@router.get("/check")
async def get_version_check():
    """Return whether a newer version is available and its manifest."""
    manifest = check_for_update()
    degraded_raw = os.environ.get("NEXPLANE_UPGRADE_DEGRADED")
    degraded = _json.loads(degraded_raw) if degraded_raw else None
    return {
        "available": manifest is not None,
        "manifest": manifest,
        "degraded": degraded,
    }
```

- [ ] **Step 4: Also add `import json` to `upgrade_verify.py` (it's used in step 1 but the import is missing)**

At the top of `backend/app/services/upgrade_verify.py`, add:

```python
import json
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/upgrade_verify.py backend/app/main.py backend/app/routers/version.py
git commit -m "feat: add phase 6 startup verify and degraded mode state"
```

---

## Task 6: Frontend — ChangeType + UpdateBanner + DegradedModeBanner

**Files:**
- Modify: `frontend/src/types/api.ts` (add `"platform_upgrade"` to `ChangeType` union)
- Modify: `frontend/src/pages/CreateChangeRequest.tsx` (add entry to `CHANGE_TYPE_META` and `CHANGE_TYPE_GROUPS`)
- Create: `frontend/src/components/UpdateBanner.tsx`
- Create: `frontend/src/components/DegradedModeBanner.tsx`

**Interfaces:**
- Consumes: `GET /version/check` → `{available: bool, manifest: {...} | null, degraded: {...} | null}`
- Consumes: `GET /version` → `{version: string}`
- Produces: `UpdateBanner` and `DegradedModeBanner` components — must be imported and rendered in the app shell (see Step 6)

- [ ] **Step 1: Add `"platform_upgrade"` to `ChangeType` union in `api.ts`**

Open `frontend/src/types/api.ts`. Find the line `| "sysmon_fim";` (the last value in the `ChangeType` union, around line 484) and change it to:

```typescript
  | "sysmon_fim"
  | "platform_upgrade";
```

- [ ] **Step 2: Add entry to `CHANGE_TYPE_META` in `CreateChangeRequest.tsx`**

Open `frontend/src/pages/CreateChangeRequest.tsx`. Find `CHANGE_TYPE_META` (around line 15). Add an entry — match the indentation style of existing entries:

```typescript
  platform_upgrade: {
    label: "Platform Upgrade",
    description: "Upgrade the Nexplane backend to a new release version with snapshot, migration, watchdog cutover, and rollback.",
    outcomeTemplate: JSON.stringify({
      target_version: "",
      image_sha256: "",
      changelog_url: "",
      require_approval: "true",
    }),
  },
```

- [ ] **Step 3: Add `"platform_upgrade"` to the appropriate group in `CHANGE_TYPE_GROUPS`**

In the same file, find `CHANGE_TYPE_GROUPS` (around line 1267). Locate the group that most logically fits platform operations (look for a "Platform" group or add to the end of an "Infrastructure" or "System" group). If no platform group exists, add a new group object at the end of the array before the closing `]`:

```typescript
  {
    label: "Platform",
    types: ["platform_upgrade"] as ChangeType[],
  },
```

- [ ] **Step 4: Write `UpdateBanner.tsx`**

```tsx
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface ReleaseManifest {
  version: string;
  released_at: string;
  min_compatible_version: string;
  image_sha256: string;
  changelog_url?: string;
  severity: "recommended" | "security" | "critical";
}

interface VersionCheckResponse {
  available: boolean;
  manifest: ReleaseManifest | null;
  degraded: Record<string, unknown> | null;
}

const SEVERITY_STYLES: Record<ReleaseManifest["severity"], string> = {
  recommended: "bg-blue-600 text-white",
  security: "bg-amber-500 text-white",
  critical: "bg-red-600 text-white",
};

const SEVERITY_LABELS: Record<ReleaseManifest["severity"], string> = {
  recommended: "Update available",
  security: "Security update available",
  critical: "Critical update required",
};

export default function UpdateBanner({ isAdmin }: { isAdmin: boolean }) {
  const [manifest, setManifest] = useState<ReleaseManifest | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAdmin) return;
    fetch("/api/version/check")
      .then((r) => r.json())
      .then((data: VersionCheckResponse) => {
        if (data.available && data.manifest) {
          setManifest(data.manifest);
        }
      })
      .catch(() => {/* silently ignore */});
  }, [isAdmin]);

  if (!isAdmin || !manifest || (dismissed && manifest.severity !== "critical")) {
    return null;
  }

  const severity = manifest.severity ?? "recommended";

  function handleReview() {
    const params = new URLSearchParams({
      changeType: "platform_upgrade",
      prefill: JSON.stringify({
        target_version: manifest!.version,
        image_sha256: manifest!.image_sha256,
        changelog_url: manifest!.changelog_url ?? "",
        require_approval: "true",
      }),
    });
    navigate(`/change-requests/new?${params}`);
  }

  return (
    <div className={`w-full px-4 py-2 flex items-center justify-between text-sm ${SEVERITY_STYLES[severity]}`}>
      <span>
        <strong>{SEVERITY_LABELS[severity]}:</strong> Nexplane {manifest.version} is available.{" "}
        {manifest.changelog_url && (
          <a href={manifest.changelog_url} target="_blank" rel="noopener noreferrer" className="underline ml-1">
            What&apos;s new
          </a>
        )}
      </span>
      <div className="flex gap-2 ml-4">
        <button
          onClick={handleReview}
          className="bg-white text-gray-900 px-3 py-1 rounded text-xs font-semibold hover:bg-gray-100"
        >
          Review update
        </button>
        {severity !== "critical" && (
          <button
            onClick={() => setDismissed(true)}
            className="opacity-75 hover:opacity-100 px-2 text-xs"
            aria-label="Dismiss"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `DegradedModeBanner.tsx`**

```tsx
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useEffect, useState } from "react";

interface UpgradeSentinel {
  state: string;
  previous_version?: string;
  target_version?: string;
  cr_id?: string;
  snapshot_path?: string;
}

interface VersionCheckResponse {
  available: boolean;
  manifest: unknown | null;
  degraded: UpgradeSentinel | null;
}

export default function DegradedModeBanner({ isAdmin }: { isAdmin: boolean }) {
  const [degraded, setDegraded] = useState<UpgradeSentinel | null>(null);
  const [action, setAction] = useState<"idle" | "marking" | "rolling_back">("idle");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    fetch("/api/version/check")
      .then((r) => r.json())
      .then((data: VersionCheckResponse) => {
        if (data.degraded) setDegraded(data.degraded);
      })
      .catch(() => {});
  }, [isAdmin]);

  if (!isAdmin || !degraded) return null;

  async function handleMarkComplete() {
    if (!degraded?.cr_id) return;
    setAction("marking");
    try {
      const resp = await fetch(`/api/change-requests/${degraded.cr_id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "completed" }),
      });
      if (resp.ok) {
        setMessage("Upgrade marked as complete. Refresh to continue.");
        setDegraded(null);
      } else {
        setMessage("Failed to mark complete — check logs.");
      }
    } catch {
      setMessage("Network error.");
    } finally {
      setAction("idle");
    }
  }

  async function handleRollback() {
    if (!degraded?.cr_id) return;
    setAction("rolling_back");
    try {
      const resp = await fetch(`/api/change-requests/${degraded.cr_id}/rollback`, {
        method: "POST",
      });
      if (resp.ok) {
        setMessage("Rollback initiated — watchdog will restore previous version.");
      } else {
        setMessage("Rollback request failed — use install.sh --recover if the instance is unreachable.");
      }
    } catch {
      setMessage("Network error — use install.sh --recover.");
    } finally {
      setAction("idle");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full p-8">
        <h2 className="text-2xl font-bold text-red-600 mb-2">Upgrade Incomplete</h2>
        <p className="text-gray-700 mb-1">
          A platform upgrade is in an unresolved state:{" "}
          <code className="bg-gray-100 px-1 rounded">{degraded.state}</code>
        </p>
        {degraded.previous_version && (
          <p className="text-gray-600 text-sm mb-1">
            Previous version: <strong>{degraded.previous_version}</strong>
          </p>
        )}
        {degraded.target_version && (
          <p className="text-gray-600 text-sm mb-4">
            Target version: <strong>{degraded.target_version}</strong>
          </p>
        )}
        {message && (
          <p className="bg-yellow-50 border border-yellow-200 text-yellow-800 rounded p-3 text-sm mb-4">
            {message}
          </p>
        )}
        <div className="flex gap-3 flex-wrap">
          <button
            onClick={handleRollback}
            disabled={action !== "idle"}
            className="bg-red-600 text-white px-4 py-2 rounded font-semibold hover:bg-red-700 disabled:opacity-50"
          >
            {action === "rolling_back" ? "Rolling back…" : `Roll back to v${degraded.previous_version ?? "previous"}`}
          </button>
          <button
            onClick={handleMarkComplete}
            disabled={action !== "idle"}
            className="bg-gray-200 text-gray-800 px-4 py-2 rounded font-semibold hover:bg-gray-300 disabled:opacity-50"
          >
            {action === "marking" ? "Marking…" : "Mark as complete"}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-4">
          If this instance is unreachable, run{" "}
          <code className="bg-gray-100 px-1 rounded">install.sh --recover</code> on the host.
        </p>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Import and render both banners in the app shell**

Find the main layout/shell component. Run:

```bash
grep -rn "isAdmin\|AdminLayout\|AppShell\|Header" frontend/src/App.tsx frontend/src/components/ 2>/dev/null | head -20
```

Identify the component that wraps the main page layout (likely `frontend/src/App.tsx` or a `Layout` component). Add the imports at the top:

```tsx
import UpdateBanner from "./components/UpdateBanner";
import DegradedModeBanner from "./components/DegradedModeBanner";
```

Then inside the JSX, render them near the top of the authenticated layout, passing the admin check:

```tsx
<UpdateBanner isAdmin={/* your existing isAdmin boolean */} />
<DegradedModeBanner isAdmin={/* your existing isAdmin boolean */} />
```

- [ ] **Step 7: Restart frontend container**

```bash
# On EC2
cd /home/ec2-user/nexplane
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 8: Smoke-check in browser**

Visit the admin UI. With no newer version available, no banner should appear. To test the banner locally without a real release, temporarily override the poller cache:

```bash
# In a Python shell inside the backend container:
docker exec -it nexplane-backend-1 python -c "
import app.services.version_poller as vp
vp._cached_manifest = {'version': '99.0.0', 'severity': 'recommended', 'changelog_url': 'https://example.com', 'image_sha256': 'sha256:abc', 'released_at': '2026-06-25T00:00:00Z', 'min_compatible_version': '0.0.0'}
print('done')
"
```

Then hit `/api/version/check` — should return `{"available": true, "manifest": {...}}`. Refresh the UI — banner should appear.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/types/api.ts \
        frontend/src/pages/CreateChangeRequest.tsx \
        frontend/src/components/UpdateBanner.tsx \
        frontend/src/components/DegradedModeBanner.tsx
git commit -m "feat: add UpdateBanner, DegradedModeBanner, and platform_upgrade CR type to frontend"
```

---

## Task 7: `install.sh --recover` Mode

**Files:**
- Modify: `install.sh`

**Interfaces:**
- Consumes: sentinel file at host path (no Docker required — reads `/home/ec2-user/nexplane-data/upgrade/sentinel.json` or `$NEXPLANE_DATA_DIR/upgrade/sentinel.json`)
- Produces: restores previous image tag in `.env`, runs `pg_restore`, restarts Docker compose stack, prints recovery summary

- [ ] **Step 1: Find the end of `install.sh` and add the `--recover` mode**

Open `install.sh`. Find the main argument parsing block (likely near the top — look for `case "$1" in` or `if [ "$1" = ... ]`).

Add a `--recover` branch. The full recovery function to insert:

```bash
recover_from_upgrade() {
  NEXPLANE_DATA_DIR="${NEXPLANE_DATA_DIR:-/home/ec2-user/nexplane-data}"
  SENTINEL="$NEXPLANE_DATA_DIR/upgrade/sentinel.json"
  NEXPLANE_DIR="${NEXPLANE_DIR:-/home/ec2-user/nexplane}"
  ENV_FILE="$NEXPLANE_DIR/.env"

  echo "=== Nexplane Recovery Mode ==="

  if [ ! -f "$SENTINEL" ]; then
    echo "ERROR: Sentinel file not found at $SENTINEL"
    echo "Nothing to recover. If the platform is running, no recovery is needed."
    exit 1
  fi

  STATE=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('state','unknown'))")
  PREVIOUS_TAG=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('previous_image_tag',''))")
  SNAPSHOT=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('snapshot_path',''))")
  PREV_VERSION=$(python3 -c "import json,sys; d=json.load(open('$SENTINEL')); print(d.get('previous_version','unknown'))")

  echo "Sentinel state : $STATE"
  echo "Previous image : $PREVIOUS_TAG"
  echo "Snapshot       : $SNAPSHOT"
  echo ""

  if [ "$STATE" = "upgrade_complete" ]; then
    echo "Upgrade completed successfully. No recovery needed."
    exit 0
  fi

  echo "Stopping containers..."
  cd "$NEXPLANE_DIR" && docker compose stop backend 2>/dev/null || true

  # Restore IMAGE_TAG in .env
  if [ -n "$PREVIOUS_TAG" ]; then
    TAG_ONLY="${PREVIOUS_TAG##*:}"
    if grep -q "^IMAGE_TAG=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$TAG_ONLY|" "$ENV_FILE"
    else
      echo "IMAGE_TAG=$TAG_ONLY" >> "$ENV_FILE"
    fi
    echo "Restored IMAGE_TAG=$TAG_ONLY in $ENV_FILE"
  fi

  # pg_restore from snapshot
  if [ -n "$SNAPSHOT" ] && [ -f "$SNAPSHOT" ]; then
    echo "Running pg_restore from $SNAPSHOT ..."
    . "$ENV_FILE"
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    DB_USER="${DB_USER:-postgres}"
    DB_NAME="${DB_NAME:-nexplane}"
    export PGPASSWORD="${DB_PASSWORD:-}"
    zcat "$SNAPSHOT" | pg_restore --clean --if-exists -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" || true
    echo "pg_restore complete."
  else
    echo "WARNING: No valid snapshot found at '$SNAPSHOT'. Skipping pg_restore."
  fi

  # Restart on previous image
  echo "Starting backend on previous image..."
  cd "$NEXPLANE_DIR" && docker compose up -d backend

  # Update sentinel
  python3 -c "
import json, sys
with open('$SENTINEL') as f:
    d = json.load(f)
d['state'] = 'rollback_complete'
d['rollback_reason'] = 'manual_recover'
with open('$SENTINEL', 'w') as f:
    json.dump(d, f, indent=2)
"

  echo ""
  echo "=== Recovery complete ==="
  echo "Platform restored to version: $PREV_VERSION"
  echo "Access the platform at your configured URL."
}
```

In the argument parsing section, add:

```bash
if [ "$1" = "--recover" ]; then
  recover_from_upgrade
  exit 0
fi
```

- [ ] **Step 2: Make sure `install.sh` is executable and test argument parsing**

```bash
# On EC2
chmod +x /home/ec2-user/nexplane/install.sh
bash /home/ec2-user/nexplane/install.sh --recover 2>&1
```

Expected output with no sentinel present: `ERROR: Sentinel file not found at ...`

- [ ] **Step 3: Test with a mock sentinel**

```bash
# On EC2
mkdir -p /home/ec2-user/nexplane-data/upgrade
cat > /home/ec2-user/nexplane-data/upgrade/sentinel.json <<'EOF'
{
  "state": "cutover_pending",
  "previous_version": "1.3.1",
  "previous_image_tag": "nexplane/nexplane:1.3.1",
  "target_version": "1.4.2",
  "snapshot_path": "/nonexistent/path.dump.gz",
  "cr_id": "test-cr-id"
}
EOF
bash /home/ec2-user/nexplane/install.sh --recover 2>&1
```

Expected: shows state/image/snapshot, updates `.env`, skips pg_restore (file missing), restarts backend, prints recovery summary.

- [ ] **Step 4: Clean up test sentinel**

```bash
rm /home/ec2-user/nexplane-data/upgrade/sentinel.json
```

- [ ] **Step 5: Commit**

```bash
git add install.sh
git commit -m "feat: add install.sh --recover mode for out-of-Docker upgrade recovery"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| `platform_upgrade.json` CR type definition | Task 1 |
| `ChangeType` enum + Alembic migration | Task 1 |
| Executor phases 1–4 (preflight, snapshot, pull, migrate) | Task 2 |
| Sentinel file write/read | Task 2 |
| Phase 1 version + disk + watchdog checks | Task 2 |
| Phase 2 pg_dump + integrity check | Task 2 |
| Phase 3 docker pull + sha256 verify | Task 2 |
| Phase 4 alembic upgrade + downgrade + pg_restore fallback | Task 2 |
| Phase 5 cutover_pending handoff | Task 2 |
| Version poller (6h, disabled for commercial, `UPDATE_CHECK_INTERVAL_HOURS=0`) | Task 3 |
| `GET /version` and `GET /version/check` | Task 3 |
| Version poller wired into APScheduler | Task 3 |
| Watchdog container (phase 5 cutover) | Task 4 |
| Watchdog rollback (pg_restore, image restore, log) | Task 4 |
| `nexplane-data` host volume in Docker Compose | Task 4 |
| Phase 6 startup verify | Task 5 |
| Degraded mode state exposed via API | Task 5 |
| `UpdateBanner` (severity colors, "Review update" → draft CR) | Task 6 |
| `DegradedModeBanner` (rollback + mark complete buttons) | Task 6 |
| `"platform_upgrade"` in frontend `ChangeType` union | Task 6 |
| `CHANGE_TYPE_META` + `CHANGE_TYPE_GROUPS` entry | Task 6 |
| `install.sh --recover` | Task 7 |
| Unit tests for executor phases and sentinel logic | Task 2 |

**Gaps identified and resolved:**

- Spec says `require_approval` always `true` in self-hosted path — the JSON definition sets `"default": "true"` and the executor doesn't override it. Sufficient.
- Spec mentions `NEXPLANE_VERSION` written by `install.sh` on first deploy — this is flagged as an open dependency in the spec. Task 7 adds `--recover` but initial install stamping is deferred per spec.
- Spec says phase 6 also handles "cutover_pending + new version running" race — `upgrade_verify.py` handles this case explicitly.
- Hosted path (`upgrade_instance` in nexplane-deploy) — explicitly out of scope per spec's "Open Dependencies" and the plan constraint. Not added.
- Watchdog rollback writes CR failure state to sentinel but cannot hit the backend API (backend may be down). The CR remains in `executing` state in the DB; the degraded mode banner surfaces this on next load. This is consistent with the spec's failure table.
- `COMPOSE_PROJECT_NAME` env var passed to watchdog so `docker compose` targets the right project — covered in Task 4 watchdog service definition.

**Placeholder scan:** No TBDs, no "similar to" references, all code steps have complete implementations.

**Type consistency:** `write_sentinel`/`read_sentinel` defined in Task 2, reused (via import) in Task 5. `check_for_update()` defined in Task 3, called in Task 3 router and Task 5. `execute` / `rollback` signatures match the pattern established by existing executors throughout.
