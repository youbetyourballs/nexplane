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
