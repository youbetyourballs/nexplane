# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.models.setup_token import SetupToken


@pytest_asyncio.fixture
async def setup_token_factory(db: AsyncSession):
    """Factory: create a SetupToken in the DB. Returns (raw_token, record)."""
    async def _make(instance_url: str, expires_at: datetime | None = None) -> tuple[str, SetupToken]:
        import secrets
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        if expires_at is None:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        record = SetupToken(
            id=uuid.uuid4(),
            token_hash=token_hash,
            instance_url=instance_url,
            expires_at=expires_at,
        )
        db.add(record)
        await db.flush()
        return raw, record
    return _make


@pytest.mark.asyncio
async def test_setup_tokens_table_exists(db: AsyncSession):
    result = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name='setup_tokens'")
    )
    cols = {r[0] for r in result.fetchall()}
    assert {"id", "token_hash", "instance_url", "expires_at", "used_at", "org_id"} <= cols


@pytest.mark.asyncio
async def test_setup_token_model_roundtrip(db: AsyncSession):
    raw = "testtoken123"
    token = SetupToken(
        id=uuid.uuid4(),
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        instance_url="https://client.example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(token)
    await db.flush()
    assert token.used_at is None
    assert token.org_id is None


@pytest.mark.asyncio
async def test_consume_setup_token_success(client: AsyncClient, setup_token_factory):
    token_raw, token_record = await setup_token_factory(instance_url="http://test")
    with patch("app.config.settings.NEXPLANE_EDITION", "commercial"):
        resp = await client.post("/setup/consume", json={
            "token": token_raw,
            "instance_url": "http://test",
            "admin_email": "admin@example.com",
            "admin_password": "Str0ngP@ssw0rd!",
            "admin_name": "Admin User",
            "org_name": "Acme Corp",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


@pytest.mark.asyncio
async def test_consume_expired_token_rejected(client: AsyncClient, setup_token_factory):
    token_raw, _ = await setup_token_factory(
        instance_url="http://test",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with patch("app.config.settings.NEXPLANE_EDITION", "commercial"):
        resp = await client.post("/setup/consume", json={
            "token": token_raw,
            "instance_url": "http://test",
            "admin_email": "admin@example.com",
            "admin_password": "Str0ngP@ssw0rd!",
            "admin_name": "Admin",
            "org_name": "Acme",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_consume_wrong_instance_url_rejected(client: AsyncClient, setup_token_factory):
    token_raw, _ = await setup_token_factory(instance_url="https://other.example.com")
    with patch("app.config.settings.NEXPLANE_EDITION", "commercial"):
        resp = await client.post("/setup/consume", json={
            "token": token_raw,
            "instance_url": "http://test",
            "admin_email": "admin@example.com",
            "admin_password": "Str0ngP@ssw0rd!",
            "admin_name": "Admin",
            "org_name": "Acme",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_setup_token_requires_ops_secret(client: AsyncClient):
    with patch("app.config.settings.NEXPLANE_EDITION", "commercial"):
        resp = await client.post("/setup/token", json={"instance_url": "https://client.example.com"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_setup_token_success(client: AsyncClient):
    with patch("app.config.settings.NEXPLANE_EDITION", "commercial"), \
         patch("app.dependencies.ops_secret._get_expected_secret", return_value="test-ops-secret"):
        resp = await client.post(
            "/setup/token",
            json={"instance_url": "https://client.example.com"},
            headers={"X-Ops-Secret": "test-ops-secret"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    assert data["setup_url"].startswith("https://client.example.com/setup?token=")


@pytest.mark.asyncio
async def test_setup_endpoints_return_404_on_core_edition(client: AsyncClient):
    with patch("app.config.settings.NEXPLANE_EDITION", "core"):
        resp = await client.post("/setup/consume", json={
            "token": "x", "instance_url": "x", "admin_email": "x@x.com",
            "admin_password": "x" * 12, "admin_name": "x", "org_name": "x",
        })
    assert resp.status_code == 404
