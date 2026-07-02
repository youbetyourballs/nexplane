# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import types, uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers import current_user
import app.routers.capabilities as capmod

def _client(actions, edition="commercial"):
    app = FastAPI(); app.include_router(capmod.router)
    app.dependency_overrides[current_user] = lambda: types.SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    capmod.get_catalog_service = lambda: types.SimpleNamespace(list_all_actions=lambda: actions)
    capmod.settings = types.SimpleNamespace(NEXPLANE_EDITION=edition)
    return TestClient(app)

def test_commercial_true_when_commercial_actions_present():
    c = _client([{"domain": "commercial"}, {"domain": "core"}])
    b = c.get("/capabilities").json()
    assert b["commercial"] is True and "commercial" in b["domains"]

def test_commercial_false_without_commercial_actions():
    c = _client([{"domain": "core"}], edition="core")
    b = c.get("/capabilities").json()
    assert b["commercial"] is False and b["edition"] == "core"
