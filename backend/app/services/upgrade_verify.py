# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Phase 6: startup verify — runs in the new backend on first boot after cutover."""
from __future__ import annotations

import json
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
