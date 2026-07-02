# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Unit tests for tunnel WS short-lived per-connection token logic.

Tests the expiry check and single-use enforcement in
app.tunnel.relay._consume_tunnel_token without touching a real database:
a lightweight in-memory SQLite AsyncSession is used so the ORM queries run
against real SQL while staying fully offline.
"""
import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models.agent import AgentRegistration, TunnelConnectionToken, OsType
from app.tunnel.relay import _consume_tunnel_token


# ---------------------------------------------------------------------------
# In-memory SQLite fixture — only the tables this test touches
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    # Create only the two tables the tests use; the full metadata contains
    # Postgres-specific types (JSONB, UUID) that SQLite rejects.
    async with engine.begin() as conn:
        await conn.run_sync(AgentRegistration.metadata.create_all, tables=[
            AgentRegistration.__table__,
            TunnelConnectionToken.__table__,
        ])
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def agent(db: AsyncSession):
    """Create a minimal AgentRegistration for tests."""
    org_id = uuid.uuid4()
    reg = AgentRegistration(
        organization_id=org_id,
        machine_id="test-machine-001",
        hostname="test-host",
        os_type=OsType.linux,
        ip_addresses=[],
        os_version="Ubuntu 22.04",
        agent_version="1.0.0",
        tunnel_enabled=True,
        tunnel_allowlist=["10.0.0.0/8:22"],
    )
    db.add(reg)
    await db.commit()
    await db.refresh(reg)
    return reg


def _make_token(db: AsyncSession, agent_id, *, offset_seconds: int = 60) -> str:
    """Insert a TunnelConnectionToken and return the raw token string."""
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    record = TunnelConnectionToken(
        agent_id=agent_id,
        token_hash=token_hash,
        expires_at=now + timedelta(seconds=offset_seconds),
    )
    db.add(record)
    return raw, record


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_token_consumed(db: AsyncSession, agent: AgentRegistration):
    """A fresh, valid token returns the AgentRegistration and marks used_at."""
    raw, record = _make_token(db, agent.id, offset_seconds=60)
    await db.commit()

    result = await _consume_tunnel_token(raw, str(agent.id), db)

    assert result is not None, "Expected a registration for a valid token"
    assert result.id == agent.id

    await db.refresh(record)
    assert record.used_at is not None, "used_at should be set after consumption"


@pytest.mark.asyncio
async def test_expired_token_rejected(db: AsyncSession, agent: AgentRegistration):
    """A token whose expires_at is in the past is rejected."""
    raw, record = _make_token(db, agent.id, offset_seconds=-1)  # already expired
    await db.commit()

    result = await _consume_tunnel_token(raw, str(agent.id), db)

    assert result is None, "Expired token must be rejected"
    await db.refresh(record)
    assert record.used_at is None, "used_at must not be set for an expired token"


@pytest.mark.asyncio
async def test_single_use_enforcement(db: AsyncSession, agent: AgentRegistration):
    """A token that was already used is rejected on the second attempt."""
    raw, record = _make_token(db, agent.id, offset_seconds=60)
    await db.commit()

    # First use — should succeed.
    result1 = await _consume_tunnel_token(raw, str(agent.id), db)
    assert result1 is not None, "First use of a valid token must succeed"

    # Second use of the same token — must be rejected.
    result2 = await _consume_tunnel_token(raw, str(agent.id), db)
    assert result2 is None, "Second use of the same token must be rejected"


@pytest.mark.asyncio
async def test_unknown_token_rejected(db: AsyncSession, agent: AgentRegistration):
    """A token that does not exist in the DB is rejected cleanly."""
    nonexistent = secrets.token_hex(32)
    result = await _consume_tunnel_token(nonexistent, str(agent.id), db)
    assert result is None


@pytest.mark.asyncio
async def test_agent_id_mismatch_rejected(db: AsyncSession, agent: AgentRegistration):
    """A valid token presented for the wrong agent_id is rejected."""
    raw, _ = _make_token(db, agent.id, offset_seconds=60)
    await db.commit()

    wrong_agent_id = str(uuid.uuid4())
    result = await _consume_tunnel_token(raw, wrong_agent_id, db)
    assert result is None, "Token belonging to a different agent must be rejected"


@pytest.mark.asyncio
async def test_invalid_agent_id_rejected(db: AsyncSession, agent: AgentRegistration):
    """A malformed agent_id string (not a UUID) is rejected without crashing."""
    raw, _ = _make_token(db, agent.id, offset_seconds=60)
    await db.commit()

    result = await _consume_tunnel_token(raw, "not-a-uuid", db)
    assert result is None
