# MCP Agent-Scoped Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `AgentToken` model for narrow-scoped AI agent authorization, extend MCP auth to resolve either token type, add scope enforcement, add `submit_for_approval` and `get_execution_progress` MCP tools, expose CRUD endpoints, and add a Settings UI tab.

**Architecture:** `AgentToken` is a new model alongside `ApiToken`. MCP auth layer resolves token type: `ApiToken` → full user RBAC; `AgentToken` → scope-intersection enforcement. New endpoint `POST /auth/agent-tokens` (admin only). Two new MCP tools in `change_requests.py`. React UI tab mirrors existing `ApiTokenManager.tsx`.

**Tech Stack:** FastAPI, SQLAlchemy async, SHA-256 hashing, React, JSONB columns (PostgreSQL)

---

## File Map

| File | Action |
|------|--------|
| `backend/app/models/agent_token.py` | New AgentToken SQLAlchemy model |
| `backend/alembic/versions/061_agent_tokens.py` | New Alembic migration |
| `backend/app/schemas/agent_token.py` | Pydantic schemas for create/response |
| `backend/app/routers/auth.py` | Add POST/GET/DELETE `/auth/agent-tokens` |
| `backend/app/mcp_server.py` | Extend `resolve_mcp_token` to return AgentToken |
| `backend/app/mcp_tools/context.py` | Add `_enforce_agent_scope` helper |
| `backend/app/mcp_tools/change_requests.py` | Add `submit_for_approval`, `get_execution_progress` tools |
| `frontend/src/components/settings/AgentTokenManager.tsx` | New UI component |
| `frontend/src/pages/Settings.tsx` | Add Agent Tokens tab |
| `backend/tests/test_agent_tokens.py` | Unit tests |

---

### Task 1: `AgentToken` model + migration

**Files:**
- Create: `backend/app/models/agent_token.py`
- Create: `backend/alembic/versions/061_agent_tokens.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_tokens.py
import pytest
from app.models.agent_token import AgentToken


def test_agent_token_model_exists():
    token = AgentToken()
    assert hasattr(token, "id")
    assert hasattr(token, "token_hash")
    assert hasattr(token, "allowed_connector_types")
    assert hasattr(token, "allowed_asset_tags")
    assert hasattr(token, "allowed_cr_types")
    assert hasattr(token, "allowed_roles")
    assert hasattr(token, "revoked")
    assert hasattr(token, "expires_at")
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py::test_agent_token_model_exists -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create the model**

```python
# backend/app/models/agent_token.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AgentToken(Base):
    __tablename__ = "agent_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Scope constraints — empty list = unrestricted for that dimension
    allowed_connector_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    allowed_asset_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    allowed_cr_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    allowed_roles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
```

- [ ] **Step 4: Create migration**

```python
# backend/alembic/versions/061_agent_tokens.py
"""add agent_tokens table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-26
"""
from typing import Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean, default=False, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("allowed_connector_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_asset_tags", JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_cr_types", JSONB, nullable=False, server_default="[]"),
        sa.Column("allowed_roles", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("ix_agent_tokens_token_hash", "agent_tokens", ["token_hash"])
    op.create_index("ix_agent_tokens_organization_id", "agent_tokens", ["organization_id"])


def downgrade() -> None:
    op.drop_table("agent_tokens")
```

- [ ] **Step 5: Run test to see it pass**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py::test_agent_token_model_exists -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/agent_token.py backend/alembic/versions/061_agent_tokens.py backend/tests/test_agent_tokens.py
git commit -m "feat: AgentToken model and migration"
```

---

### Task 2: Pydantic schemas

**Files:**
- Create: `backend/app/schemas/agent_token.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_agent_tokens.py`:

```python
def test_agent_token_schemas():
    from app.schemas.agent_token import AgentTokenCreate, AgentTokenResponse
    import uuid

    create = AgentTokenCreate(
        name="Claude Code — staging",
        expires_in_days=90,
        allowed_connector_types=["aws"],
        allowed_asset_tags=["env:staging"],
        allowed_cr_types=["patch_packages"],
        allowed_roles=["read", "write"],
    )
    assert create.name == "Claude Code — staging"
    assert create.expires_in_days == 90

    resp = AgentTokenResponse(
        id=uuid.uuid4(),
        name="test",
        created_at=__import__("datetime").datetime.utcnow(),
        expires_at=None,
        revoked=False,
        last_used_at=None,
        allowed_connector_types=[],
        allowed_asset_tags=[],
        allowed_cr_types=[],
        allowed_roles=["read"],
    )
    assert resp.revoked is False
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py::test_agent_token_schemas -v
```

- [ ] **Step 3: Create schemas**

```python
# backend/app/schemas/agent_token.py
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class AgentTokenCreate(BaseModel):
    name: str = Field(..., max_length=255)
    expires_in_days: Optional[int] = None
    allowed_connector_types: list[str] = Field(default_factory=list)
    allowed_asset_tags: list[str] = Field(default_factory=list)
    allowed_cr_types: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)


class AgentTokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    expires_at: Optional[datetime]
    revoked: bool
    last_used_at: Optional[datetime]
    allowed_connector_types: list[str]
    allowed_asset_tags: list[str]
    allowed_cr_types: list[str]
    allowed_roles: list[str]

    class Config:
        from_attributes = True


class AgentTokenCreateResponse(AgentTokenResponse):
    token: str  # Raw token — shown once only
```

- [ ] **Step 4: Run test to see it pass**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py::test_agent_token_schemas -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/agent_token.py backend/tests/test_agent_tokens.py
git commit -m "feat: AgentToken Pydantic schemas"
```

---

### Task 3: API endpoints `POST/GET/DELETE /auth/agent-tokens`

**Files:**
- Modify: `backend/app/routers/auth.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_agent_tokens.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_create_agent_token_returns_raw_token(client):
    with patch("app.routers.auth.get_current_admin_user") as mock_admin, \
         patch("app.routers.auth._create_agent_token_in_db") as mock_create:
        mock_admin.return_value = MagicMock(id="user-1", organization_id="org-1")
        import uuid
        from datetime import datetime
        mock_token_record = MagicMock()
        mock_token_record.id = uuid.uuid4()
        mock_token_record.name = "test"
        mock_token_record.created_at = datetime.utcnow()
        mock_token_record.expires_at = None
        mock_token_record.revoked = False
        mock_token_record.last_used_at = None
        mock_token_record.allowed_connector_types = []
        mock_token_record.allowed_asset_tags = []
        mock_token_record.allowed_cr_types = []
        mock_token_record.allowed_roles = ["read"]
        mock_create.return_value = (mock_token_record, "raw-token-value")

        resp = client.post("/auth/agent-tokens", json={
            "name": "test",
            "allowed_roles": ["read"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "token" in data
        assert data["token"] == "raw-token-value"
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py::test_create_agent_token_returns_raw_token -v
```

- [ ] **Step 3: Add endpoints to `auth.py`**

In `backend/app/routers/auth.py`, add after existing ApiToken endpoints:

```python
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

from app.models.agent_token import AgentToken
from app.schemas.agent_token import AgentTokenCreate, AgentTokenCreateResponse, AgentTokenResponse


async def _create_agent_token_in_db(db, user, payload: AgentTokenCreate) -> tuple:
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = None
    if payload.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
    record = AgentToken(
        organization_id=user.organization_id,
        created_by_user_id=user.id,
        name=payload.name,
        token_hash=token_hash,
        expires_at=expires_at,
        allowed_connector_types=payload.allowed_connector_types,
        allowed_asset_tags=payload.allowed_asset_tags,
        allowed_cr_types=payload.allowed_cr_types,
        allowed_roles=payload.allowed_roles,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record, raw


@router.post("/auth/agent-tokens", response_model=AgentTokenCreateResponse, status_code=201)
async def create_agent_token(
    payload: AgentTokenCreate,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    record, raw_token = await _create_agent_token_in_db(db, current_user, payload)
    return AgentTokenCreateResponse(
        token=raw_token,
        **AgentTokenResponse.model_validate(record).model_dump(),
    )


@router.get("/auth/agent-tokens", response_model=list[AgentTokenResponse])
async def list_agent_tokens(
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(
        select(AgentToken).where(AgentToken.organization_id == current_user.organization_id)
    )
    return result.scalars().all()


@router.delete("/auth/agent-tokens/{token_id}", status_code=204)
async def revoke_agent_token(
    token_id: uuid.UUID,
    current_user=Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.id == token_id,
            AgentToken.organization_id == current_user.organization_id,
        )
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Agent token not found")
    token.revoked = True
    await db.commit()
```

- [ ] **Step 4: Run test to see it pass**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py::test_create_agent_token_returns_raw_token -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/auth.py backend/tests/test_agent_tokens.py
git commit -m "feat: POST/GET/DELETE /auth/agent-tokens endpoints"
```

---

### Task 4: Extend `resolve_mcp_token` + `_enforce_agent_scope`

**Files:**
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/app/mcp_tools/context.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_agent_tokens.py`:

```python
@pytest.mark.asyncio
async def test_resolve_mcp_token_returns_agent_token():
    from app.mcp_server import resolve_mcp_token
    from app.models.agent_token import AgentToken
    import hashlib
    raw = "test-raw-token-xyz"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    mock_agent_token = MagicMock(spec=AgentToken)
    mock_agent_token.revoked = False
    mock_agent_token.expires_at = None

    db = AsyncMock()
    with patch("app.mcp_server._lookup_api_token", return_value=None), \
         patch("app.mcp_server._lookup_agent_token", return_value=mock_agent_token):
        user, agent_token = await resolve_mcp_token(raw, db)
    assert user is None
    assert agent_token is mock_agent_token


def test_enforce_agent_scope_passes_for_allowed():
    from app.mcp_tools.context import _enforce_agent_scope
    agent_token = MagicMock()
    agent_token.allowed_roles = ["read", "write"]
    agent_token.allowed_connector_types = ["aws"]
    agent_token.allowed_asset_tags = []
    agent_token.allowed_cr_types = ["patch_packages"]

    # Should not raise
    _enforce_agent_scope(agent_token, connector_type="aws", cr_type="patch_packages", required_role="write")


def test_enforce_agent_scope_blocks_wrong_connector():
    from app.mcp_tools.context import _enforce_agent_scope
    from fastapi import HTTPException
    agent_token = MagicMock()
    agent_token.allowed_roles = ["read", "write"]
    agent_token.allowed_connector_types = ["aws"]
    agent_token.allowed_asset_tags = []
    agent_token.allowed_cr_types = []

    with pytest.raises(HTTPException) as exc_info:
        _enforce_agent_scope(agent_token, connector_type="ssh", required_role="write")
    assert exc_info.value.status_code == 403


def test_enforce_agent_scope_blocks_missing_role():
    from app.mcp_tools.context import _enforce_agent_scope
    from fastapi import HTTPException
    agent_token = MagicMock()
    agent_token.allowed_roles = ["read"]
    agent_token.allowed_connector_types = []
    agent_token.allowed_asset_tags = []
    agent_token.allowed_cr_types = []

    with pytest.raises(HTTPException) as exc_info:
        _enforce_agent_scope(agent_token, required_role="write")
    assert exc_info.value.status_code == 403


def test_enforce_agent_scope_none_token_passes():
    from app.mcp_tools.context import _enforce_agent_scope
    # None = ApiToken path — no scope enforcement
    _enforce_agent_scope(None, connector_type="ssh", required_role="approve")
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py -k "resolve_mcp or enforce_agent" -v
```

- [ ] **Step 3: Add `_lookup_agent_token` to `mcp_server.py`**

Find `resolve_mcp_token` in `backend/app/mcp_server.py`. Extend it:

```python
async def _lookup_agent_token(db, token_hash: str):
    from app.models.agent_token import AgentToken
    from sqlalchemy import select
    from datetime import datetime, timezone

    result = await db.execute(
        select(AgentToken).where(AgentToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()
    if not token:
        return None
    if token.revoked:
        return None
    if token.expires_at and token.expires_at < datetime.now(timezone.utc):
        return None
    return token


async def resolve_mcp_token(raw_token: str, db) -> tuple:
    """Returns (user, agent_token). Exactly one is non-None. Raises 401 if invalid."""
    import hashlib
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    api_token = await _lookup_api_token(db, token_hash)
    if api_token:
        user = await _get_user(db, api_token.user_id)
        return user, None

    agent_token = await _lookup_agent_token(db, token_hash)
    if agent_token:
        return None, agent_token

    raise HTTPException(status_code=401, detail="Invalid or revoked token")
```

- [ ] **Step 4: Add `_enforce_agent_scope` to `context.py`**

Open `backend/app/mcp_tools/context.py`. Add:

```python
def _enforce_agent_scope(
    agent_token,
    *,
    connector_type: str | None = None,
    asset_tags: list[str] | None = None,
    cr_type: str | None = None,
    required_role: str = "read",
) -> None:
    """Enforce AgentToken scope constraints. No-op if agent_token is None (ApiToken path)."""
    if agent_token is None:
        return
    from fastapi import HTTPException
    if required_role not in agent_token.allowed_roles:
        raise HTTPException(403, f"Agent token does not have '{required_role}' permission")
    if connector_type and agent_token.allowed_connector_types:
        if connector_type not in agent_token.allowed_connector_types:
            raise HTTPException(403, f"Agent token not authorized for connector type '{connector_type}'")
    if asset_tags and agent_token.allowed_asset_tags:
        if not any(t in agent_token.allowed_asset_tags for t in asset_tags):
            raise HTTPException(403, "Agent token not authorized for any of the asset's tags")
    if cr_type and agent_token.allowed_cr_types:
        if cr_type not in agent_token.allowed_cr_types:
            raise HTTPException(403, f"Agent token not authorized for CR type '{cr_type}'")
```

- [ ] **Step 5: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py -k "resolve_mcp or enforce_agent" -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/mcp_server.py backend/app/mcp_tools/context.py backend/tests/test_agent_tokens.py
git commit -m "feat: extend resolve_mcp_token for AgentToken + _enforce_agent_scope"
```

---

### Task 5: `submit_for_approval` and `get_execution_progress` MCP tools

**Files:**
- Modify: `backend/app/mcp_tools/change_requests.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_agent_tokens.py`:

```python
@pytest.mark.asyncio
async def test_submit_for_approval_calls_submit_endpoint():
    from app.mcp_tools.change_requests import submit_for_approval
    import uuid
    cr_id = str(uuid.uuid4())

    with patch("app.mcp_tools.change_requests._get_cr_or_404") as mock_get, \
         patch("app.mcp_tools.change_requests._submit_cr") as mock_submit, \
         patch("app.mcp_tools.change_requests.resolve_mcp_token", return_value=(MagicMock(), None)):

        mock_cr = MagicMock()
        mock_cr.status = "draft"
        mock_get.return_value = mock_cr
        mock_submit.return_value = {"status": "awaiting_approval"}

        result = await submit_for_approval(token="tok", cr_id=cr_id)
    assert result["status"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_get_execution_progress_returns_step_data():
    from app.mcp_tools.change_requests import get_execution_progress
    import uuid
    cr_id = str(uuid.uuid4())

    with patch("app.mcp_tools.change_requests._get_cr_or_404") as mock_get, \
         patch("app.mcp_tools.change_requests.resolve_mcp_token", return_value=(MagicMock(), None)):

        mock_cr = MagicMock()
        mock_cr.status = "executing"
        mock_cr.execution_result = {
            "completed_steps": 2,
            "total_steps": 5,
            "current_step": "patch_packages",
            "errors": [],
        }
        mock_get.return_value = mock_cr

        result = await get_execution_progress(token="tok", cr_id=cr_id)
    assert result["completed_steps"] == 2
    assert result["total_steps"] == 5
    assert result["percent_complete"] == 40
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py -k "submit_for_approval or get_execution_progress" -v
```

- [ ] **Step 3: Add tools to `change_requests.py`**

Open `backend/app/mcp_tools/change_requests.py`. Add after the existing tools:

```python
@mcp.tool()
async def submit_for_approval(token: str, cr_id: str) -> dict:
    """
    Move a CR from Draft to Awaiting Approval so approvers are notified.
    Use after create_change_request and reviewing get_change_request_plan.
    Returns updated CR status.
    """
    from app.mcp_tools.context import get_db_and_auth
    db, user, agent_token = await get_db_and_auth(token)
    from app.mcp_tools.context import _enforce_agent_scope
    _enforce_agent_scope(agent_token, required_role="write")

    cr = await _get_cr_or_404(db, cr_id)
    result = await _submit_cr(db, cr)
    return result


@mcp.tool()
async def get_execution_progress(token: str, cr_id: str) -> dict:
    """
    Poll execution progress for a CR currently in Executing state.
    Returns completed_steps, total_steps, current_step, percent_complete, and any error messages.
    Call repeatedly until status is 'completed' or 'failed'.
    """
    from app.mcp_tools.context import get_db_and_auth
    db, user, agent_token = await get_db_and_auth(token)
    from app.mcp_tools.context import _enforce_agent_scope
    _enforce_agent_scope(agent_token, required_role="read")

    cr = await _get_cr_or_404(db, cr_id)
    exec_result = cr.execution_result or {}
    completed = exec_result.get("completed_steps", 0)
    total = exec_result.get("total_steps", 0)
    percent = int(completed / total * 100) if total else 0
    return {
        "cr_id": cr_id,
        "status": cr.status,
        "completed_steps": completed,
        "total_steps": total,
        "current_step": exec_result.get("current_step"),
        "percent_complete": percent,
        "errors": exec_result.get("errors", []),
    }
```

- [ ] **Step 4: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_agent_tokens.py -k "submit_for_approval or get_execution_progress" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_tools/change_requests.py backend/tests/test_agent_tokens.py
git commit -m "feat: submit_for_approval and get_execution_progress MCP tools"
```

---

### Task 6: React UI — `AgentTokenManager.tsx` + Settings tab

**Files:**
- Create: `frontend/src/components/settings/AgentTokenManager.tsx`
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Read existing `ApiTokenManager.tsx` for patterns**

```bash
cat frontend/src/components/settings/ApiTokenManager.tsx
```

Note the pattern for listing tokens, revoke button, and generate modal — mirror it exactly.

- [ ] **Step 2: Create `AgentTokenManager.tsx`**

```tsx
// frontend/src/components/settings/AgentTokenManager.tsx
import React, { useEffect, useState } from "react";
import { api } from "../../lib/api";

interface AgentToken {
  id: string;
  name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
  allowed_connector_types: string[];
  allowed_asset_tags: string[];
  allowed_cr_types: string[];
  allowed_roles: string[];
}

interface CreatePayload {
  name: string;
  expires_in_days: number | null;
  allowed_connector_types: string[];
  allowed_asset_tags: string[];
  allowed_cr_types: string[];
  allowed_roles: string[];
}

export function AgentTokenManager() {
  const [tokens, setTokens] = useState<AgentToken[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [form, setForm] = useState<CreatePayload>({
    name: "",
    expires_in_days: 90,
    allowed_connector_types: [],
    allowed_asset_tags: [],
    allowed_cr_types: [],
    allowed_roles: ["read"],
  });

  const load = async () => {
    const data = await api.get("/auth/agent-tokens");
    setTokens(data);
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    const result = await api.post("/auth/agent-tokens", form);
    setNewToken(result.token);
    setShowModal(false);
    load();
  };

  const handleRevoke = async (id: string) => {
    await api.delete(`/auth/agent-tokens/${id}`);
    load();
  };

  const scopeChips = (labels: string[]) =>
    labels.length === 0 ? <span className="text-gray-400 text-xs">all</span> : labels.map(l => (
      <span key={l} className="inline-block bg-gray-100 text-gray-700 text-xs px-2 py-0.5 rounded mr-1">{l}</span>
    ));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium">Agent Tokens</h3>
        <button
          onClick={() => setShowModal(true)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          Generate Agent Token
        </button>
      </div>

      {newToken && (
        <div className="bg-yellow-50 border border-yellow-300 rounded p-3 text-sm">
          <p className="font-medium text-yellow-800 mb-1">Token generated — copy it now. It will not be shown again.</p>
          <code className="break-all font-mono text-yellow-900">{newToken}</code>
          <button onClick={() => setNewToken(null)} className="ml-4 text-yellow-700 underline text-xs">Dismiss</button>
        </div>
      )}

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            <th className="pb-2">Name</th>
            <th className="pb-2">Connector Types</th>
            <th className="pb-2">CR Types</th>
            <th className="pb-2">Roles</th>
            <th className="pb-2">Expires</th>
            <th className="pb-2">Last Used</th>
            <th className="pb-2"></th>
          </tr>
        </thead>
        <tbody>
          {tokens.filter(t => !t.revoked).map(token => (
            <tr key={token.id} className="border-b last:border-0">
              <td className="py-2 font-medium">{token.name}</td>
              <td className="py-2">{scopeChips(token.allowed_connector_types)}</td>
              <td className="py-2">{scopeChips(token.allowed_cr_types)}</td>
              <td className="py-2">{scopeChips(token.allowed_roles)}</td>
              <td className="py-2 text-gray-500">{token.expires_at ? new Date(token.expires_at).toLocaleDateString() : "Never"}</td>
              <td className="py-2 text-gray-500">{token.last_used_at ? new Date(token.last_used_at).toLocaleDateString() : "Never"}</td>
              <td className="py-2">
                <button
                  onClick={() => handleRevoke(token.id)}
                  className="text-red-600 hover:text-red-800 text-xs"
                >
                  Revoke
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {showModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md space-y-4">
            <h4 className="text-lg font-semibold">Generate Agent Token</h4>

            <div>
              <label className="block text-sm font-medium mb-1">Name</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Claude Code — staging patch only"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Expires in (days, blank = never)</label>
              <input
                type="number"
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.expires_in_days ?? ""}
                onChange={e => setForm(f => ({ ...f, expires_in_days: e.target.value ? Number(e.target.value) : null }))}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Allowed Roles (comma-separated)</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.allowed_roles.join(", ")}
                onChange={e => setForm(f => ({ ...f, allowed_roles: e.target.value.split(",").map(s => s.trim()).filter(Boolean) }))}
                placeholder="read, write"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Allowed CR Types (comma-separated, blank = all)</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.allowed_cr_types.join(", ")}
                onChange={e => setForm(f => ({ ...f, allowed_cr_types: e.target.value.split(",").map(s => s.trim()).filter(Boolean) }))}
                placeholder="patch_packages, ssm_command"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Allowed Connector Types (comma-separated, blank = all)</label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.allowed_connector_types.join(", ")}
                onChange={e => setForm(f => ({ ...f, allowed_connector_types: e.target.value.split(",").map(s => s.trim()).filter(Boolean) }))}
                placeholder="aws, ssh"
              />
            </div>

            <div className="flex justify-end gap-2">
              <button onClick={() => setShowModal(false)} className="px-3 py-1.5 text-sm border rounded hover:bg-gray-50">Cancel</button>
              <button onClick={handleCreate} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700" disabled={!form.name}>
                Generate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add Agent Tokens tab to Settings**

Open `frontend/src/pages/Settings.tsx`. Find where the API Tokens tab is defined. Add a new tab for Agent Tokens:

```tsx
// In the tabs array/list, add:
{ id: "agent-tokens", label: "Agent Tokens" }

// In the tab content switch/conditional, add:
{activeTab === "agent-tokens" && <AgentTokenManager />}
```

Import at top of file:
```tsx
import { AgentTokenManager } from "../components/settings/AgentTokenManager";
```

- [ ] **Step 4: Restart frontend and verify in browser**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Navigate to Settings → Agent Tokens tab. Verify the table renders, generate button opens modal, token appears after creation.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/settings/AgentTokenManager.tsx frontend/src/pages/Settings.tsx
git commit -m "feat: AgentTokenManager UI component and Settings tab"
```

---

### Task 7: Push, apply migration, smoke verify

- [ ] **Step 1: Push all changes**

```bash
git push origin master
```

- [ ] **Step 2: Apply migration on EC2**

```bash
# On EC2:
cd /home/ec2-user/nexplane && git pull
docker exec nexplane-backend-1 alembic upgrade head
docker restart nexplane-backend-1
```

- [ ] **Step 3: Run unit tests in container**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_agent_tokens.py -v
```

Expected: All pass

- [ ] **Step 4: Smoke — create an agent token via API**

```bash
ADMIN_TOKEN="<get from platform>"
curl -s -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke-test","allowed_roles":["read","write"],"allowed_cr_types":["patch_packages"]}' \
  http://localhost:8000/auth/agent-tokens | python -m json.tool
```

Expected: Response contains `token` field and agent token record.

- [ ] **Step 5: Smoke — use agent token for MCP read (allowed)**

```bash
AGENT_TOKEN="<token from step 4>"
curl -s -H "Authorization: Bearer $AGENT_TOKEN" \
  http://localhost:8000/mcp/list_change_types | python -m json.tool
```

Expected: 200 with change type list.

- [ ] **Step 6: Smoke — verify scope blocks disallowed cr_type**

Use an MCP tool that creates a CR with `ssm_command` (not in `allowed_cr_types`). Expected: 403 response.

- [ ] **Step 7: Smoke — revoke token**

```bash
TOKEN_ID="<id from step 4>"
curl -s -X DELETE \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8000/auth/agent-tokens/$TOKEN_ID
```

Retry MCP call with same agent token. Expected: 401.
