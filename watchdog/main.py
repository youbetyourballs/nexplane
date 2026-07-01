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

    # 5. Write failure state to sentinel (backend may be down so we can't hit the API)
    updated = {**sentinel, "state": "rollback_complete", "rollback_reason": "watchdog_rollback"}
    _write_sentinel(updated)
    log.info("Rollback complete — sentinel set to rollback_complete")


def main():
    log.info("nexplane-watchdog started. Watching %s", SENTINEL_PATH)
    while True:
        sentinel = _read_sentinel()
        if sentinel and sentinel.get("state") == "cutover_pending":
            log.info("Detected cutover_pending for CR %s", sentinel.get("cr_id"))
            success = _do_cutover(sentinel)
            if success:
                updated = {**sentinel, "state": "cutover_complete", "cutover_completed_at": datetime.now(timezone.utc).isoformat()}
                _write_sentinel(updated)
                log.info("Sentinel updated to cutover_complete")
            else:
                _do_rollback(sentinel)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
