# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Version info and update-check endpoints."""
from __future__ import annotations

import json as _json
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
    degraded_raw = os.environ.get("NEXPLANE_UPGRADE_DEGRADED")
    degraded = _json.loads(degraded_raw) if degraded_raw else None
    return {
        "available": manifest is not None,
        "manifest": manifest,
        "degraded": degraded,
    }
