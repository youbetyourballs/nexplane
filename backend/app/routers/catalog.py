# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Generic catalog action discovery + read-only action execution.

These are generic seams (not commercial-specific): they expose whatever actions
are mounted in the catalog (core + any commercial overlay) so the frontend can
render them without hardcoding. See docs/superpowers/specs/2026-07-01-commercial-ui-integration-design.md.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.connectors.catalog_service import get_catalog_service
from app.models.user import User, UserRole
from app.routers import require_roles
from app.services.connector_service import execute_action

router = APIRouter(prefix="/catalog", tags=["Catalog"])


def _shape(a: dict) -> dict:
    ct = a.get("connector_type", "")
    return {
        "connector_type": ct,
        "action_id": a.get("action_id", ""),
        "generic_action": a.get("generic_action", a.get("action_id", "")),
        "display_name": a.get("display_name") or a.get("action_id", "").replace("_", " ").title(),
        "description": a.get("description", ""),
        "group": a.get("group", ""),
        "domain": a.get("domain", "core"),
        "param_schema": a.get("param_schema", []),
        "read_only": bool(a.get("read_only", False)),
        "destructive": bool(a.get("destructive", False)),
        "order": a.get("order", 100),
    }


class RunActionRequest(BaseModel):
    connector_type: str
    action_id: str
    params: dict = {}


@router.post("/run")
async def run_action(
    body: RunActionRequest,
    user: User = Depends(require_roles(UserRole.admin)),
):
    try:
        action_def = get_catalog_service().get_action_def(body.connector_type, body.action_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Action not found")
    if not action_def.get("read_only", False):
        raise HTTPException(status_code=400, detail="Only read-only actions may run here; use a change request")
    return await execute_action(body.connector_type, body.action_id, body.params, asset_ids=[], connector=None)


@router.get("/actions")
async def list_actions(
    domain: str | None = Query(None),
    user: User = Depends(require_roles(UserRole.admin)),
):
    actions = [_shape(a) for a in get_catalog_service().list_all_actions()]
    if domain is not None:
        actions = [a for a in actions if a["domain"] == domain]
    actions.sort(key=lambda a: (a["group"], a["order"], a["action_id"]))
    return actions
