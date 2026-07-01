# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Tests for network_path validation on the connector create endpoint.
Mounts only the connectors router with fake auth + DB; no live DB needed.
Run with --noconftest."""
import types
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models.connector import ConnectorType, ConnectorStatus
from app.models.user import UserRole
from app.routers import current_user
from app.routers.connectors import router, _validate_network_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_agent(org_id, tunnel_enabled=True):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=org_id,
        tunnel_enabled=tunnel_enabled,
    )


class _Result:
    def __init__(self, item=None):
        self._item = item

    def scalar_one_or_none(self):
        return self._item


class _FakeSession:
    """Fake async DB session. agent_result controls what agent queries return."""

    def __init__(self, agent=None):
        self._agent = agent
        self.added = []
        self.committed = False
        self.flushed = False

    async def execute(self, *_a, **_k):
        return _Result(self._agent)

    def add(self, obj):
        # Assign sentinel values so ConnectorRead serialisation succeeds
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "status", None):
            obj.status = ConnectorStatus.inactive
        if not getattr(obj, "created_at", None):
            obj.created_at = datetime.now(timezone.utc)
        if not getattr(obj, "organization_id", None):
            obj.organization_id = uuid.uuid4()
        if not getattr(obj, "network_path", None):
            pass  # already set from constructor
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


def _make_client(fake_session):
    app = FastAPI()
    app.include_router(router)
    org_id = uuid.uuid4()
    user = types.SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=org_id,
        role=UserRole.admin,
    )
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: fake_session
    return TestClient(app), user


def _post_create(client, connector_type="postgres", network_path="direct", tls_skip=False):
    return client.post(
        "/connectors",
        json={
            "connector_type": connector_type,
            "name": "test-connector",
            "network_path": network_path,
            "network_tls_skip_verify": tls_skip,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_direct_path_succeeds():
    """network_path='direct' (or omitted) should succeed."""
    session = _FakeSession(agent=None)
    client, _ = _make_client(session)
    resp = _post_create(client, connector_type="postgres", network_path="direct")
    assert resp.status_code == 201, resp.text


def test_create_via_agent_routable_type_enabled_agent_succeeds():
    """via_agent:<uuid> for routable type + tunnel-enabled agent in same org → 201."""
    org_id = uuid.uuid4()
    agent = _fake_agent(org_id, tunnel_enabled=True)
    session = _FakeSession(agent=agent)
    client, user = _make_client(session)
    # Override org_id to match
    user.organization_id = org_id
    resp = _post_create(client, connector_type="postgres", network_path=f"via_agent:{agent.id}")
    assert resp.status_code == 201, resp.text


def test_create_via_agent_non_routable_type_rejected():
    """via_agent for a non-routable connector type → 400."""
    org_id = uuid.uuid4()
    agent = _fake_agent(org_id, tunnel_enabled=True)
    session = _FakeSession(agent=agent)
    client, user = _make_client(session)
    user.organization_id = org_id
    resp = _post_create(client, connector_type="aws", network_path=f"via_agent:{agent.id}")
    assert resp.status_code == 400, resp.text
    assert "not supported" in resp.json()["detail"]


def test_create_via_agent_missing_agent_rejected():
    """via_agent where the agent doesn't exist → 400."""
    session = _FakeSession(agent=None)  # no agent in DB
    client, _ = _make_client(session)
    resp = _post_create(client, connector_type="postgres", network_path=f"via_agent:{uuid.uuid4()}")
    assert resp.status_code == 400, resp.text
    assert "tunnel-enabled" in resp.json()["detail"]


def test_create_via_agent_disabled_agent_rejected():
    """via_agent where tunnel_enabled=False → 400."""
    org_id = uuid.uuid4()
    agent = _fake_agent(org_id, tunnel_enabled=False)
    session = _FakeSession(agent=agent)
    client, user = _make_client(session)
    user.organization_id = org_id
    resp = _post_create(client, connector_type="postgres", network_path=f"via_agent:{agent.id}")
    assert resp.status_code == 400, resp.text
    assert "tunnel-enabled" in resp.json()["detail"]


def test_create_via_agent_bad_uuid_rejected():
    """via_agent:<not-a-uuid> → 400."""
    session = _FakeSession(agent=None)
    client, _ = _make_client(session)
    resp = _post_create(client, connector_type="postgres", network_path="via_agent:not-a-uuid")
    assert resp.status_code == 400, resp.text
    assert "Invalid agent id" in resp.json()["detail"]


def test_create_malformed_network_path_rejected():
    """network_path that's neither 'direct' nor 'via_agent:...' → 400."""
    session = _FakeSession(agent=None)
    client, _ = _make_client(session)
    resp = _post_create(client, connector_type="postgres", network_path="via_proxy:something")
    assert resp.status_code == 400, resp.text
    assert "network_path must be" in resp.json()["detail"]


def test_create_returns_network_path_in_response():
    """ConnectorRead should include network_path."""
    session = _FakeSession(agent=None)
    client, _ = _make_client(session)
    resp = _post_create(client, connector_type="postgres", network_path="direct")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "network_path" in body
    assert body["network_path"] == "direct"
    assert "network_tls_skip_verify" in body
    assert body["network_tls_skip_verify"] is False


# ---------------------------------------------------------------------------
# Direct unit tests for _validate_network_path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_network_path_direct_noop():
    """direct path should not raise."""
    await _validate_network_path("direct", ConnectorType.postgres, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_validate_network_path_empty_noop():
    """empty string treated as direct, no raise."""
    await _validate_network_path("", ConnectorType.postgres, uuid.uuid4(), None)
