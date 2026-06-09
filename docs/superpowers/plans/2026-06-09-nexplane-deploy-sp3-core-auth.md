# SP3: Core Auth Changes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-use token flow (commercial edition gate) and OIDC identity provider support to the `nexplane` core repo, enabling fresh instances to require a setup token before unlock and orgs to delegate login to an external OIDC provider.

**Architecture:** Two independent feature areas land in parallel under a `NEXPLANE_EDITION=commercial` guard: (A) a `setup_tokens` table + `/setup/consume` + `/setup/token` endpoints plus a redirect middleware that locks unconfigured instances; (B) an `identity_providers` table + CRUD router + OIDC authorization-code flow (`/auth/oidc/{idp_id}/redirect` and `/auth/oidc/{idp_id}/callback`) plus an `auth_mode` field on `Organization`. The ops instance's shared secret (SSM `/nexplane/ops/instance-shared-secret`) gates the machine-to-machine token-creation endpoint. LDAP and SAML are deferred to SP4.

**Tech Stack:** Python 3.12, FastAPI (async), SQLAlchemy 2 mapped-column style, Alembic sequential migrations, PostgreSQL JSONB, `httpx` (async OIDC HTTP calls), `python-jose` (JWT), `passlib[bcrypt]` (password hashing), `boto3` (SSM parameter read), `pytest-asyncio` + `httpx.AsyncClient` (tests).

---

## File Structure

```
backend/
  alembic/versions/
    074_add_setup_tokens.py                     # Part A migration
    075_add_identity_providers.py               # Part B migration
    076_add_org_auth_mode.py                    # Part B migration (org field)

  app/
    models/
      setup_token.py                            # SetupToken SQLAlchemy model
      identity_provider.py                      # IdentityProvider SQLAlchemy model

    routers/
      setup.py                                  # POST /setup/consume, POST /setup/token
      identity_providers.py                     # CRUD /identity-providers + /identity-providers/active
      oidc.py                                   # GET /auth/oidc/{idp_id}/redirect + /callback
      org_auth_mode.py                          # POST /orgs/{id}/auth-mode

    middleware/
      setup_guard.py                            # 307 redirect when instance not yet unlocked

    dependencies/
      ops_secret.py                             # FastAPI dependency: validate ops shared secret header

    schemas/
      setup_token.py                            # Pydantic schemas for setup flow
      identity_provider.py                      # Pydantic schemas for IdP CRUD + OIDC

    services/
      oidc_service.py                           # build_authorization_url(), exchange_code()

    tests/
      test_setup_flow.py                        # Part A tests
      test_identity_providers.py                # Part B CRUD tests
      test_oidc_flow.py                         # Part B OIDC flow tests
```

---

## Task 1 — Migration 074: `setup_tokens` table

### Files
- `backend/alembic/versions/074_add_setup_tokens.py`

### Failing test (write first)

```python
# backend/app/tests/test_setup_flow.py
import pytest
from sqlalchemy import inspect, text

@pytest.mark.asyncio
async def test_setup_tokens_table_exists(db):
    """Migration 074 creates setup_tokens with expected columns."""
    async with db.begin():
        result = await db.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='setup_tokens'")
        )
        cols = {r[0] for r in result.fetchall()}
    assert cols == {"id", "token_hash", "instance_url", "expires_at", "used_at", "org_id"}
```

### Implementation

```python
# backend/alembic/versions/074_add_setup_tokens.py
"""Add setup_tokens table

Revision ID: 074
Revises: 073
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "setup_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("instance_url", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_setup_tokens_token_hash", "setup_tokens", ["token_hash"], unique=True)


def downgrade():
    op.drop_index("ix_setup_tokens_token_hash", table_name="setup_tokens")
    op.drop_table("setup_tokens")
```

### - [ ] Run migration, confirm test passes, commit
```
git add backend/alembic/versions/074_add_setup_tokens.py backend/app/tests/test_setup_flow.py
git commit -m "feat(migration): 074 add setup_tokens table"
```

---

## Task 2 — Migration 075: `identity_providers` table

### Files
- `backend/alembic/versions/075_add_identity_providers.py`

### Failing test

```python
# backend/app/tests/test_identity_providers.py
import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_identity_providers_table_exists(db):
    async with db.begin():
        result = await db.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='identity_providers'")
        )
        cols = {r[0] for r in result.fetchall()}
    assert {"id", "org_id", "type", "name", "status", "enabled", "config", "connector_id"} <= cols
```

### Implementation

```python
# backend/alembic/versions/075_add_identity_providers.py
"""Add identity_providers table

Revision ID: 075
Revises: 074
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade():
    # Enums
    op.execute("CREATE TYPE idp_type AS ENUM ('oidc', 'ldap', 'saml')")
    op.execute("CREATE TYPE idp_status AS ENUM ('pending', 'active')")

    op.create_table(
        "identity_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.Enum("oidc", "ldap", "saml", name="idp_type", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "active", name="idp_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "connector_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connectors.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_identity_providers_org_id", "identity_providers", ["org_id"])


def downgrade():
    op.drop_index("ix_identity_providers_org_id", table_name="identity_providers")
    op.drop_table("identity_providers")
    op.execute("DROP TYPE IF EXISTS idp_status")
    op.execute("DROP TYPE IF EXISTS idp_type")
```

### - [ ] Run migration, confirm test passes, commit
```
git add backend/alembic/versions/075_add_identity_providers.py backend/app/tests/test_identity_providers.py
git commit -m "feat(migration): 075 add identity_providers table"
```

---

## Task 3 — Migration 076: `auth_mode` column on `organizations`

### Files
- `backend/alembic/versions/076_add_org_auth_mode.py`

### Failing test (add to `test_identity_providers.py`)

```python
@pytest.mark.asyncio
async def test_org_has_auth_mode_column(db):
    async with db.begin():
        result = await db.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='organizations' AND column_name='auth_mode'")
        )
        assert result.fetchone() is not None
```

### Implementation

```python
# backend/alembic/versions/076_add_org_auth_mode.py
"""Add auth_mode to organizations

Revision ID: 076
Revises: 075
Create Date: 2026-06-09
"""
import sqlalchemy as sa
from alembic import op

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TYPE org_auth_mode AS ENUM ('local', 'idp')")
    op.add_column(
        "organizations",
        sa.Column(
            "auth_mode",
            sa.Enum("local", "idp", name="org_auth_mode", create_type=False),
            nullable=False,
            server_default="local",
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("auth_mode_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("organizations", "auth_mode_changed_at")
    op.drop_column("organizations", "auth_mode")
    op.execute("DROP TYPE IF EXISTS org_auth_mode")
```

### - [ ] Run migration, confirm test passes, commit
```
git add backend/alembic/versions/076_add_org_auth_mode.py
git commit -m "feat(migration): 076 add org auth_mode column"
```

---

## Task 4 — Model: `SetupToken`

### Files
- `backend/app/models/setup_token.py`
- Update `backend/app/models/__init__.py` to export `SetupToken`

### Failing test

```python
# backend/app/tests/test_setup_flow.py (append)
from app.models.setup_token import SetupToken
import uuid, hashlib
from datetime import datetime, timezone, timedelta

@pytest.mark.asyncio
async def test_setup_token_model_roundtrip(db):
    raw = "testtoken123"
    token = SetupToken(
        id=uuid.uuid4(),
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        instance_url="https://client.example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    assert token.used_at is None
    assert token.org_id is None
```

### Implementation

```python
# backend/app/models/setup_token.py
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class SetupToken(Base):
    __tablename__ = "setup_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    instance_url: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )

    organization: Mapped["Organization | None"] = relationship("Organization", foreign_keys=[org_id])
```

### - [ ] Confirm test passes, commit
```
git add backend/app/models/setup_token.py
git commit -m "feat(model): SetupToken model"
```

---

## Task 5 — Model: `IdentityProvider`

### Files
- `backend/app/models/identity_provider.py`
- Update `backend/app/models/__init__.py`

### Failing test

```python
# backend/app/tests/test_identity_providers.py (append)
from app.models.identity_provider import IdentityProvider, IdpType, IdpStatus
import uuid

@pytest.mark.asyncio
async def test_identity_provider_model_roundtrip(db, org):
    idp = IdentityProvider(
        id=uuid.uuid4(),
        org_id=org.id,
        type=IdpType.oidc,
        name="Test OIDC",
        status=IdpStatus.pending,
        enabled=False,
        config={"issuer": "https://accounts.example.com", "client_id": "abc", "client_secret": "secret"},
    )
    db.add(idp)
    await db.commit()
    await db.refresh(idp)
    assert idp.connector_id is None
    assert idp.config["issuer"] == "https://accounts.example.com"
```

### Implementation

```python
# backend/app/models/identity_provider.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, Boolean, ForeignKey, func, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class IdpType(str, enum.Enum):
    oidc = "oidc"
    ldap = "ldap"
    saml = "saml"


class IdpStatus(str, enum.Enum):
    pending = "pending"
    active = "active"


class IdentityProvider(Base):
    __tablename__ = "identity_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    type: Mapped[IdpType] = mapped_column(SAEnum(IdpType, name="idp_type"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[IdpStatus] = mapped_column(
        SAEnum(IdpStatus, name="idp_status"), nullable=False, default=IdpStatus.pending
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped["Organization"] = relationship("Organization", foreign_keys=[org_id])
    connector: Mapped["Connector | None"] = relationship("Connector", foreign_keys=[connector_id])
```

### - [ ] Confirm test passes, commit
```
git add backend/app/models/identity_provider.py
git commit -m "feat(model): IdentityProvider model"
```

---

## Task 6 — Update `Organization` model: `auth_mode`

### Files
- `backend/app/models/organization.py` — add `auth_mode` and `auth_mode_changed_at` fields

### Implementation (edit existing file)

```python
# Add to top-level imports in organization.py
import enum
from sqlalchemy import String, DateTime, func, Enum as SAEnum

# Add enum before class Organization:
class OrgAuthMode(str, enum.Enum):
    local = "local"
    idp = "idp"

# Add fields inside Organization class:
    auth_mode: Mapped[OrgAuthMode] = mapped_column(
        SAEnum(OrgAuthMode, name="org_auth_mode"), nullable=False, default=OrgAuthMode.local, server_default="local"
    )
    auth_mode_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    identity_providers: Mapped[list["IdentityProvider"]] = relationship("IdentityProvider", back_populates="organization", foreign_keys="[IdentityProvider.org_id]")
```

### - [ ] Confirm existing org tests still pass, commit
```
git add backend/app/models/organization.py
git commit -m "feat(model): add auth_mode to Organization"
```

---

## Task 7 — Dependency: ops shared secret

### Files
- `backend/app/dependencies/ops_secret.py`

### Failing test (add to `test_setup_flow.py`)

```python
from fastapi.testclient import TestClient
from unittest.mock import patch

def test_ops_secret_dependency_rejects_missing_header(client):
    resp = client.post("/setup/token", json={"instance_url": "https://x.example.com"})
    assert resp.status_code == 401

def test_ops_secret_dependency_rejects_wrong_secret(client):
    with patch("app.dependencies.ops_secret._get_expected_secret", return_value="correct-secret"):
        resp = client.post(
            "/setup/token",
            json={"instance_url": "https://x.example.com"},
            headers={"X-Ops-Secret": "wrong-secret"},
        )
    assert resp.status_code == 401
```

### Implementation

```python
# backend/app/dependencies/ops_secret.py
import os
import logging
from functools import lru_cache
from fastapi import Header, HTTPException, status

_log = logging.getLogger(__name__)

_SSM_PARAM = "/nexplane/ops/instance-shared-secret"


@lru_cache(maxsize=1)
def _get_expected_secret() -> str:
    """Read ops shared secret from SSM. Cached for process lifetime."""
    # Allow override via env var for testing
    env_override = os.environ.get("NEXPLANE_OPS_SECRET")
    if env_override:
        return env_override
    try:
        import boto3
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=_SSM_PARAM, WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception as exc:
        _log.error("Failed to read ops shared secret from SSM: %s", exc)
        raise RuntimeError("Ops shared secret unavailable") from exc


async def require_ops_secret(x_ops_secret: str | None = Header(default=None, alias="X-Ops-Secret")) -> None:
    """FastAPI dependency: validates the ops instance shared secret header."""
    if not x_ops_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Ops-Secret header")
    try:
        expected = _get_expected_secret()
    except RuntimeError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ops secret configuration error")
    if x_ops_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ops secret")
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/dependencies/ops_secret.py
git commit -m "feat(dependency): ops shared secret validation"
```

---

## Task 8 — Schemas: setup flow

### Files
- `backend/app/schemas/setup_token.py`

### Implementation

```python
# backend/app/schemas/setup_token.py
import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr


class SetupConsumeRequest(BaseModel):
    token: str                 # raw token (will be hashed server-side)
    instance_url: str          # must match token's instance_url
    admin_email: EmailStr
    admin_password: str        # min 12 chars enforced in router
    admin_name: str
    org_name: str


class SetupConsumeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    org_id: uuid.UUID


class SetupTokenCreateRequest(BaseModel):
    instance_url: str


class SetupTokenCreateResponse(BaseModel):
    token: str                 # raw token — shown once
    setup_url: str             # instance_url + /setup?token=<raw>
    expires_at: datetime
```

---

## Task 9 — Schemas: identity providers

### Files
- `backend/app/schemas/identity_provider.py`

### Implementation

```python
# backend/app/schemas/identity_provider.py
import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel
from app.models.identity_provider import IdpType, IdpStatus


class IdentityProviderCreate(BaseModel):
    type: IdpType
    name: str
    config: dict[str, Any]
    connector_id: uuid.UUID | None = None


class IdentityProviderUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    connector_id: uuid.UUID | None = None


class IdentityProviderRead(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    type: IdpType
    name: str
    status: IdpStatus
    enabled: bool
    config: dict[str, Any]
    connector_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuthModeSwitch(BaseModel):
    auth_mode: str              # "local" or "idp"
    idp_id: uuid.UUID | None = None   # required when switching to "idp"
```

### - [ ] Commit schemas together
```
git add backend/app/schemas/setup_token.py backend/app/schemas/identity_provider.py
git commit -m "feat(schemas): setup flow and identity provider schemas"
```

---

## Task 10 — Router: `setup.py` (Part A endpoints)

### Files
- `backend/app/routers/setup.py`
- `backend/app/main.py` — register router, conditional on edition

### Failing tests

```python
# backend/app/tests/test_setup_flow.py (append)
import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_consume_setup_token_success(async_client: AsyncClient, db, setup_token_factory):
    token_raw, token_record = await setup_token_factory(instance_url="http://testserver")
    resp = await async_client.post("/setup/consume", json={
        "token": token_raw,
        "instance_url": "http://testserver",
        "admin_email": "admin@example.com",
        "admin_password": "Str0ngP@ssw0rd!",
        "admin_name": "Admin User",
        "org_name": "Acme Corp",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    # Token is now marked used
    await db.refresh(token_record)
    assert token_record.used_at is not None

@pytest.mark.asyncio
async def test_consume_expired_token_rejected(async_client: AsyncClient, setup_token_factory):
    from datetime import datetime, timezone, timedelta
    token_raw, _ = await setup_token_factory(
        instance_url="http://testserver",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1)  # already expired
    )
    resp = await async_client.post("/setup/consume", json={
        "token": token_raw,
        "instance_url": "http://testserver",
        "admin_email": "admin@example.com",
        "admin_password": "Str0ngP@ssw0rd!",
        "admin_name": "Admin",
        "org_name": "Acme",
    })
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_consume_wrong_instance_url_rejected(async_client: AsyncClient, setup_token_factory):
    token_raw, _ = await setup_token_factory(instance_url="https://other.example.com")
    resp = await async_client.post("/setup/consume", json={
        "token": token_raw,
        "instance_url": "http://testserver",   # wrong
        "admin_email": "admin@example.com",
        "admin_password": "Str0ngP@ssw0rd!",
        "admin_name": "Admin",
        "org_name": "Acme",
    })
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_create_setup_token_requires_ops_secret(async_client: AsyncClient):
    resp = await async_client.post("/setup/token", json={"instance_url": "https://client.example.com"})
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_create_setup_token_success(async_client: AsyncClient):
    with patch("app.dependencies.ops_secret._get_expected_secret", return_value="test-ops-secret"):
        resp = await async_client.post(
            "/setup/token",
            json={"instance_url": "https://client.example.com"},
            headers={"X-Ops-Secret": "test-ops-secret"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    assert data["setup_url"].startswith("https://client.example.com/setup?token=")
```

### Implementation

```python
# backend/app/routers/setup.py
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.config import settings
from app.database import get_db
from app.models.setup_token import SetupToken
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.dependencies.ops_secret import require_ops_secret
from app.schemas.setup_token import (
    SetupConsumeRequest,
    SetupConsumeResponse,
    SetupTokenCreateRequest,
    SetupTokenCreateResponse,
)
from app.services.auth_service import create_access_token, hash_password

router = APIRouter(prefix="/setup", tags=["Setup"])

_SETUP_TOKEN_TTL_HOURS = 24


def _require_commercial():
    if settings.NEXPLANE_EDITION != "commercial":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/consume", response_model=SetupConsumeResponse)
async def consume_setup_token(
    body: SetupConsumeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Validate a setup token, create the first admin + org, return JWT."""
    _require_commercial()

    if len(body.admin_password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    result = await db.execute(
        select(SetupToken).where(SetupToken.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=400, detail="Invalid token")
    if record.used_at is not None:
        raise HTTPException(status_code=400, detail="Token has already been used")
    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Token has expired")
    if record.instance_url.rstrip("/") != body.instance_url.rstrip("/"):
        raise HTTPException(status_code=400, detail="Token is not valid for this instance URL")

    # Create org
    org = Organization(id=uuid.uuid4(), name=body.org_name)
    db.add(org)
    await db.flush()

    # Create admin user
    admin = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=body.admin_email,
        name=body.admin_name,
        role=UserRole.admin,
        hashed_password=hash_password(body.admin_password),
    )
    db.add(admin)

    # Mark token used and link org
    record.used_at = datetime.now(timezone.utc)
    record.org_id = org.id

    await db.commit()

    access_token = create_access_token(str(admin.id))
    return SetupConsumeResponse(
        access_token=access_token,
        user_id=admin.id,
        org_id=org.id,
    )


@router.post("/token", response_model=SetupTokenCreateResponse, status_code=201,
             dependencies=[Depends(require_ops_secret)])
async def create_setup_token(
    body: SetupTokenCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new one-time setup token (machine-to-machine, ops instance only)."""
    _require_commercial()

    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_SETUP_TOKEN_TTL_HOURS)

    record = SetupToken(
        id=uuid.uuid4(),
        token_hash=token_hash,
        instance_url=body.instance_url.rstrip("/"),
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()

    setup_url = f"{body.instance_url.rstrip('/')}/setup?token={raw}"
    return SetupTokenCreateResponse(token=raw, setup_url=setup_url, expires_at=expires_at)
```

### Register in `main.py`

In `main.py`, add after the existing router imports:

```python
from app.routers.setup import router as setup_router
# ...
app.include_router(setup_router)
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/routers/setup.py backend/app/main.py
git commit -m "feat(router): setup consume and token endpoints"
```

---

## Task 11 — Middleware: setup guard (redirect unconfigured instances)

### Files
- `backend/app/middleware/setup_guard.py`
- `backend/app/main.py` — add middleware

### Failing test

```python
# backend/app/tests/test_setup_flow.py (append)
@pytest.mark.asyncio
async def test_setup_guard_redirects_empty_instance(async_client_no_users: AsyncClient):
    """All non-setup routes redirect to /setup when no users exist."""
    resp = await async_client_no_users.get("/health", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/setup"

@pytest.mark.asyncio
async def test_setup_guard_allows_setup_routes(async_client_no_users: AsyncClient):
    """Routes starting with /setup are exempt from the guard."""
    resp = await async_client_no_users.get("/setup", follow_redirects=False)
    assert resp.status_code != 307

@pytest.mark.asyncio
async def test_setup_guard_inactive_when_not_commercial(async_client_no_users_core: AsyncClient):
    """Guard does not activate on core edition."""
    resp = await async_client_no_users_core.get("/health", follow_redirects=False)
    assert resp.status_code == 200
```

### Implementation

```python
# backend/app/middleware/setup_guard.py
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User

# Paths exempt from the guard
_EXEMPT_PREFIXES = ("/setup", "/health", "/docs", "/redoc", "/openapi.json", "/mcp")


class SetupGuardMiddleware:
    """
    When NEXPLANE_EDITION=commercial and no users exist in the DB,
    redirect all non-exempt requests to /setup (307 Temporary Redirect).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if settings.NEXPLANE_EDITION != "commercial":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "/")
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Check whether any user exists (fast COUNT)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(func.count()).select_from(User))
            user_count = result.scalar_one()

        if user_count == 0:
            response = RedirectResponse(url="/setup", status_code=307)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
```

In `main.py`, add after middleware setup:

```python
from app.middleware.setup_guard import SetupGuardMiddleware
app.add_middleware(SetupGuardMiddleware)
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/middleware/setup_guard.py backend/app/main.py
git commit -m "feat(middleware): setup guard redirect for unconfigured commercial instances"
```

---

## Task 12 — Router: `identity_providers.py` (CRUD)

### Files
- `backend/app/routers/identity_providers.py`
- `backend/app/main.py` — register

### Failing tests

```python
# backend/app/tests/test_identity_providers.py (append)

@pytest.mark.asyncio
async def test_create_identity_provider(auth_client: AsyncClient, org):
    resp = await auth_client.post("/identity-providers", json={
        "type": "oidc",
        "name": "Google OIDC",
        "config": {
            "issuer": "https://accounts.google.com",
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "secret",
            "scopes": ["openid", "email", "profile"],
        },
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "oidc"
    assert data["status"] == "pending"
    assert data["enabled"] is False

@pytest.mark.asyncio
async def test_list_identity_providers(auth_client: AsyncClient, idp_factory):
    await idp_factory(name="IDP1")
    await idp_factory(name="IDP2")
    resp = await auth_client.get("/identity-providers")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2

@pytest.mark.asyncio
async def test_get_identity_provider_by_id(auth_client: AsyncClient, idp_factory):
    idp = await idp_factory(name="MyIDP")
    resp = await auth_client.get(f"/identity-providers/{idp['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "MyIDP"

@pytest.mark.asyncio
async def test_update_identity_provider(auth_client: AsyncClient, idp_factory):
    idp = await idp_factory(name="OldName")
    resp = await auth_client.put(f"/identity-providers/{idp['id']}", json={"name": "NewName"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "NewName"

@pytest.mark.asyncio
async def test_delete_identity_provider(auth_client: AsyncClient, idp_factory):
    idp = await idp_factory(name="ToDelete")
    resp = await auth_client.delete(f"/identity-providers/{idp['id']}")
    assert resp.status_code == 204

@pytest.mark.asyncio
async def test_active_idps_no_auth_required(async_client: AsyncClient):
    """GET /identity-providers/active is accessible without a JWT."""
    resp = await async_client.get("/identity-providers/active")
    assert resp.status_code == 200
```

### Implementation

```python
# backend/app/routers/identity_providers.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.identity_provider import IdentityProvider, IdpStatus
from app.models.user import User
from app.routers import current_user
from app.schemas.identity_provider import (
    IdentityProviderCreate,
    IdentityProviderUpdate,
    IdentityProviderRead,
)

router = APIRouter(prefix="/identity-providers", tags=["Identity Providers"])


@router.get("/active", response_model=list[IdentityProviderRead])
async def list_active_idps(db: AsyncSession = Depends(get_db)):
    """
    Public endpoint: returns enabled, active IdPs for pre-login 'continue with' buttons.
    No JWT required. Org derived from request context is not available pre-login;
    frontend passes ?org_id= query param when multi-tenancy UI is needed.
    For now returns all active+enabled IdPs (single-org instances).
    """
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.status == IdpStatus.active,
            IdentityProvider.enabled == True,  # noqa: E712
        )
    )
    return result.scalars().all()


@router.post("", response_model=IdentityProviderRead, status_code=201)
async def create_idp(
    body: IdentityProviderCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = IdentityProvider(
        id=uuid.uuid4(),
        org_id=user.organization_id,
        type=body.type,
        name=body.name,
        config=body.config,
        connector_id=body.connector_id,
    )
    db.add(idp)
    await db.commit()
    await db.refresh(idp)
    return idp


@router.get("", response_model=list[IdentityProviderRead])
async def list_idps(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IdentityProvider).where(IdentityProvider.org_id == user.organization_id)
    )
    return result.scalars().all()


@router.get("/{idp_id}", response_model=IdentityProviderRead)
async def get_idp(
    idp_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = await _get_org_idp(db, idp_id, user.organization_id)
    return idp


@router.put("/{idp_id}", response_model=IdentityProviderRead)
async def update_idp(
    idp_id: uuid.UUID,
    body: IdentityProviderUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = await _get_org_idp(db, idp_id, user.organization_id)
    if body.name is not None:
        idp.name = body.name
    if body.config is not None:
        idp.config = body.config
    if body.enabled is not None:
        idp.enabled = body.enabled
    if body.connector_id is not None:
        idp.connector_id = body.connector_id
    await db.commit()
    await db.refresh(idp)
    return idp


@router.delete("/{idp_id}", status_code=204)
async def delete_idp(
    idp_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    idp = await _get_org_idp(db, idp_id, user.organization_id)
    await db.delete(idp)
    await db.commit()


async def _get_org_idp(db: AsyncSession, idp_id: uuid.UUID, org_id: uuid.UUID) -> IdentityProvider:
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.id == idp_id,
            IdentityProvider.org_id == org_id,
        )
    )
    idp = result.scalar_one_or_none()
    if not idp:
        raise HTTPException(status_code=404, detail="Identity provider not found")
    return idp
```

Register in `main.py`:
```python
from app.routers.identity_providers import router as identity_providers_router
app.include_router(identity_providers_router)
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/routers/identity_providers.py backend/app/main.py
git commit -m "feat(router): identity providers CRUD"
```

---

## Task 13 — Router: `org_auth_mode.py` (atomic auth mode switch)

### Files
- `backend/app/routers/org_auth_mode.py`
- `backend/app/main.py` — register

### Failing tests

```python
# backend/app/tests/test_identity_providers.py (append)

@pytest.mark.asyncio
async def test_switch_to_idp_mode_atomic(auth_client: AsyncClient, idp_factory, org):
    idp = await idp_factory(name="GoogleOIDC", enabled=True)
    resp = await auth_client.post(
        f"/orgs/{org['id']}/auth-mode",
        json={"auth_mode": "idp", "idp_id": idp["id"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_mode"] == "idp"
    # IDP should now be active
    idp_resp = await auth_client.get(f"/identity-providers/{idp['id']}")
    assert idp_resp.json()["status"] == "active"

@pytest.mark.asyncio
async def test_switch_to_idp_requires_idp_id(auth_client: AsyncClient, org):
    resp = await auth_client.post(
        f"/orgs/{org['id']}/auth-mode",
        json={"auth_mode": "idp"},
    )
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_switch_back_to_local(auth_client: AsyncClient, idp_factory, org):
    idp = await idp_factory(name="G", enabled=True)
    await auth_client.post(f"/orgs/{org['id']}/auth-mode", json={"auth_mode": "idp", "idp_id": idp["id"]})
    resp = await auth_client.post(f"/orgs/{org['id']}/auth-mode", json={"auth_mode": "local"})
    assert resp.status_code == 200
    assert resp.json()["auth_mode"] == "local"
```

### Implementation

```python
# backend/app/routers/org_auth_mode.py
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.organization import Organization, OrgAuthMode
from app.models.identity_provider import IdentityProvider, IdpStatus
from app.models.user import User, UserRole
from app.routers import require_roles
from app.schemas.identity_provider import AuthModeSwitch

router = APIRouter(prefix="/orgs", tags=["Organizations"])

_require_admin = require_roles(UserRole.admin)


@router.post("/{org_id}/auth-mode")
async def set_auth_mode(
    org_id: uuid.UUID,
    body: AuthModeSwitch,
    user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Atomically switch org auth mode.
    - local → idp: idp_id required; sets org.auth_mode=idp, idp.status=active
    - idp → local: clears active IdPs to pending (graceful rollback path)
    """
    if str(org_id) != str(user.organization_id):
        raise HTTPException(status_code=403, detail="Cannot modify a different org")

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    if body.auth_mode == "idp":
        if not body.idp_id:
            raise HTTPException(status_code=422, detail="idp_id is required when switching to idp mode")
        idp_result = await db.execute(
            select(IdentityProvider).where(
                IdentityProvider.id == body.idp_id,
                IdentityProvider.org_id == org_id,
            )
        )
        idp = idp_result.scalar_one_or_none()
        if not idp:
            raise HTTPException(status_code=404, detail="Identity provider not found")
        if not idp.enabled:
            raise HTTPException(status_code=400, detail="Identity provider must be enabled before activating")

        # Atomic: set org mode + activate IdP
        org.auth_mode = OrgAuthMode.idp
        org.auth_mode_changed_at = datetime.now(timezone.utc)
        idp.status = IdpStatus.active

    elif body.auth_mode == "local":
        org.auth_mode = OrgAuthMode.local
        org.auth_mode_changed_at = datetime.now(timezone.utc)
        # Return active IdPs to pending (so they can be re-activated after testing)
        active_idps_result = await db.execute(
            select(IdentityProvider).where(
                IdentityProvider.org_id == org_id,
                IdentityProvider.status == IdpStatus.active,
            )
        )
        for idp in active_idps_result.scalars().all():
            idp.status = IdpStatus.pending
    else:
        raise HTTPException(status_code=422, detail="auth_mode must be 'local' or 'idp'")

    await db.commit()
    await db.refresh(org)
    return {"auth_mode": org.auth_mode, "auth_mode_changed_at": org.auth_mode_changed_at}
```

Register in `main.py`:
```python
from app.routers.org_auth_mode import router as org_auth_mode_router
app.include_router(org_auth_mode_router)
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/routers/org_auth_mode.py backend/app/main.py
git commit -m "feat(router): atomic org auth mode switch"
```

---

## Task 14 — Service: `oidc_service.py`

### Files
- `backend/app/services/oidc_service.py`

### Failing test (add to `test_oidc_flow.py`)

```python
# backend/app/tests/test_oidc_flow.py
import pytest
from unittest.mock import AsyncMock, patch
from app.services.oidc_service import build_authorization_url, OidcConfig, exchange_code_for_userinfo

@pytest.mark.asyncio
async def test_build_authorization_url():
    cfg = OidcConfig(
        issuer="https://accounts.example.com",
        client_id="client123",
        client_secret="secret",
        scopes=["openid", "email", "profile"],
    )
    url = build_authorization_url(
        config=cfg,
        redirect_uri="https://app.example.com/auth/oidc/callback",
        state="random-state-123",
    )
    assert "response_type=code" in url
    assert "client_id=client123" in url
    assert "state=random-state-123" in url

@pytest.mark.asyncio
async def test_exchange_code_returns_userinfo():
    cfg = OidcConfig(
        issuer="https://accounts.example.com",
        client_id="client123",
        client_secret="secret",
        scopes=["openid", "email", "profile"],
    )
    mock_token_response = {"access_token": "tok", "id_token": "id.tok.sig"}
    mock_userinfo = {"sub": "1234", "email": "user@example.com", "name": "Test User"}

    with patch("app.services.oidc_service._fetch_token", new=AsyncMock(return_value=mock_token_response)), \
         patch("app.services.oidc_service._fetch_userinfo", new=AsyncMock(return_value=mock_userinfo)):
        info = await exchange_code_for_userinfo(
            config=cfg,
            code="auth-code-abc",
            redirect_uri="https://app.example.com/auth/oidc/callback",
        )
    assert info["email"] == "user@example.com"
```

### Implementation

```python
# backend/app/services/oidc_service.py
import urllib.parse
import httpx
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])
    auto_provision: bool = False
    icon: str | None = None


async def _get_oidc_metadata(issuer: str) -> dict[str, Any]:
    """Fetch .well-known/openid-configuration."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _fetch_token(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _fetch_userinfo(userinfo_endpoint: str, access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def build_authorization_url(
    config: OidcConfig,
    redirect_uri: str,
    state: str,
    authorization_endpoint: str | None = None,
) -> str:
    """
    Build the OIDC authorization redirect URL.
    If authorization_endpoint is None, it will be derived from {issuer}/.well-known/openid-configuration
    at call time by the router (which has async context).
    When passed directly (e.g. pre-fetched), uses that value.
    """
    if authorization_endpoint is None:
        # Construct the standard URL — callers must pre-fetch if needed
        authorization_endpoint = config.issuer.rstrip("/") + "/authorize"

    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
    }
    return f"{authorization_endpoint}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_userinfo(
    config: OidcConfig,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """
    Full authorization-code exchange: token endpoint call + userinfo call.
    Returns raw userinfo dict from the IdP.
    """
    metadata = await _get_oidc_metadata(config.issuer)
    token_response = await _fetch_token(
        token_endpoint=metadata["token_endpoint"],
        client_id=config.client_id,
        client_secret=config.client_secret,
        code=code,
        redirect_uri=redirect_uri,
    )
    userinfo = await _fetch_userinfo(
        userinfo_endpoint=metadata["userinfo_endpoint"],
        access_token=token_response["access_token"],
    )
    return userinfo
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/services/oidc_service.py backend/app/tests/test_oidc_flow.py
git commit -m "feat(service): OIDC authorization-code service"
```

---

## Task 15 — Router: `oidc.py` (OIDC login flow)

### Files
- `backend/app/routers/oidc.py`
- `backend/app/main.py` — register

### Failing tests

```python
# backend/app/tests/test_oidc_flow.py (append)
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_oidc_redirect_returns_302(async_client: AsyncClient, oidc_idp):
    """GET /auth/oidc/{idp_id}/redirect redirects to issuer authorization endpoint."""
    with patch("app.services.oidc_service._get_oidc_metadata", new=AsyncMock(return_value={
        "authorization_endpoint": "https://accounts.example.com/o/oauth2/auth",
        "token_endpoint": "https://accounts.example.com/token",
        "userinfo_endpoint": "https://accounts.example.com/userinfo",
    })):
        resp = await async_client.get(f"/auth/oidc/{oidc_idp['id']}/redirect", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "accounts.example.com" in resp.headers["location"]

@pytest.mark.asyncio
async def test_oidc_callback_creates_user(async_client: AsyncClient, db, oidc_idp):
    """Callback with valid code creates or finds user and returns JWT."""
    with patch("app.services.oidc_service.exchange_code_for_userinfo", new=AsyncMock(return_value={
        "sub": "google-uid-999",
        "email": "newuser@example.com",
        "name": "New User",
    })), patch("app.routers.oidc._verify_state", return_value=True):
        resp = await async_client.get(
            f"/auth/oidc/{oidc_idp['id']}/callback",
            params={"code": "fake-code", "state": "valid-state"},
        )
    assert resp.status_code == 200
    assert "access_token" in resp.json()

@pytest.mark.asyncio
async def test_oidc_callback_invalid_state_rejected(async_client: AsyncClient, oidc_idp):
    with patch("app.routers.oidc._verify_state", return_value=False):
        resp = await async_client.get(
            f"/auth/oidc/{oidc_idp['id']}/callback",
            params={"code": "x", "state": "tampered"},
        )
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_oidc_disabled_idp_rejected(async_client: AsyncClient, disabled_oidc_idp):
    resp = await async_client.get(f"/auth/oidc/{disabled_oidc_idp['id']}/redirect", follow_redirects=False)
    assert resp.status_code == 404
```

### Implementation

```python
# backend/app/routers/oidc.py
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.identity_provider import IdentityProvider, IdpType, IdpStatus
from app.models.user import User, UserRole
from app.models.organization import Organization
from app.schemas.auth import Token
from app.services.auth_service import create_access_token, hash_password
from app.services.oidc_service import OidcConfig, build_authorization_url, exchange_code_for_userinfo, _get_oidc_metadata

router = APIRouter(prefix="/auth/oidc", tags=["OIDC"])

# In-memory state store (suitable for single-instance; swap for Redis in multi-instance deployments)
_pending_states: dict[str, dict] = {}


def _generate_state(idp_id: uuid.UUID) -> str:
    state = secrets.token_urlsafe(32)
    _pending_states[state] = {
        "idp_id": str(idp_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return state


def _verify_state(state: str, idp_id: uuid.UUID) -> bool:
    entry = _pending_states.pop(state, None)
    if not entry:
        return False
    return entry["idp_id"] == str(idp_id)


async def _get_active_oidc_idp(db: AsyncSession, idp_id: uuid.UUID) -> IdentityProvider:
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.id == idp_id,
            IdentityProvider.type == IdpType.oidc,
            IdentityProvider.enabled == True,  # noqa: E712
        )
    )
    idp = result.scalar_one_or_none()
    if not idp:
        raise HTTPException(status_code=404, detail="OIDC identity provider not found or not enabled")
    return idp


@router.get("/{idp_id}/redirect")
async def oidc_redirect(
    idp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Redirect user's browser to the OIDC provider's authorization endpoint."""
    idp = await _get_active_oidc_idp(db, idp_id)
    cfg = OidcConfig(**{k: idp.config[k] for k in ("issuer", "client_id", "client_secret", "scopes") if k in idp.config})

    metadata = await _get_oidc_metadata(cfg.issuer)
    state = _generate_state(idp_id)

    # Build redirect_uri using issuer's instance URL convention — in production,
    # read from settings.INSTANCE_URL; fall back to a relative path the frontend proxies.
    redirect_uri = f"/auth/oidc/{idp_id}/callback"

    url = build_authorization_url(
        config=cfg,
        redirect_uri=redirect_uri,
        state=state,
        authorization_endpoint=metadata["authorization_endpoint"],
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/{idp_id}/callback", response_model=Token)
async def oidc_callback(
    idp_id: uuid.UUID,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Exchange authorization code for user info; match or create user; return JWT."""
    if not _verify_state(state, idp_id):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    idp = await _get_active_oidc_idp(db, idp_id)
    cfg = OidcConfig(**{k: idp.config[k] for k in ("issuer", "client_id", "client_secret", "scopes") if k in idp.config})
    cfg.auto_provision = idp.config.get("auto_provision", False)

    redirect_uri = f"/auth/oidc/{idp_id}/callback"

    try:
        userinfo = await exchange_code_for_userinfo(cfg, code, redirect_uri)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OIDC token exchange failed: {exc}")

    email = userinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in OIDC userinfo")

    # Match existing user by email within the IdP's org
    result = await db.execute(
        select(User).where(
            User.email == email,
            User.organization_id == idp.org_id,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        if not cfg.auto_provision:
            raise HTTPException(
                status_code=403,
                detail="No local account found for this email. Contact your admin.",
            )
        # Auto-provision: create user with a random unusable password
        user = User(
            id=uuid.uuid4(),
            organization_id=idp.org_id,
            email=email,
            name=userinfo.get("name", email),
            role=UserRole.security_operator,  # default role for IdP-provisioned users
            hashed_password=hash_password(secrets.token_urlsafe(32)),  # random, login only via IdP
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(str(user.id))
    return Token(access_token=token)
```

Register in `main.py`:
```python
from app.routers.oidc import router as oidc_router
app.include_router(oidc_router)
```

### - [ ] Confirm tests pass, commit
```
git add backend/app/routers/oidc.py backend/app/main.py
git commit -m "feat(router): OIDC authorization-code login flow"
```

---

## Task 16 — Wire everything together: `main.py` final state

At this point `main.py` accumulates five new registrations. Verify the final import block:

```python
from app.routers.setup import router as setup_router
from app.routers.identity_providers import router as identity_providers_router
from app.routers.org_auth_mode import router as org_auth_mode_router
from app.routers.oidc import router as oidc_router
from app.middleware.setup_guard import SetupGuardMiddleware

# ... (after app = FastAPI(...)):
app.add_middleware(SetupGuardMiddleware)

# ... (after existing include_router calls):
app.include_router(setup_router)
app.include_router(identity_providers_router)
app.include_router(org_auth_mode_router)
app.include_router(oidc_router)
```

### - [ ] Run full test suite, confirm no regressions
```
cd backend && python -m pytest app/tests/ -x -q
```

### - [ ] Commit wiring cleanup if any
```
git add backend/app/main.py
git commit -m "chore: wire SP3 routers and middleware into main.py"
```

---

## Task 17 — Add `hash_password` utility to `auth_service.py`

The setup router and OIDC callback both call `hash_password`. Confirm it exists in `app/services/auth_service.py`. If absent, add:

```python
# Add to backend/app/services/auth_service.py
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)
```

### - [ ] Confirm `authenticate_user` still uses `_pwd_context.verify`, commit
```
git add backend/app/services/auth_service.py
git commit -m "feat(service): expose hash_password utility"
```

---

## Task 18 — `config.py`: add `OPS_INSTANCE_URL` setting

The OIDC callback redirect_uri should use the instance's own URL in production:

```python
# Add to Settings class in backend/app/config.py:
    INSTANCE_URL: str = "http://localhost:8000"
```

Update `oidc.py` to use `settings.INSTANCE_URL`:
```python
from app.config import settings
redirect_uri = f"{settings.INSTANCE_URL.rstrip('/')}/auth/oidc/{idp_id}/callback"
```

### - [ ] Confirm tests still pass, commit
```
git add backend/app/config.py backend/app/routers/oidc.py
git commit -m "feat(config): add INSTANCE_URL setting for OIDC redirect_uri"
```

---

## Task 19 — Smoke test phase: `IDP_OIDC`

> **Note:** Per project principle, unit test green is not done. A live smoke phase must pass. The smoke phase lives in `nexplane-deploy/smoke/` and runs against a real provisioned instance with a real OIDC provider. This task specifies what the smoke phase must cover; implementation of the smoke runner itself is part of SP2/nexplane-deploy.

### Smoke phase spec: `IDP_OIDC`

**Infrastructure:** Use a generic OIDC provider (Dex IdP or a test Okta tenant) reachable from the CI ops instance. Configure one client application per smoke run.

**Phases to assert:**

1. **Setup:** POST `/identity-providers` with Dex issuer config → status 201, status=pending.
2. **Pre-activation test:** Attempt login via OIDC redirect before auth mode switch → IdP not active, expect 404 on redirect.
3. **Enable + activate:** PUT `/identity-providers/{id}` `enabled=true`, then POST `/orgs/{id}/auth-mode` `{auth_mode: "idp", idp_id: ...}` → org.auth_mode=idp, idp.status=active.
4. **Login flow:** Hit `GET /auth/oidc/{id}/redirect` with a headless browser (or direct HTTP if Dex supports `?prompt=none`) → follow redirect chain → callback returns JWT.
5. **Token validity:** Use returned JWT to call `GET /auth/me` → 200 with correct email.
6. **Rollback:** POST `/orgs/{id}/auth-mode` `{auth_mode: "local"}` → 200, verify GET `/auth/login` with original local admin creds still works.
7. **Cleanup:** DELETE `/identity-providers/{id}`.

**Done criterion:** All 7 phases green on a live provisioned instance.

---

## Task 20 — Final checklist before marking SP3 complete

- [ ] All three Alembic migrations (074, 075, 076) run cleanly on a fresh DB (`alembic upgrade head`)
- [ ] All migrations have working `downgrade()` functions
- [ ] `pytest backend/app/tests/test_setup_flow.py` — all assertions pass
- [ ] `pytest backend/app/tests/test_identity_providers.py` — all assertions pass
- [ ] `pytest backend/app/tests/test_oidc_flow.py` — all assertions pass
- [ ] Full test suite: `pytest backend/app/tests/ -x -q` — no regressions vs. pre-SP3 baseline
- [ ] `NEXPLANE_EDITION=core python -c "from app.main import app"` — app starts without error, setup routes return 404
- [ ] `NEXPLANE_EDITION=commercial python -c "from app.main import app"` — app starts, setup routes return correct responses
- [ ] Live smoke phase `IDP_OIDC` passes on CI ops instance (SP2/nexplane-deploy prerequisite)
- [ ] PR opened targeting `master` with all commits from Tasks 1–19

---

## Commit sequence summary

| Task | Commit message |
|------|----------------|
| 1 | `feat(migration): 074 add setup_tokens table` |
| 2 | `feat(migration): 075 add identity_providers table` |
| 3 | `feat(migration): 076 add org auth_mode column` |
| 4 | `feat(model): SetupToken model` |
| 5 | `feat(model): IdentityProvider model` |
| 6 | `feat(model): add auth_mode to Organization` |
| 7 | `feat(dependency): ops shared secret validation` |
| 8+9 | `feat(schemas): setup flow and identity provider schemas` |
| 10 | `feat(router): setup consume and token endpoints` |
| 11 | `feat(middleware): setup guard redirect for unconfigured commercial instances` |
| 12 | `feat(router): identity providers CRUD` |
| 13 | `feat(router): atomic org auth mode switch` |
| 14 | `feat(service): OIDC authorization-code service` |
| 15 | `feat(router): OIDC authorization-code login flow` |
| 16 | `chore: wire SP3 routers and middleware into main.py` |
| 17 | `feat(service): expose hash_password utility` |
| 18 | `feat(config): add INSTANCE_URL setting for OIDC redirect_uri` |
