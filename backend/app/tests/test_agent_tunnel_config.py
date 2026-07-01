# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Tests for GET /agent/tunnel-config (live config the agent polls). Fake auth +
session, no live DB. Run with --noconftest."""
import types
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers.agent import router, _get_org_settings_by_secret


class _Result:
    def __init__(self, items):
        self._items = items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    def __init__(self, regs):
        self.regs = regs
        self.committed = False

    async def execute(self, *_a, **_k):
        return _Result(self.regs)

    async def commit(self):
        self.committed = True


def _client(regs):
    app = FastAPI()
    app.include_router(router)
    org = types.SimpleNamespace(organization_id=uuid.uuid4())
    session = _FakeSession(regs)
    app.dependency_overrides[_get_org_settings_by_secret] = lambda: org
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app), session


def _reg(enabled, allowlist):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        tunnel_enabled=enabled,
        tunnel_allowlist=allowlist,
        last_seen=None,
    )


def test_returns_enabled_config():
    reg = _reg(True, ["10.0.0.0/8:5432"])
    client, session = _client([reg])
    resp = client.get(f"/agent/tunnel-config?agent_id={reg.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True, "allowlist": ["10.0.0.0/8:5432"]}
    assert session.committed is True         # last_seen refreshed
    assert reg.last_seen is not None


def test_returns_disabled_defaults():
    reg = _reg(False, None)
    client, _ = _client([reg])
    resp = client.get(f"/agent/tunnel-config?agent_id={reg.id}")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "allowlist": []}


def test_unknown_agent_404():
    client, _ = _client([])
    resp = client.get(f"/agent/tunnel-config?agent_id={uuid.uuid4()}")
    assert resp.status_code == 404
