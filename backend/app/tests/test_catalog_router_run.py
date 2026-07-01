# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import types, uuid, asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.models.user import UserRole
from app.routers import current_user
import app.routers.catalog as catmod

def _client(action_def, exec_result):
    app = FastAPI(); app.include_router(catmod.router)
    admin = types.SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4(), role=UserRole.admin)
    app.dependency_overrides[current_user] = lambda: admin
    catmod.get_catalog_service = lambda: types.SimpleNamespace(get_action_def=lambda ct, aid: action_def)
    async def fake_exec(connector_type, action_id, parameters, asset_ids, connector=None, db=None):
        return exec_result
    catmod.execute_action = fake_exec
    return TestClient(app)

def test_run_readonly_returns_result():
    c = _client({"action_id": "list_customers", "read_only": True}, {"customers": [{"client_id": "acme"}]})
    r = c.post("/catalog/run", json={"connector_type": "commercial", "action_id": "list_customers", "params": {}})
    assert r.status_code == 200
    assert r.json()["customers"][0]["client_id"] == "acme"

def test_run_rejects_mutating():
    c = _client({"action_id": "provision_instance", "read_only": False}, {})
    r = c.post("/catalog/run", json={"connector_type": "commercial", "action_id": "provision_instance", "params": {}})
    assert r.status_code == 400
