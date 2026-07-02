# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Edition/capabilities probe — lets the frontend gate edition-specific UI."""
from fastapi import APIRouter, Depends

from app.config import settings
from app.connectors.catalog_service import get_catalog_service
from app.models.user import User
from app.routers import current_user

router = APIRouter(tags=["Capabilities"])


@router.get("/capabilities")
async def get_capabilities(user: User = Depends(current_user)):
    domains = sorted({a.get("domain", "core") for a in get_catalog_service().list_all_actions()})
    return {
        "edition": settings.NEXPLANE_EDITION,
        "commercial": "commercial" in domains,
        "domains": domains,
    }
