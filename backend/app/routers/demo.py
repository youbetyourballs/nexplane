# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Demo org switcher endpoint — only active when DEMO_MODE=true."""
import os
from fastapi import APIRouter, HTTPException

router = APIRouter()

DEMO_ORGS = [
    {"id": "00000000-0000-0000-0000-000000000001", "name": "Acme Security Corp",        "slug": "acme",    "admin_email": "admin@acme.example",      "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000002", "name": "CloudRun Technologies",     "slug": "saas",    "admin_email": "admin@cloudrun.example",  "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000003", "name": "Meridian Capital Partners", "slug": "finserv", "admin_email": "admin@meridian.example",  "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000004", "name": "Ironclad Systems Group",    "slug": "defense", "admin_email": "admin@ironclad.example",  "admin_password": "admin123"},
]


@router.get("/orgs")
async def list_demo_orgs():
    if os.getenv("DEMO_MODE") != "true":
        raise HTTPException(status_code=404, detail="Not found")
    return DEMO_ORGS
