# AI Project Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AI planning assistant to Projects — a per-org Anthropic API key stored encrypted, a conversational chat endpoint that calls Claude with project context, structured CR proposal output parsed from the response, and a collapsible AI panel on the ProjectDetail page.

**Architecture:** `SecretsService` wraps Fernet encryption as an abstraction for future HSM/Vault swap-out. `OrganizationSettings` stores the encrypted key (one row per org). `AIService` builds prompts from project goal + asset context, calls Claude, and parses `<nexplane-proposal>` blocks from responses. A new `POST /projects/{id}/ai/chat` endpoint orchestrates key retrieval → AI call → conversation storage. The frontend adds a `Settings` page for key management and an `AIPanel` component on `ProjectDetail`.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / `anthropic` SDK / `cryptography` (Fernet) — React 18 / TypeScript / TanStack Query / Tailwind

**Test commands:**
- Backend: `docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short"`
- Run migrations: `docker compose exec backend alembic upgrade head`
- Rebuild backend: `docker compose up --build -d backend`
- Frontend hot-reloads at http://localhost:3000

---

### Task 1: Add dependencies + SecretsService

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/config.py`
- Create: `backend/app/services/secrets_service.py`
- Create: `backend/app/tests/test_secrets_service.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_secrets_service.py`:
```python
import pytest
from app.services.secrets_service import SecretsService


def test_encrypt_decrypt_roundtrip():
    svc = SecretsService("test-secret-key-32-chars-minimum!")
    original = "sk-ant-api-key-test-value"
    encrypted = svc.encrypt(original)
    assert encrypted != original
    assert svc.decrypt(encrypted) == original


def test_encrypt_produces_different_ciphertext_each_time():
    svc = SecretsService("test-secret-key-32-chars-minimum!")
    v1 = svc.encrypt("same-value")
    v2 = svc.encrypt("same-value")
    # Fernet uses random IV — same plaintext → different ciphertext
    assert v1 != v2
    # But both decrypt correctly
    assert svc.decrypt(v1) == "same-value"
    assert svc.decrypt(v2) == "same-value"


def test_wrong_key_raises_on_decrypt():
    svc1 = SecretsService("test-secret-key-32-chars-minimum!")
    svc2 = SecretsService("different-key-32-chars-min-pad!!")
    encrypted = svc1.encrypt("secret")
    with pytest.raises(Exception):
        svc2.decrypt(encrypted)
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_secrets_service.py -v" 2>&1 | tail -10
```

Expected: `ImportError` — `secrets_service` does not exist yet.

- [ ] **Step 3: Add `anthropic` and `cryptography` to `backend/requirements.txt`**

Add these two lines after `greenlet==3.1.1`:
```
anthropic>=0.40.0
cryptography>=43.0.0
```

- [ ] **Step 4: Add `AI_MODEL` to `backend/app/config.py`**

Add inside the `Settings` class after `CORS_ORIGINS`:
```python
AI_MODEL: str = "claude-sonnet-4-6"
```

- [ ] **Step 5: Create `backend/app/services/secrets_service.py`**

```python
import hashlib
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet


class SecretsService:
    """
    Encrypts and decrypts string secrets using Fernet (AES-256-GCM).

    Uses a key derived from the app SECRET_KEY. This class is designed
    as an abstraction: future implementations can delegate to HashiCorp
    Vault, AWS Secrets Manager, or an HSM without changing callers.
    """

    def __init__(self, secret_key: str):
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(urlsafe_b64encode(key_bytes))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()
```

- [ ] **Step 6: Run tests — expect 3 PASSED**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_secrets_service.py -v"
```

Expected: 3 tests PASSED.

- [ ] **Step 7: Run full suite — expect 89 PASSED**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 92 passed (89 + 3).

- [ ] **Step 8: Commit**

```bash
git add backend/requirements.txt backend/app/config.py backend/app/services/secrets_service.py backend/app/tests/test_secrets_service.py
git commit -m "feat: add SecretsService and anthropic dependency"
```

---

### Task 2: OrganizationSettings model + migration 004

**Files:**
- Create: `backend/app/models/org_settings.py`
- Create: `backend/alembic/versions/004_add_ai_settings.py`
- Modify: `backend/app/models/project.py` (add `ai_context`)

- [ ] **Step 1: Create `backend/app/models/org_settings.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import Text, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OrganizationSettings(Base):
    __tablename__ = "organization_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, unique=True
    )
    anthropic_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Add `ai_context` to `backend/app/models/project.py`**

Add after the `updated_at` field and before the `members` relationship:

```python
    ai_context: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
```

The full updated file with the addition:

```python
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class ProjectStatus(str, enum.Enum):
    draft = "draft"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status"), nullable=False, default=ProjectStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    ai_context: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    members: Mapped[list["ProjectChangeRequest"]] = relationship(
        "ProjectChangeRequest",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectChangeRequest.sequence_order",
    )


class ProjectChangeRequest(Base):
    __tablename__ = "project_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False
    )
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    change_request: Mapped["ChangeRequest"] = relationship("ChangeRequest")
```

- [ ] **Step 3: Create `backend/alembic/versions/004_add_ai_settings.py`**

```python
"""add ai settings and project ai_context

Revision ID: 004
Revises: 003
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("anthropic_api_key_encrypted", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.add_column(
        "projects",
        sa.Column("ai_context", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("projects", "ai_context")
    op.drop_table("organization_settings")
```

- [ ] **Step 4: Run the migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 003 -> 004, add ai settings and project ai_context`

- [ ] **Step 5: Verify**

```bash
docker compose exec db psql -U nexplane -d nexplane -c "\dt" 2>&1 | grep -E "organization_settings|projects"
docker compose exec db psql -U nexplane -d nexplane -c "SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='ai_context'"
```

Expected: `organization_settings` in table list; `ai_context` column present in projects.

- [ ] **Step 6: Run full test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 92 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/org_settings.py backend/app/models/project.py backend/alembic/versions/004_add_ai_settings.py
git commit -m "feat: add OrganizationSettings model, ai_context to projects, migration 004"
```

---

### Task 3: Settings schemas + router

**Files:**
- Create: `backend/app/schemas/org_settings.py`
- Create: `backend/app/routers/settings.py`
- Modify: `backend/app/schemas/project.py` (add `ai_context` to `ProjectDetailRead`)
- Modify: `backend/app/main.py` (register settings router)

- [ ] **Step 1: Create `backend/app/schemas/org_settings.py`**

```python
from datetime import datetime
from pydantic import BaseModel


class OrgSettingsRead(BaseModel):
    ai_configured: bool
    updated_at: datetime | None = None


class AIKeyUpdate(BaseModel):
    api_key: str
```

- [ ] **Step 2: Add `ai_context` to `ProjectDetailRead` in `backend/app/schemas/project.py`**

Replace the `ProjectDetailRead` class:
```python
class ProjectDetailRead(ProjectRead):
    members: list[ProjectMemberRead] = []
    ai_context: list[dict] = []
```

- [ ] **Step 3: Create `backend/app/routers/settings.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.org_settings import OrganizationSettings
from app.models.user import User, UserRole
from app.routers import current_user, require_roles
from app.schemas.org_settings import OrgSettingsRead, AIKeyUpdate
from app.services.secrets_service import SecretsService
from app.config import settings as app_settings

router = APIRouter(prefix="/settings", tags=["Settings"])


def _get_secrets() -> SecretsService:
    return SecretsService(app_settings.SECRET_KEY)


@router.get("", response_model=OrgSettingsRead)
async def get_settings(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == user.organization_id
        )
    )
    org_settings = result.scalar_one_or_none()
    if not org_settings:
        return OrgSettingsRead(ai_configured=False, updated_at=None)
    return OrgSettingsRead(
        ai_configured=org_settings.anthropic_api_key_encrypted is not None,
        updated_at=org_settings.updated_at,
    )


@router.put("/ai-key", response_model=OrgSettingsRead)
async def update_ai_key(
    body: AIKeyUpdate,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    secrets = _get_secrets()
    encrypted = secrets.encrypt(body.api_key)

    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == user.organization_id
        )
    )
    org_settings = result.scalar_one_or_none()

    if org_settings:
        org_settings.anthropic_api_key_encrypted = encrypted
    else:
        org_settings = OrganizationSettings(
            organization_id=user.organization_id,
            anthropic_api_key_encrypted=encrypted,
        )
        db.add(org_settings)

    await db.commit()
    await db.refresh(org_settings)
    return OrgSettingsRead(
        ai_configured=True,
        updated_at=org_settings.updated_at,
    )
```

- [ ] **Step 4: Register settings router in `backend/app/main.py`**

Add `settings` to the import line:
```python
from app.routers import auth, assets, connectors, change_requests, audit, projects, settings
```

Add after `app.include_router(projects.router)`:
```python
app.include_router(settings.router)
```

- [ ] **Step 5: Run full test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 92 passed.

- [ ] **Step 6: Rebuild and smoke-test**

```bash
docker compose up --build -d backend
```

Wait 8 seconds, then:
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"admin@acme.example","password":"admin123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/settings | python3 -c "import sys,json; print(json.load(sys.stdin))"
```

Expected: `{'ai_configured': False, 'updated_at': None}`

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/org_settings.py backend/app/routers/settings.py backend/app/schemas/project.py backend/app/main.py
git commit -m "feat: add settings router with AI key management"
```

---

### Task 4: AIService

**Files:**
- Create: `backend/app/services/ai_service.py`
- Create: `backend/app/tests/test_ai_service.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_ai_service.py`:
```python
import json
import pytest
from app.services.ai_service import AIService
from app.services.secrets_service import SecretsService


def make_service():
    return AIService(SecretsService("test-key-32-chars-minimum-pad!!"))


def test_parse_proposal_extracts_json():
    svc = make_service()
    text = (
        "Here is my proposed plan based on the information provided.\n\n"
        "<nexplane-proposal>\n"
        '[{"title": "Update firewall", "change_type": "security_group_update", '
        '"suggested_assets": ["fw-01"], "desired_outcome_sketch": {}, "notes": "First"}]\n'
        "</nexplane-proposal>"
    )
    result = svc._parse_proposal(text)
    assert result is not None
    assert len(result) == 1
    assert result[0]["title"] == "Update firewall"
    assert result[0]["change_type"] == "security_group_update"


def test_parse_proposal_returns_none_when_absent():
    svc = make_service()
    result = svc._parse_proposal("No proposal here, just a clarifying question.")
    assert result is None


def test_parse_proposal_returns_none_on_invalid_json():
    svc = make_service()
    text = "<nexplane-proposal>not valid json</nexplane-proposal>"
    result = svc._parse_proposal(text)
    assert result is None


def test_strip_proposal_tags_removes_block():
    svc = make_service()
    text = "Here is my plan.\n\n<nexplane-proposal>[{}]</nexplane-proposal>"
    result = svc._strip_proposal_tags(text)
    assert "<nexplane-proposal>" not in result
    assert "Here is my plan." in result


def test_build_system_prompt_includes_goal():
    svc = make_service()
    prompt = svc._build_system_prompt("Microsegmentation for payments", [])
    assert "Microsegmentation for payments" in prompt
    assert "<nexplane-proposal>" in prompt


def test_build_system_prompt_includes_assets():
    svc = make_service()
    assets = [{"name": "payments-fw-01", "asset_type": "firewall", "environment": "prod", "tags": ["payments"]}]
    prompt = svc._build_system_prompt("Test goal", assets)
    assert "payments-fw-01" in prompt
    assert "firewall" in prompt
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_ai_service.py -v" 2>&1 | tail -10
```

Expected: `ImportError` — `ai_service` does not exist yet.

- [ ] **Step 3: Create `backend/app/services/ai_service.py`**

```python
import json
import re
from app.services.secrets_service import SecretsService


_SYSTEM_PROMPT_TEMPLATE = """You are a planning assistant for Nexplane, a secure infrastructure change management platform.

The operator is planning a project with this goal: {goal}

Available assets in their environment:
{assets_text}

Your job:
1. Ask targeted clarifying questions ONE AT A TIME to understand scope, affected assets, risk tolerance, and sequencing constraints. Reference available assets by name when relevant.
2. When you have enough information to propose a complete change plan, write your proposal as prose and then append a structured block using this EXACT format:

<nexplane-proposal>
[
  {{
    "title": "Short descriptive title for the change request",
    "change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy",
    "suggested_assets": ["asset name 1", "asset name 2"],
    "desired_outcome_sketch": {{}},
    "notes": "Optional sequencing or dependency notes"
  }}
]
</nexplane-proposal>

Only include the <nexplane-proposal> block when you are ready to propose the FULL plan. Do not include it in clarifying question responses."""


class AIService:
    def __init__(self, secrets_service: SecretsService):
        self._secrets = secrets_service

    def _build_system_prompt(self, goal: str, asset_context: list[dict]) -> str:
        if asset_context:
            lines = []
            for a in asset_context:
                line = f"- {a['name']} ({a['asset_type']}, {a['environment']})"
                if a.get("tags"):
                    line += f" [tags: {', '.join(a['tags'])}]"
                lines.append(line)
            assets_text = "\n".join(lines)
        else:
            assets_text = "No assets registered yet."
        return _SYSTEM_PROMPT_TEMPLATE.format(goal=goal, assets_text=assets_text)

    def _parse_proposal(self, text: str) -> list[dict] | None:
        match = re.search(r"<nexplane-proposal>(.*?)</nexplane-proposal>", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            return None

    def _strip_proposal_tags(self, text: str) -> str:
        return re.sub(
            r"\s*<nexplane-proposal>.*?</nexplane-proposal>", "", text, flags=re.DOTALL
        ).strip()

    async def chat(
        self,
        api_key: str,
        conversation: list[dict],
        project_goal: str,
        asset_context: list[dict],
    ) -> dict:
        import anthropic
        from app.config import settings

        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt = self._build_system_prompt(project_goal, asset_context)

        messages = [{"role": m["role"], "content": m["content"]} for m in conversation]

        response = await client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )

        reply_text = response.content[0].text
        proposed_crs = self._parse_proposal(reply_text)
        clean_reply = self._strip_proposal_tags(reply_text)

        return {"reply": clean_reply, "proposed_crs": proposed_crs}
```

- [ ] **Step 4: Run tests — expect 6 PASSED**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_ai_service.py -v"
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Run full suite — expect 98 passed**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 98 passed (92 + 6).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai_service.py backend/app/tests/test_ai_service.py
git commit -m "feat: add AIService with Claude integration and proposal parsing"
```

---

### Task 5: AI chat endpoint in projects router

**Files:**
- Modify: `backend/app/routers/projects.py`

Add `POST /projects/{id}/ai/chat` after the existing `update_project_member` endpoint.

- [ ] **Step 1: Add the AI chat endpoint to `backend/app/routers/projects.py`**

Add these imports at the top of the file (after existing imports):
```python
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.models.org_settings import OrganizationSettings
from app.models.asset import Asset
from app.services.secrets_service import SecretsService
from app.services.ai_service import AIService
from app.config import settings as app_settings
from pydantic import BaseModel
```

Add this schema class after the imports:
```python
class AIChatRequest(BaseModel):
    message: str


class AIChatResponse(BaseModel):
    reply: str
    proposed_crs: list[dict] | None = None
```

Add this endpoint at the end of `projects.py`:
```python
@router.post("/{project_id}/ai/chat", response_model=AIChatResponse)
async def ai_chat(
    project_id: uuid.UUID,
    body: AIChatRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    # Get org API key
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == user.organization_id
        )
    )
    org_settings = result.scalar_one_or_none()
    if not org_settings or not org_settings.anthropic_api_key_encrypted:
        raise HTTPException(
            status_code=402,
            detail="AI not configured — add an Anthropic API key in Settings",
        )

    secrets = SecretsService(app_settings.SECRET_KEY)
    api_key = secrets.decrypt(org_settings.anthropic_api_key_encrypted)

    # Load asset context
    assets_result = await db.execute(
        select(Asset).where(Asset.organization_id == user.organization_id)
    )
    assets = assets_result.scalars().all()
    asset_context = [
        {
            "name": a.name,
            "asset_type": a.asset_type.value,
            "environment": a.environment.value,
            "tags": a.tags or [],
        }
        for a in assets
    ]

    # Append user message to conversation
    conversation = list(project.ai_context or [])
    conversation.append({"role": "user", "content": body.message})

    # Call Claude
    ai_service = AIService(secrets)
    try:
        result_dict = await ai_service.chat(
            api_key=api_key,
            conversation=conversation,
            project_goal=project.goal or project.name,
            asset_context=asset_context,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI service error: {str(exc)}",
        )

    # Append AI reply to conversation and save
    conversation.append({"role": "assistant", "content": result_dict["reply"]})
    project.ai_context = conversation
    await db.commit()

    return AIChatResponse(
        reply=result_dict["reply"],
        proposed_crs=result_dict["proposed_crs"],
    )
```

- [ ] **Step 2: Run full test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 98 passed.

- [ ] **Step 3: Rebuild and smoke-test**

```bash
docker compose up --build -d backend
```

Wait 8 seconds, then test that the endpoint exists and returns 402 when no key is configured:
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
PROJECT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/projects | python3 -c "import sys,json; projects=json.load(sys.stdin); print(projects[0]['id']) if projects else print('no-projects')")
curl -s -X POST "http://localhost:8000/projects/$PROJECT_ID/ai/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "help me plan"}' | python3 -c "import sys,json; print(json.load(sys.stdin).get('detail', 'no detail'))"
```

Expected: `AI not configured — add an Anthropic API key in Settings` (402 response).

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/projects.py
git commit -m "feat: add POST /projects/{id}/ai/chat endpoint"
```

---

### Task 6: Frontend types + API client

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/api/endpoints.ts`

- [ ] **Step 1: Add AI types to `frontend/src/types/api.ts`**

Append these types after `ProjectMemberUpdate`:
```typescript
export interface OrgSettings {
  ai_configured: boolean;
  updated_at: string | null;
}

export interface AIProposedCR {
  title: string;
  change_type: ChangeType;
  suggested_assets: string[];
  desired_outcome_sketch: Record<string, unknown>;
  notes?: string;
}

export interface AIChatResponse {
  reply: string;
  proposed_crs: AIProposedCR[] | null;
}

export interface AIChatRequest {
  message: string;
}
```

Also update `ProjectDetail` interface to include `ai_context`:
```typescript
export interface ProjectDetail extends Project {
  members: ProjectMember[];
  ai_context: Array<{ role: string; content: string }>;
}
```

- [ ] **Step 2: Update `frontend/src/api/endpoints.ts`**

Add `OrgSettings`, `AIChatResponse`, `AIChatRequest` to the import line:
```typescript
import type {
  Token, User, Asset, AssetCreate, AssetUpdate, AssetListParams, BulkTagBody,
  Project, ProjectSummary, ProjectDetail, ProjectMember,
  ProjectCreate, ProjectUpdate, ProjectMemberCreate, ProjectMemberUpdate,
  OrgSettings, AIChatResponse, AIChatRequest,
  Connector, ConnectorCreate, ConnectorTestResult,
  ChangeRequest, ChangeRequestSummary, ChangeRequestCreate,
  ChangePlan, Approval, ApprovalCreate, ExecutionRun, AuditEvent,
} from "../types/api";
```

Add `settingsApi` before the `// Connectors` section:
```typescript
// Settings
export const settingsApi = {
  get: () =>
    apiClient.get<OrgSettings>("/settings").then((r) => r.data),
  updateAIKey: (api_key: string) =>
    apiClient.put<OrgSettings>("/settings/ai-key", { api_key }).then((r) => r.data),
};
```

Add `aiChat` to `projectsApi`:
```typescript
  aiChat: (id: string, data: AIChatRequest) =>
    apiClient.post<AIChatResponse>(`/projects/${id}/ai/chat`, data).then((r) => r.data),
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/api/endpoints.ts
git commit -m "feat: add AI types and API client methods"
```

---

### Task 7: Settings page

**Files:**
- Create: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Create `frontend/src/pages/Settings.tsx`**

```tsx
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Settings as SettingsIcon, Check, Key } from "lucide-react";
import { settingsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";

export function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [showKeyInput, setShowKeyInput] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => settingsApi.get(),
  });

  const updateKey = useMutation({
    mutationFn: () => settingsApi.updateAIKey(apiKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setShowKeyInput(false);
      setApiKey("");
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  const isAdmin = user?.role === "admin";

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8 max-w-2xl">
      <PageHeader
        title="Settings"
        subtitle="Organization configuration"
      />

      <div className="bg-white border border-slate-200 rounded-lg p-6">
        <div className="flex items-center gap-2 mb-1">
          <Key className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">AI Configuration</h2>
        </div>
        <p className="text-xs text-slate-500 mb-4">
          Anthropic API key for AI-assisted project planning. Key is encrypted at rest and never displayed.
        </p>

        <div className="flex items-center gap-3 mb-3">
          {settings?.ai_configured ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600">
              <Check className="w-4 h-4" />
              Configured
              {settings.updated_at && (
                <span className="text-slate-400 font-normal">
                  · Updated {new Date(settings.updated_at).toLocaleDateString()}
                </span>
              )}
            </span>
          ) : (
            <span className="text-sm text-slate-400">Not configured</span>
          )}

          {isAdmin && !showKeyInput && (
            <button
              onClick={() => setShowKeyInput(true)}
              className="text-sm text-brand-600 hover:underline"
            >
              {settings?.ai_configured ? "Update key" : "Add key"}
            </button>
          )}

          {saved && (
            <span className="text-sm text-emerald-600">✓ Saved</span>
          )}
        </div>

        {isAdmin && showKeyInput && (
          <div className="flex gap-2 items-start">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-ant-api03-..."
              autoFocus
              className="flex-1 text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
            />
            <button
              onClick={() => updateKey.mutate()}
              disabled={!apiKey.trim() || updateKey.isPending}
              className="px-3 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50"
            >
              {updateKey.isPending ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => { setShowKeyInput(false); setApiKey(""); }}
              className="px-3 py-2 border border-slate-200 text-slate-600 text-sm rounded-md hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
        )}

        {!isAdmin && !settings?.ai_configured && (
          <p className="text-xs text-amber-600">
            AI is not configured. Ask an administrator to add an Anthropic API key.
          </p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat: add Settings page with AI key management"
```

---

### Task 8: AI Panel component

**Files:**
- Create: `frontend/src/components/AIPanel.tsx`

This component is used inside `ProjectDetail`. It receives the project's existing conversation and handles the chat interaction.

- [ ] **Step 1: Create `frontend/src/components/AIPanel.tsx`**

```tsx
import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Send, X, Plus } from "lucide-react";
import { projectsApi, changeRequestsApi } from "../api/endpoints";
import type { AIProposedCR, ChangeType } from "../types/api";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface AIPanelProps {
  projectId: string;
  projectGoal: string;
  initialConversation: Message[];
  onClose: () => void;
}

export function AIPanel({ projectId, projectGoal, initialConversation, onClose }: AIPanelProps) {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<Message[]>(initialConversation);
  const [input, setInput] = useState("");
  const [proposedCRs, setProposedCRs] = useState<AIProposedCR[] | null>(null);
  const [addedIndices, setAddedIndices] = useState<Set<number>>(new Set());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const chatMutation = useMutation({
    mutationFn: (message: string) =>
      projectsApi.aiChat(projectId, { message }),
    onSuccess: (data) => {
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.proposed_crs) {
        setProposedCRs(data.proposed_crs);
      }
    },
  });

  function handleSend() {
    const msg = input.trim();
    if (!msg || chatMutation.isPending) return;
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setInput("");
    chatMutation.mutate(msg);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const addCRMutation = useMutation({
    mutationFn: async ({ cr, idx }: { cr: AIProposedCR; idx: number }) => {
      const newCr = await changeRequestsApi.create({
        title: cr.title,
        change_type: cr.change_type as ChangeType,
        target_asset_ids: [],
        desired_outcome: cr.desired_outcome_sketch,
      });
      await projectsApi.addMember(projectId, { change_request_id: newCr.id });
      return idx;
    },
    onSuccess: (idx) => {
      setAddedIndices((prev) => new Set([...prev, idx]));
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
    },
  });

  const addAllMutation = useMutation({
    mutationFn: async () => {
      if (!proposedCRs) return;
      for (let i = 0; i < proposedCRs.length; i++) {
        if (!addedIndices.has(i)) {
          const cr = proposedCRs[i];
          const newCr = await changeRequestsApi.create({
            title: cr.title,
            change_type: cr.change_type as ChangeType,
            target_asset_ids: [],
            desired_outcome: cr.desired_outcome_sketch,
          });
          await projectsApi.addMember(projectId, { change_request_id: newCr.id });
          setAddedIndices((prev) => new Set([...prev, i]));
        }
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
    },
  });

  return (
    <div className="flex flex-col h-full bg-white border border-slate-200 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
        <span className="text-sm font-semibold text-slate-900">✦ AI Assistant</span>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Conversation */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
        {messages.length === 0 && (
          <div className="text-sm text-slate-400 text-center py-8">
            Describe your goal above, then ask the AI to help plan the change requests.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-xs rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-brand-600 text-white"
                  : "bg-slate-100 text-slate-900"
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}
        {chatMutation.isPending && (
          <div className="flex justify-start">
            <div className="bg-slate-100 rounded-lg px-3 py-2 text-sm text-slate-400 animate-pulse">
              Thinking…
            </div>
          </div>
        )}
        {chatMutation.isError && (
          <div className="text-xs text-red-500 text-center">
            {(chatMutation.error as Error)?.message?.includes("402")
              ? "AI not configured. Ask an admin to add an Anthropic API key in Settings."
              : "Something went wrong. Please try again."}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Proposed CRs */}
      {proposedCRs && proposedCRs.length > 0 && (
        <div className="border-t border-slate-100 p-3 space-y-2 shrink-0">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Proposed Changes
          </p>
          <div className="space-y-1.5 max-h-48 overflow-y-auto">
            {proposedCRs.map((cr, i) => (
              <div
                key={i}
                className="flex items-start justify-between gap-2 bg-slate-50 rounded-md px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="text-xs font-medium text-slate-900 truncate">{cr.title}</div>
                  <div className="text-xs text-slate-400">
                    {cr.change_type.replace(/_/g, " ")}
                    {cr.notes && <> · {cr.notes}</>}
                  </div>
                </div>
                {addedIndices.has(i) ? (
                  <span className="text-xs text-emerald-600 shrink-0">✓ Added</span>
                ) : (
                  <button
                    onClick={() => addCRMutation.mutate({ cr, idx: i })}
                    disabled={addCRMutation.isPending}
                    className="shrink-0 p-1 text-brand-600 hover:bg-brand-50 rounded"
                  >
                    <Plus className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
          {proposedCRs.some((_, i) => !addedIndices.has(i)) && (
            <button
              onClick={() => addAllMutation.mutate()}
              disabled={addAllMutation.isPending}
              className="w-full text-xs text-brand-600 hover:underline py-1 disabled:opacity-50"
            >
              {addAllMutation.isPending ? "Adding…" : "Add all to Project"}
            </button>
          )}
        </div>
      )}

      {/* Input */}
      <div className="border-t border-slate-100 p-3 flex gap-2 shrink-0">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask the AI to help plan your project…"
          rows={2}
          className="flex-1 text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || chatMutation.isPending}
          className="self-end p-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/AIPanel.tsx
git commit -m "feat: add AIPanel component with conversation and proposal cards"
```

---

### Task 9: Wire AIPanel into ProjectDetail

**Files:**
- Modify: `frontend/src/pages/ProjectDetail.tsx`

Add the AI panel toggle button to the header and the two-column layout when the panel is open.

- [ ] **Step 1: Read the current `ProjectDetail.tsx`**

Read `frontend/src/pages/ProjectDetail.tsx` to understand its current structure before modifying.

- [ ] **Step 2: Add these changes to `ProjectDetail.tsx`**

**Add import at the top:**
```tsx
import { AIPanel } from "../components/AIPanel";
```

**Add `Sparkles` to the lucide-react import:**
```tsx
import {
  ArrowLeft, Plus, X, ChevronUp, ChevronDown, Search, Sparkles,
} from "lucide-react";
```

**Add state variable after existing state declarations:**
```tsx
const [showAIPanel, setShowAIPanel] = useState(false);
```

**In the header div, add the AI Assistant button** before the status dropdown. Find the `<div className="flex items-center gap-2 shrink-0">` in the header and add this button as the first child:
```tsx
          {!isNew && isDraft && (
            <button
              onClick={() => setShowAIPanel(!showAIPanel)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
                showAIPanel
                  ? "bg-brand-50 border-brand-300 text-brand-700"
                  : "border-slate-200 text-slate-600 hover:bg-slate-50"
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              AI Assistant
            </button>
          )}
```

**Wrap the member list section** in a flex container when the panel is open. Find the `{!isNew && (` block and wrap its content in:
```tsx
          <div className={`flex gap-4 ${showAIPanel ? "items-start" : ""}`}>
            <div className={showAIPanel ? "flex-1 min-w-0" : "w-full"}>
              {/* existing member list content unchanged */}
            </div>
            {showAIPanel && (
              <div className="w-80 shrink-0 sticky top-4" style={{ height: "calc(100vh - 200px)" }}>
                <AIPanel
                  projectId={id!}
                  projectGoal={project?.goal ?? ""}
                  initialConversation={(project?.ai_context ?? []) as Array<{role: "user" | "assistant"; content: string}>}
                  onClose={() => setShowAIPanel(false)}
                />
              </div>
            )}
          </div>
```

- [ ] **Step 3: Copy both files to main repo**

- Read `frontend/src/pages/ProjectDetail.tsx` from worktree → Write to `f:/Nexplane/nexplane/frontend/src/pages/ProjectDetail.tsx`
- Read `frontend/src/components/AIPanel.tsx` from worktree → Write to `f:/Nexplane/nexplane/frontend/src/components/AIPanel.tsx`

- [ ] **Step 4: Verify in browser**

Open http://localhost:3000/projects, navigate to a draft project. Verify:
1. "✦ AI Assistant" button appears in the header (with Sparkles icon)
2. Clicking it opens the right-side panel with the conversation area and input
3. Clicking it again closes the panel
4. The member list shrinks to accommodate the panel when open

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ProjectDetail.tsx
git commit -m "feat: integrate AIPanel into ProjectDetail page"
```

---

### Task 10: Routes + Sidebar + final sync

**Files:**
- Modify: `frontend/src/routes/index.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Sync all changed files to main repo

- [ ] **Step 1: Add `/settings` route to `frontend/src/routes/index.tsx`**

Add import:
```tsx
import { Settings } from "../pages/Settings";
```

Add route inside `<Route element={<Layout />}>`:
```tsx
<Route path="/settings" element={<Settings />} />
```

- [ ] **Step 2: Add Settings link to `frontend/src/components/Sidebar.tsx`**

Add `Settings as SettingsIcon` to the lucide-react import:
```tsx
import {
  LayoutDashboard, FileStack, CheckSquare, Plug, Server, ShieldCheck, FolderOpen, Settings as SettingsIcon,
} from "lucide-react";
```

Add to `navItems` at the end (before the user profile section):
```tsx
{ to: "/settings", label: "Settings", icon: SettingsIcon },
```

- [ ] **Step 3: Sync all changed files to main repo**

Copy these files from worktree to `f:/Nexplane/nexplane/...`:
- `frontend/src/routes/index.tsx`
- `frontend/src/components/Sidebar.tsx`
- `frontend/src/types/api.ts`
- `frontend/src/api/endpoints.ts`
- `frontend/src/pages/Settings.tsx`
- `backend/app/models/org_settings.py`
- `backend/app/models/project.py`
- `backend/app/schemas/org_settings.py`
- `backend/app/schemas/project.py`
- `backend/app/services/secrets_service.py`
- `backend/app/services/ai_service.py`
- `backend/app/routers/settings.py`
- `backend/app/routers/projects.py`
- `backend/app/main.py`
- `backend/requirements.txt`
- `backend/app/config.py`

- [ ] **Step 4: Run final backend test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 98 passed.

- [ ] **Step 5: End-to-end verification**

1. Navigate to http://localhost:3000/settings — Settings link appears in sidebar
2. Log in as admin — "Add key" button is visible
3. Enter a valid Anthropic API key and save — status shows "Configured ✓"
4. Navigate to a draft project — "✦ AI Assistant" button appears
5. Click it — panel opens on the right
6. Type a message and send — AI responds (or shows error if key is test-only)
7. If AI returns a `<nexplane-proposal>` block, proposal cards appear with `[+]` buttons
8. Click `[+]` on a proposal — CR is created and added to the project

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/index.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: add Settings route and sidebar link"
```
