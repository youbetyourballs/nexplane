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
