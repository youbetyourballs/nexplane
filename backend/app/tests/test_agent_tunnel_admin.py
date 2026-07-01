# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Admin tunnel-config API tests. Mounts only the tunnel-admin router with
fake auth + DB so no live database is needed. Run with --noconftest."""
import types
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models.user import UserRole
from app.routers import current_user
from app.routers.agent_tunnel_admin import router


def _reg(enabled=False, allowlist=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        hostname="agent-host",
        organization_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        tunnel_enabled=enabled,
        tunnel_allowlist=allowlist or [],
    )


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeSession:
    def __init__(self, regs):
        self.regs = regs
        self.committed = False

    async def execute(self, *_a, **_k):
        return _Result(self.regs)

    def add(self, _obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        pass


def _client(regs):
    app = FastAPI()
    app.include_router(router)
    admin = types.SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4(), role=UserRole.admin)
    session = _FakeSession(regs)
    app.dependency_overrides[current_user] = lambda: admin
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app), session


def test_list_reports_offline_and_config():
    reg = _reg(enabled=True, allowlist=["10.0.0.0/8:5432"])
    client, _ = _client([reg])
    resp = client.get("/agents/tunnel")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["tunnel_enabled"] is True
    assert body[0]["tunnel_allowlist"] == ["10.0.0.0/8:5432"]
    assert body[0]["online"] is False  # not connected to this process's relay


def test_put_valid_enables_and_persists():
    reg = _reg()
    client, session = _client([reg])
    resp = client.put(
        f"/agents/{reg.id}/tunnel",
        json={"enabled": True, "allowlist": ["10.0.0.0/8:5432", "db.internal:5432"]},
    )
    assert resp.status_code == 200, resp.text
    assert reg.tunnel_enabled is True
    assert reg.tunnel_allowlist == ["10.0.0.0/8:5432", "db.internal:5432"]
    assert session.committed is True


def test_put_rejects_enabled_with_empty_allowlist():
    reg = _reg()
    client, session = _client([reg])
    resp = client.put(f"/agents/{reg.id}/tunnel", json={"enabled": True, "allowlist": []})
    assert resp.status_code == 400
    assert "empty allowlist" in resp.text
    assert session.committed is False


def test_put_rejects_malformed_allowlist():
    reg = _reg()
    client, _ = _client([reg])
    resp = client.put(
        f"/agents/{reg.id}/tunnel",
        json={"enabled": True, "allowlist": ["not a valid rule at all ::: 999999"]},
    )
    assert resp.status_code == 400
    assert "Invalid allowlist" in resp.text


def test_put_unknown_agent_404():
    client, _ = _client([])  # no regs -> scalar_one_or_none returns None
    resp = client.put(
        f"/agents/{uuid.uuid4()}/tunnel",
        json={"enabled": True, "allowlist": ["10.0.0.0/8:5432"]},
    )
    assert resp.status_code == 404


def test_put_disable_allows_empty_allowlist():
    reg = _reg(enabled=True, allowlist=["10.0.0.0/8:5432"])
    client, session = _client([reg])
    resp = client.put(f"/agents/{reg.id}/tunnel", json={"enabled": False, "allowlist": []})
    assert resp.status_code == 200, resp.text
    assert reg.tunnel_enabled is False
    assert reg.tunnel_allowlist == []
    assert session.committed is True


def test_list_includes_asset_id():
    reg = _reg(enabled=True, allowlist=["10.0.0.0/8:5432"])
    reg.asset_id = uuid.uuid4()
    client, _ = _client([reg])
    resp = client.get("/agents/tunnel")
    assert resp.status_code == 200
    assert resp.json()[0]["asset_id"] == str(reg.asset_id)
