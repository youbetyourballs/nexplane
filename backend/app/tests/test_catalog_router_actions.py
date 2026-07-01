# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import types, uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.models.user import UserRole
from app.routers import require_roles
import app.routers.catalog as catmod

def _client(actions):
    app = FastAPI(); app.include_router(catmod.router)
    admin = types.SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4(), role=UserRole.admin)
    # require_roles(UserRole.admin) depends on current_user; override current_user
    from app.routers import current_user
    app.dependency_overrides[current_user] = lambda: admin
    fake_cs = types.SimpleNamespace(list_all_actions=lambda: actions)
    catmod.get_catalog_service = lambda: fake_cs
    return TestClient(app)

def test_actions_listed_with_defaults():
    c = _client([{"connector_type": "commercial", "action_id": "provision_instance", "generic_action": "provision_instance", "display_name": "Provision", "group": "Customers", "domain": "commercial", "destructive": False, "read_only": False}])
    r = c.get("/catalog/actions")
    assert r.status_code == 200
    a = r.json()[0]
    assert a["connector_type"] == "commercial" and a["action_id"] == "provision_instance"
    assert a["domain"] == "commercial" and a["read_only"] is False

def test_actions_domain_filter():
    c = _client([
        {"connector_type": "commercial", "action_id": "x", "domain": "commercial"},
        {"connector_type": "aws", "action_id": "y", "domain": "core"},
    ])
    r = c.get("/catalog/actions?domain=commercial")
    assert [a["action_id"] for a in r.json()] == ["x"]
