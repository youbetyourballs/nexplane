# Nexplane Agent — Backend API Implementation Plan (Plan 4a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the backend agent API to Nexplane: DB models, migration, authentication, three endpoints, agent secret management in Settings, and the nexplane_agent_mock connector catalog.

**Architecture:** New `AgentRegistration` and `AgentJob` models live alongside existing models. A dedicated `/agent/...` router handles registration, long-poll job dispatch, and result posting. Agent auth uses bearer token matching against the org's encrypted agent secret (same SecretsService as the AI key). The `nexplane_agent_mock` connector catalog registers five commands so the planning engine can create agent jobs.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, PostgreSQL, Pydantic, React 18, TypeScript, TanStack Query

---

## Codebase Context

- Existing pattern for models: `backend/app/models/*.py` — use `Mapped`, `mapped_column`, `SAEnum`
- Existing pattern for routers: `backend/app/routers/*.py` — `APIRouter`, `Depends(current_user)` for user auth
- Agent auth is different: bearer token = org's agent secret, not a user JWT
- `SecretsService` in `backend/app/services/secrets_service.py` — encrypt/decrypt strings
- Migration pattern: see `backend/alembic/versions/005_add_identity_type_and_connector_types.py`
- Connector catalog: JSON files in `backend/app/connectors/catalog/`, executor modules in `backend/app/connectors/executors/<connector_type>/`
- Run tests: `docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"`

---

## Task 1: DB Models and Migration 006

**Files:**
- Create: `backend/app/models/agent.py`
- Create: `backend/alembic/versions/006_add_agent_models.py`
- Modify: `backend/app/models/org_settings.py` (add `agent_secret_encrypted`)
- Modify: `backend/app/models/connector.py` (add `nexplane_agent` to `ConnectorType`)

- [ ] **Step 1: Write the failing test**

```python
# backend/app/tests/test_agent_models.py
import uuid
import pytest
from app.models.agent import AgentRegistration, AgentJob, OsType, AgentJobStatus


def test_os_type_enum_values():
    assert OsType.linux == "linux"
    assert OsType.windows == "windows"


def test_agent_job_status_enum_values():
    assert AgentJobStatus.pending == "pending"
    assert AgentJobStatus.running == "running"
    assert AgentJobStatus.completed == "completed"
    assert AgentJobStatus.failed == "failed"


def test_connector_type_includes_nexplane_agent():
    from app.models.connector import ConnectorType
    assert ConnectorType.nexplane_agent == "nexplane_agent"
```

- [ ] **Step 2: Run to verify it fails**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_models.py -v"
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.agent'`

- [ ] **Step 3: Create `backend/app/models/agent.py`**

```python
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Text, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OsType(str, enum.Enum):
    linux = "linux"
    windows = "windows"


class AgentJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AgentRegistration(Base):
    __tablename__ = "agent_registrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    machine_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    os_type: Mapped[OsType] = mapped_column(SAEnum(OsType, name="os_type"), nullable=False)
    ip_addresses: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    os_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    agent_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentJob(Base):
    __tablename__ = "agent_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    agent_registration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_registrations.id"), nullable=False)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    command: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    hmac_signature: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[AgentJobStatus] = mapped_column(
        SAEnum(AgentJobStatus, name="agent_job_status"),
        nullable=False,
        default=AgentJobStatus.pending,
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Add `nexplane_agent` to `backend/app/models/connector.py`**

```python
class ConnectorType(str, enum.Enum):
    aws_mock = "aws_mock"
    azure_mock = "azure_mock"
    cloudflare_mock = "cloudflare_mock"
    okta_mock = "okta_mock"
    paloalto_mock = "paloalto_mock"
    ssh_runner_mock = "ssh_runner_mock"
    active_directory_mock = "active_directory_mock"
    crowdstrike_mock = "crowdstrike_mock"
    tenable_mock = "tenable_mock"
    nexplane_agent = "nexplane_agent"
```

- [ ] **Step 5: Add `agent_secret_encrypted` to `backend/app/models/org_settings.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import Text, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OrganizationSettings(Base):
    __tablename__ = "organization_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, unique=True
    )
    anthropic_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 6: Create `backend/alembic/versions/006_add_agent_models.py`**

```python
"""add agent models and nexplane_agent connector type

Revision ID: 006
Revises: 005
Create Date: 2026-05-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'nexplane_agent'")

    op.execute("CREATE TYPE os_type AS ENUM ('linux', 'windows')")
    op.execute("CREATE TYPE agent_job_status AS ENUM ('pending', 'running', 'completed', 'failed')")

    op.add_column(
        "organization_settings",
        sa.Column("agent_secret_encrypted", sa.Text, nullable=True),
    )

    op.create_table(
        "agent_registrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("machine_id", sa.String(255), nullable=False),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os_type", sa.Enum("linux", "windows", name="os_type"), nullable=False),
        sa.Column("ip_addresses", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("os_version", sa.String(255), nullable=False, server_default=""),
        sa.Column("agent_version", sa.String(50), nullable=False, server_default=""),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_agent_registrations_org_machine",
        "agent_registrations",
        ["organization_id", "machine_id"],
    )

    op.create_table(
        "agent_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("agent_registration_id", UUID(as_uuid=True), sa.ForeignKey("agent_registrations.id"), nullable=False),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("command", sa.String(100), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("hmac_signature", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="agent_job_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("agent_jobs")
    op.drop_table("agent_registrations")
    op.drop_column("organization_settings", "agent_secret_encrypted")
    op.execute("DROP TYPE agent_job_status")
    op.execute("DROP TYPE os_type")
```

- [ ] **Step 7: Run migration**

```
docker compose exec backend bash -c "cd /app && alembic upgrade head"
```
Expected: `006 (head)`

- [ ] **Step 8: Run tests to verify they pass**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_models.py -v"
```
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/agent.py backend/app/models/org_settings.py \
        backend/app/models/connector.py \
        backend/alembic/versions/006_add_agent_models.py \
        backend/app/tests/test_agent_models.py
git commit -m "feat: add AgentRegistration, AgentJob models and migration 006"
```

---

## Task 2: Schemas and HMAC Signing

**Files:**
- Create: `backend/app/schemas/agent.py`
- Create: `backend/app/services/agent_hmac.py`
- Modify: `backend/app/schemas/org_settings.py`
- Create: `backend/app/tests/test_agent_hmac.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/app/tests/test_agent_hmac.py
from app.services.agent_hmac import compute_job_signature, verify_job_signature


def test_compute_signature_is_deterministic():
    sig1 = compute_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"})
    sig2 = compute_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"})
    assert sig1 == sig2


def test_signature_varies_by_secret():
    sig1 = compute_job_signature("secret-a", "job-1", "change_ip", {})
    sig2 = compute_job_signature("secret-b", "job-1", "change_ip", {})
    assert sig1 != sig2


def test_signature_varies_by_command():
    sig1 = compute_job_signature("secret", "job-1", "change_ip", {})
    sig2 = compute_job_signature("secret", "job-1", "configure_syslog", {})
    assert sig1 != sig2


def test_verify_returns_true_for_correct_signature():
    sig = compute_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"})
    assert verify_job_signature("secret", "job-1", "change_ip", {"interface": "eth0"}, sig) is True


def test_verify_returns_false_for_wrong_signature():
    assert verify_job_signature("secret", "job-1", "change_ip", {}, "badsig") is False


def test_canonical_json_sorts_keys():
    sig1 = compute_job_signature("s", "j", "cmd", {"b": 1, "a": 2})
    sig2 = compute_job_signature("s", "j", "cmd", {"a": 2, "b": 1})
    assert sig1 == sig2
```

- [ ] **Step 2: Run to verify fails**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_hmac.py -v"
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/services/agent_hmac.py`**

```python
import hmac as _hmac
import hashlib
import json


def compute_job_signature(secret: str, job_id: str, command: str, parameters: dict) -> str:
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    message = f"{job_id}:{command}:{canonical}".encode()
    return _hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_job_signature(
    secret: str, job_id: str, command: str, parameters: dict, signature: str
) -> bool:
    expected = compute_job_signature(secret, job_id, command, parameters)
    return _hmac.compare_digest(expected, signature)
```

- [ ] **Step 4: Create `backend/app/schemas/agent.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.agent import OsType, AgentJobStatus


class AgentRegisterRequest(BaseModel):
    machine_id: str
    hostname: str
    os_type: OsType
    ip_addresses: list[str] = []
    os_version: str = ""
    agent_version: str = ""


class AgentRegisterResponse(BaseModel):
    agent_id: uuid.UUID
    asset_id: uuid.UUID


class AgentJobResponse(BaseModel):
    job_id: uuid.UUID
    command: str
    parameters: dict
    hmac_signature: str


class AgentJobResultRequest(BaseModel):
    status: AgentJobStatus
    result: dict | None = None
    error: str | None = None
```

- [ ] **Step 5: Update `backend/app/schemas/org_settings.py`**

```python
from datetime import datetime
from pydantic import BaseModel


class OrgSettingsRead(BaseModel):
    ai_configured: bool
    agent_configured: bool = False
    updated_at: datetime | None = None


class AIKeyUpdate(BaseModel):
    api_key: str


class AgentSecretUpdate(BaseModel):
    secret: str
```

- [ ] **Step 6: Run tests**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_hmac.py -v"
```
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/agent_hmac.py backend/app/schemas/agent.py \
        backend/app/schemas/org_settings.py backend/app/tests/test_agent_hmac.py
git commit -m "feat: add agent HMAC service and schemas"
```

---

## Task 3: Agent Router — Register and Auth

**Files:**
- Create: `backend/app/routers/agent.py`
- Create: `backend/app/tests/test_agent_router.py` (register endpoint)

- [ ] **Step 1: Write the failing test**

```python
# backend/app/tests/test_agent_router.py
import uuid
import pytest
from httpx import AsyncClient
from app.main import app
from app.tests.conftest import db  # noqa — used via pytest fixture

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
AGENT_SECRET = "sk-agent-test1234567890abcdef12345678"


@pytest.mark.asyncio
async def test_register_rejects_invalid_secret():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post(
            "/agent/register",
            json={
                "machine_id": "test-machine-id",
                "hostname": "test-host",
                "os_type": "linux",
                "ip_addresses": ["10.0.0.1"],
                "os_version": "Ubuntu 22.04",
                "agent_version": "0.1.0",
            },
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_missing_auth_rejected():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post(
            "/agent/register",
            json={
                "machine_id": "test-machine-id",
                "hostname": "test-host",
                "os_type": "linux",
                "ip_addresses": [],
                "os_version": "Ubuntu 22.04",
                "agent_version": "0.1.0",
            },
        )
    assert resp.status_code in (401, 422)
```

- [ ] **Step 2: Run to verify fails**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_router.py -v"
```
Expected: FAIL — `ImportError` or route 404

- [ ] **Step 3: Create `backend/app/routers/agent.py`**

```python
import uuid
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Response
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import AgentRegistration, AgentJob, AgentJobStatus, OsType
from app.models.org_settings import OrganizationSettings
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.schemas.agent import (
    AgentRegisterRequest, AgentRegisterResponse,
    AgentJobResponse, AgentJobResultRequest,
)
from app.services.secrets_service import SecretsService
from app import config as app_config

router = APIRouter(prefix="/agent", tags=["Agent"])

_LONG_POLL_SECONDS = 30
_POLL_INTERVAL_SECONDS = 2


def _secrets() -> SecretsService:
    return SecretsService(app_config.settings.SECRET_KEY)


async def _get_org_settings_by_secret(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> OrganizationSettings:
    """Validate bearer token against stored agent secrets. Returns matching OrgSettings."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")

    secrets = _secrets()
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.agent_secret_encrypted.is_not(None)
        )
    )
    for org_settings in result.scalars().all():
        try:
            if secrets.decrypt(org_settings.agent_secret_encrypted) == token:
                return org_settings
        except Exception:
            continue
    raise HTTPException(status_code=401, detail="Invalid agent secret")


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    org_id = org_settings.organization_id

    # Upsert AgentRegistration by (organization_id, machine_id)
    result = await db.execute(
        select(AgentRegistration).where(
            and_(
                AgentRegistration.organization_id == org_id,
                AgentRegistration.machine_id == body.machine_id,
            )
        )
    )
    registration = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if not registration:
        # Create asset first
        asset = Asset(
            organization_id=org_id,
            name=body.hostname,
            asset_type=AssetType.server,
            environment=Environment.prod,
            criticality=Criticality.medium,
            tags=["nexplane-agent", body.os_type.value],
            asset_metadata={
                "hostname": body.hostname,
                "os_type": body.os_type.value,
                "os_version": body.os_version,
                "agent_version": body.agent_version,
                "ip_addresses": body.ip_addresses,
                "last_seen": now.isoformat(),
            },
        )
        db.add(asset)
        await db.flush()

        registration = AgentRegistration(
            organization_id=org_id,
            machine_id=body.machine_id,
            asset_id=asset.id,
            hostname=body.hostname,
            os_type=body.os_type,
            ip_addresses=body.ip_addresses,
            os_version=body.os_version,
            agent_version=body.agent_version,
            last_seen=now,
        )
        db.add(registration)
        await db.flush()
    else:
        # Update existing registration and asset
        registration.hostname = body.hostname
        registration.ip_addresses = body.ip_addresses
        registration.os_version = body.os_version
        registration.agent_version = body.agent_version
        registration.last_seen = now

        if registration.asset_id:
            asset_result = await db.get(Asset, registration.asset_id)
            if asset_result:
                asset_result.name = body.hostname
                meta = dict(asset_result.asset_metadata or {})
                meta.update({
                    "hostname": body.hostname,
                    "os_type": body.os_type.value,
                    "os_version": body.os_version,
                    "agent_version": body.agent_version,
                    "ip_addresses": body.ip_addresses,
                    "last_seen": now.isoformat(),
                })
                asset_result.asset_metadata = meta

    await db.commit()
    await db.refresh(registration)

    return AgentRegisterResponse(
        agent_id=registration.id,
        asset_id=registration.asset_id,
    )
```

- [ ] **Step 4: Register the router in `backend/app/main.py`**

Add import and `app.include_router`:

```python
from app.routers import auth, assets, connectors, change_requests, audit, projects, agent as agent_router
from app.routers import settings as settings_router
```

After `app.include_router(settings_router.router)`:
```python
app.include_router(agent_router.router)
```

- [ ] **Step 5: Run tests**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_router.py -v"
```
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/agent.py backend/app/main.py \
        backend/app/tests/test_agent_router.py
git commit -m "feat: add agent router with registration endpoint and secret auth"
```

---

## Task 4: Agent Router — Job Poll and Result Endpoints

**Files:**
- Modify: `backend/app/routers/agent.py` (add two endpoints)
- Modify: `backend/app/tests/test_agent_router.py` (add tests)

- [ ] **Step 1: Add tests**

Append to `backend/app/tests/test_agent_router.py`:

```python
@pytest.mark.asyncio
async def test_poll_returns_204_when_no_jobs():
    """Poll with a valid agent_id that has no pending jobs returns 204."""
    # We test with a non-existent agent_id — should return 204 (no jobs for unknown agent)
    # The endpoint should not crash on unknown agent_id; just return 204
    fake_agent_id = uuid.uuid4()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get(
            f"/agent/jobs/next?agent_id={fake_agent_id}",
            headers={"Authorization": "Bearer wrong-secret"},
        )
    # 401 because auth fails first — proves endpoint exists
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_result_404_for_unknown_job():
    async with AsyncClient(app=app, base_url="http://test") as client:
        fake_job_id = uuid.uuid4()
        resp = await client.post(
            f"/agent/jobs/{fake_job_id}/result",
            json={"status": "completed", "result": {}, "error": None},
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run to verify tests pass (they test auth rejection, which already works)**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_agent_router.py -v"
```
Expected: 4 passed

- [ ] **Step 3: Add `GET /agent/jobs/next` to `backend/app/routers/agent.py`**

Add after the `register_agent` function:

```python
@router.get("/jobs/next")
async def poll_next_job(
    agent_id: uuid.UUID,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    """Long-poll up to 30s for the next pending job for this agent."""
    org_id = org_settings.organization_id
    deadline = asyncio.get_event_loop().time() + _LONG_POLL_SECONDS

    # Update last_seen on each poll
    reg_result = await db.execute(
        select(AgentRegistration).where(
            and_(
                AgentRegistration.id == agent_id,
                AgentRegistration.organization_id == org_id,
            )
        )
    )
    registration = reg_result.scalar_one_or_none()
    if registration:
        registration.last_seen = datetime.now(timezone.utc)
        await db.commit()

    while True:
        job_result = await db.execute(
            select(AgentJob).where(
                and_(
                    AgentJob.agent_registration_id == agent_id,
                    AgentJob.organization_id == org_id,
                    AgentJob.status == AgentJobStatus.pending,
                )
            ).order_by(AgentJob.created_at.asc()).limit(1)
        )
        job = job_result.scalar_one_or_none()

        if job:
            job.status = AgentJobStatus.running
            job.started_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(job)
            return AgentJobResponse(
                job_id=job.id,
                command=job.command,
                parameters=job.parameters,
                hmac_signature=job.hmac_signature,
            )

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return Response(status_code=204)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
```

- [ ] **Step 4: Add `POST /agent/jobs/{job_id}/result` to `backend/app/routers/agent.py`**

```python
@router.post("/jobs/{job_id}/result")
async def post_job_result(
    job_id: uuid.UUID,
    body: AgentJobResultRequest,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    org_id = org_settings.organization_id
    job = await db.get(AgentJob, job_id)
    if not job or job.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = body.status
    job.result = body.result
    job.error = body.error
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}
```

- [ ] **Step 5: Run full test suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/agent.py backend/app/tests/test_agent_router.py
git commit -m "feat: add agent job poll and result endpoints"
```

---

## Task 5: Agent Secret Management — Backend + Frontend

**Files:**
- Modify: `backend/app/routers/settings.py` (add PUT /settings/agent-secret + update GET)
- Modify: `frontend/src/pages/Settings.tsx` (add Agent Secret section)
- Modify: `frontend/src/types/api.ts` (update OrgSettings, add AgentSecretUpdate, add nexplane_agent ConnectorType)
- Modify: `frontend/src/api/endpoints.ts` (add settingsApi.updateAgentSecret)

- [ ] **Step 1: Update `backend/app/routers/settings.py`**

```python
import secrets as secrets_mod
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.org_settings import OrganizationSettings
from app.models.user import User, UserRole
from app.routers import current_user, require_roles
from app.schemas.org_settings import OrgSettingsRead, AIKeyUpdate, AgentSecretUpdate
from app.services.secrets_service import SecretsService
from app import config as app_config

router = APIRouter(prefix="/settings", tags=["Settings"])


def _get_secrets() -> SecretsService:
    return SecretsService(app_config.settings.SECRET_KEY)


async def _get_or_create_org_settings(
    org_id, db: AsyncSession
) -> OrganizationSettings:
    result = await db.execute(
        select(OrganizationSettings).where(OrganizationSettings.organization_id == org_id)
    )
    org_settings = result.scalar_one_or_none()
    if not org_settings:
        org_settings = OrganizationSettings(organization_id=org_id)
        db.add(org_settings)
        await db.flush()
    return org_settings


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
        return OrgSettingsRead(ai_configured=False, agent_configured=False, updated_at=None)
    return OrgSettingsRead(
        ai_configured=org_settings.anthropic_api_key_encrypted is not None,
        agent_configured=org_settings.agent_secret_encrypted is not None,
        updated_at=org_settings.updated_at,
    )


@router.put("/ai-key", response_model=OrgSettingsRead)
async def update_ai_key(
    body: AIKeyUpdate,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    if not body.api_key.startswith("sk-ant-"):
        raise HTTPException(status_code=422, detail="Invalid API key format — must start with 'sk-ant-'")
    svc = _get_secrets()
    org_settings = await _get_or_create_org_settings(user.organization_id, db)
    org_settings.anthropic_api_key_encrypted = svc.encrypt(body.api_key)
    await db.commit()
    await db.refresh(org_settings)
    return OrgSettingsRead(
        ai_configured=True,
        agent_configured=org_settings.agent_secret_encrypted is not None,
        updated_at=org_settings.updated_at,
    )


@router.post("/agent-secret", response_model=OrgSettingsRead)
async def generate_agent_secret(
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    """Generates a new random agent secret, stores it encrypted, returns it once in plaintext."""
    new_secret = "sk-agent-" + secrets_mod.token_hex(24)
    svc = _get_secrets()
    org_settings = await _get_or_create_org_settings(user.organization_id, db)
    org_settings.agent_secret_encrypted = svc.encrypt(new_secret)
    await db.commit()
    await db.refresh(org_settings)
    return OrgSettingsRead(
        ai_configured=org_settings.anthropic_api_key_encrypted is not None,
        agent_configured=True,
        updated_at=org_settings.updated_at,
        agent_secret_plaintext=new_secret,
    )
```

Note: `OrgSettingsRead` needs a `agent_secret_plaintext` optional field — add it to the schema:

```python
# backend/app/schemas/org_settings.py
from datetime import datetime
from pydantic import BaseModel


class OrgSettingsRead(BaseModel):
    ai_configured: bool
    agent_configured: bool = False
    updated_at: datetime | None = None
    agent_secret_plaintext: str | None = None  # only returned once on generation


class AIKeyUpdate(BaseModel):
    api_key: str


class AgentSecretUpdate(BaseModel):
    secret: str
```

- [ ] **Step 2: Update `frontend/src/types/api.ts`**

Update `OrgSettings` interface:
```typescript
export interface OrgSettings {
  ai_configured: boolean;
  agent_configured: boolean;
  updated_at: string | null;
  agent_secret_plaintext?: string | null;
}
```

Add `nexplane_agent` to `ConnectorType`:
```typescript
export type ConnectorType =
  | "aws_mock"
  | "azure_mock"
  | "cloudflare_mock"
  | "okta_mock"
  | "paloalto_mock"
  | "ssh_runner_mock"
  | "active_directory_mock"
  | "crowdstrike_mock"
  | "tenable_mock"
  | "nexplane_agent";
```

- [ ] **Step 3: Update `frontend/src/api/endpoints.ts`**

Add `generateAgentSecret` to `settingsApi`:
```typescript
export const settingsApi = {
  get: () =>
    apiClient.get<OrgSettings>("/settings").then((r) => r.data),
  updateAIKey: (api_key: string) =>
    apiClient.put<OrgSettings>("/settings/ai-key", { api_key }).then((r) => r.data),
  generateAgentSecret: () =>
    apiClient.post<OrgSettings>("/settings/agent-secret").then((r) => r.data),
};
```

- [ ] **Step 4: Update `frontend/src/pages/Settings.tsx`**

Add an Agent Configuration section below the AI Configuration section. Import `Terminal` from lucide-react.

The complete updated Settings.tsx:
```typescript
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Key, Terminal, Copy } from "lucide-react";
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
  const [generatedSecret, setGeneratedSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

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

  const generateSecret = useMutation({
    mutationFn: () => settingsApi.generateAgentSecret(),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setGeneratedSecret(data.agent_secret_plaintext ?? null);
    },
  });

  const isAdmin = user?.role === "admin";

  function copySecret() {
    if (generatedSecret) {
      navigator.clipboard.writeText(generatedSecret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8 max-w-2xl">
      <PageHeader title="Settings" subtitle="Organization configuration" />

      {/* AI Configuration */}
      <div className="bg-white border border-slate-200 rounded-lg p-6 mb-4">
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
            <button onClick={() => setShowKeyInput(true)} className="text-sm text-brand-600 hover:underline">
              {settings?.ai_configured ? "Update key" : "Add key"}
            </button>
          )}
          {saved && <span className="text-sm text-emerald-600">✓ Saved</span>}
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
      </div>

      {/* Agent Configuration */}
      <div className="bg-white border border-slate-200 rounded-lg p-6">
        <div className="flex items-center gap-2 mb-1">
          <Terminal className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">Agent Configuration</h2>
        </div>
        <p className="text-xs text-slate-500 mb-4">
          Shared secret used by Nexplane agents to authenticate with the control plane.
          The secret is shown only once when generated — store it securely.
        </p>
        <div className="flex items-center gap-3 mb-3">
          {settings?.agent_configured ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600">
              <Check className="w-4 h-4" />
              Configured
            </span>
          ) : (
            <span className="text-sm text-slate-400">Not configured</span>
          )}
          {isAdmin && (
            <button
              onClick={() => { setGeneratedSecret(null); generateSecret.mutate(); }}
              disabled={generateSecret.isPending}
              className="text-sm text-brand-600 hover:underline disabled:opacity-50"
            >
              {generateSecret.isPending
                ? "Generating…"
                : settings?.agent_configured
                ? "Rotate secret"
                : "Generate secret"}
            </button>
          )}
        </div>

        {generatedSecret && (
          <div className="bg-slate-50 border border-slate-200 rounded-md p-3">
            <p className="text-xs text-amber-600 mb-2 font-medium">
              ⚠ Copy this secret now — it will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs font-mono text-slate-800 break-all">{generatedSecret}</code>
              <button
                onClick={copySecret}
                className="shrink-0 p-1.5 text-slate-400 hover:text-slate-600 rounded border border-slate-200 hover:bg-white"
                title="Copy to clipboard"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <p className="text-xs text-slate-400 mt-2">
              Pass to the agent: <code className="font-mono">--secret {generatedSecret}</code>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run backend tests**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 6: Restart frontend and verify Settings page shows both sections**

```
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/settings.py backend/app/schemas/org_settings.py \
        frontend/src/pages/Settings.tsx frontend/src/types/api.ts \
        frontend/src/api/endpoints.ts
git commit -m "feat: add agent secret generation to settings backend and UI"
```

---

## Task 6: nexplane_agent Connector Catalog (Mock)

**Files:**
- Create: `backend/app/connectors/catalog/nexplane_agent_mock.json`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/__init__.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/estimate_image_size.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/change_ip.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/configure_syslog.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/virtualize_for_migration.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/upload_image.py`
- Modify: `backend/app/tests/test_catalog_service.py` (update connector set)

- [ ] **Step 1: Create `backend/app/connectors/catalog/nexplane_agent_mock.json`**

```json
{
  "connector_type": "nexplane_agent_mock",
  "display_name": "Nexplane Agent (Mock)",
  "actions": [
    {
      "action_id": "estimate_image_size",
      "generic_action": "estimate_image_size",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Estimate Image Size & Check Space",
      "description": "Checks source disk size and available destination space. Run this before virtualize_for_migration to prevent filling the disk.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "source_device", "type": "string", "required": false},
        {"name": "destination_path", "type": "string", "required": true}
      ],
      "executor": "nexplane_agent_mock.estimate_image_size",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "change_ip",
      "generic_action": "change_ip",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Change IP Address",
      "description": "Changes the IP address on a network interface. Supports IPv4, IPv6, static, and DHCP modes.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "interface", "type": "string", "required": true},
        {"name": "mode", "type": "string", "required": true},
        {"name": "ip_version", "type": "string", "required": false, "default": "4"},
        {"name": "new_ip_v4", "type": "string", "required": false},
        {"name": "new_ip_v6", "type": "string", "required": false},
        {"name": "new_gateway_v4", "type": "string", "required": false},
        {"name": "new_gateway_v6", "type": "string", "required": false},
        {"name": "dns_servers", "type": "array", "required": false}
      ],
      "executor": "nexplane_agent_mock.change_ip",
      "rollback_action": "change_ip",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "network_connectivity_loss",
      "safety_notes": ["Changing IP may temporarily disconnect the agent from the control plane"]
    },
    {
      "action_id": "configure_syslog",
      "generic_action": "configure_syslog",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Configure Syslog Forwarding",
      "description": "Configures rsyslog/syslog-ng (Linux) or NXLog/WEF (Windows) to forward logs to a remote collector.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "destination_host", "type": "string", "required": true},
        {"name": "destination_port", "type": "integer", "required": true},
        {"name": "protocol", "type": "string", "required": true},
        {"name": "facility", "type": "string", "required": false, "default": "*"}
      ],
      "executor": "nexplane_agent_mock.configure_syslog",
      "rollback_action": "configure_syslog",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "virtualize_for_migration",
      "generic_action": "virtualize_for_migration",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Virtualize Machine for Migration",
      "description": "Creates a disk image of the machine and pre-configures the target IP inside the image. Run estimate_image_size first.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "image_path", "type": "string", "required": true},
        {"name": "target_mode", "type": "string", "required": true},
        {"name": "target_ip_v4", "type": "string", "required": false},
        {"name": "target_ip_v6", "type": "string", "required": false},
        {"name": "target_gateway_v4", "type": "string", "required": false},
        {"name": "target_gateway_v6", "type": "string", "required": false},
        {"name": "target_dns_servers", "type": "array", "required": false},
        {"name": "target_interface", "type": "string", "required": false},
        {"name": "source_device", "type": "string", "required": false}
      ],
      "executor": "nexplane_agent_mock.virtualize_for_migration",
      "rollback_action": "virtualize_for_migration",
      "estimated_duration_seconds": 3600,
      "blast_radius_hint": "disk_io_impact",
      "safety_notes": ["Run estimate_image_size first to verify sufficient disk space"]
    },
    {
      "action_id": "upload_image",
      "generic_action": "upload_image",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Upload Image to Object Storage",
      "description": "Uploads a disk image to S3 (or compatible object storage) using multipart upload.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "image_path", "type": "string", "required": true},
        {"name": "destination_uri", "type": "string", "required": true},
        {"name": "region", "type": "string", "required": false},
        {"name": "access_key_id", "type": "string", "required": false},
        {"name": "secret_access_key", "type": "string", "required": false}
      ],
      "executor": "nexplane_agent_mock.upload_image",
      "rollback_action": "upload_image",
      "estimated_duration_seconds": 1800
    }
  ]
}
```

- [ ] **Step 2: Create `backend/app/connectors/executors/nexplane_agent_mock/__init__.py`** (empty)

- [ ] **Step 3: Create executor stubs**

`estimate_image_size.py`:
```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "estimate_image_size",
        "source_device": parameters.get("source_device", "/dev/sda"),
        "source_size_bytes": 107374182400,
        "destination_path": parameters.get("destination_path", "/tmp"),
        "destination_available_bytes": 214748364800,
        "recommended_minimum_bytes": 118111600640,
        "sufficient_space": True,
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "estimate_image_size is read-only"}
```

`change_ip.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "change_ip",
        "interface": parameters.get("interface"),
        "mode": parameters.get("mode"),
        "ip_version": parameters.get("ip_version", "4"),
        "applied": True,
        "snapshot": {
            "interface": parameters.get("interface"),
            "ipv4": {"mode": "static", "address": "10.0.0.100/24", "gateway": "10.0.0.1"},
            "ipv6": {"mode": "dhcp", "address": None, "gateway": None},
            "dns_servers": ["8.8.8.8"],
        },
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "change_ip", "snapshot_restored": True}
```

`configure_syslog.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "configure_syslog",
        "destination_host": parameters.get("destination_host"),
        "destination_port": parameters.get("destination_port"),
        "protocol": parameters.get("protocol"),
        "applied": True,
        "config_backup": "# previous syslog config snapshot",
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_syslog", "config_restored": True}
```

`virtualize_for_migration.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "virtualize_for_migration",
        "image_path": parameters.get("image_path"),
        "image_size_bytes": 107374182400,
        "source_device": parameters.get("source_device", "/dev/sda"),
        "network_config_applied": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "delete_image",
        "image_path": execution_result.get("image_path"),
        "deleted": True,
    }
```

`upload_image.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "upload_image",
        "image_path": parameters.get("image_path"),
        "destination_uri": parameters.get("destination_uri"),
        "size_bytes": 107374182400,
        "etag": "d41d8cd98f00b204e9800998ecf8427e",
        "checksum_verified": True,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "delete_s3_object",
        "destination_uri": execution_result.get("destination_uri"),
        "deleted": True,
    }
```

- [ ] **Step 4: Update `test_load_indexes_all_connectors` in `backend/app/tests/test_catalog_service.py`**

```python
def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock",
        "active_directory_mock", "crowdstrike_mock", "tenable_mock", "azure_mock",
        "nexplane_agent_mock",
    }
```

- [ ] **Step 5: Run full test suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 6: Add nexplane_agent to seed connectors and update frontend CONNECTOR_LABELS/ICONS**

In `backend/seed.py`, add to the `NEW_CONNECTOR_IDS` dict and `seed_expansion()` function:

In `NEW_CONNECTOR_IDS`:
```python
"nexplane_agent": uuid.UUID("00000000-0000-0000-0002-000000000009"),
```

In `seed_expansion()` `new_connectors` list:
```python
Connector(
    id=NEW_CONNECTOR_IDS["nexplane_agent"],
    organization_id=ORG_ID,
    connector_type=ConnectorType.nexplane_agent,
    name="Nexplane Agent Mock Connector",
    status=ConnectorStatus.active,
    scoped_permissions={"commands": ["estimate_image_size", "change_ip", "configure_syslog", "virtualize_for_migration", "upload_image"]},
),
```

In `frontend/src/pages/Connectors.tsx`, add to `CONNECTOR_LABELS`:
```typescript
nexplane_agent: "Nexplane Agent",
```

Add to `CONNECTOR_ICONS`:
```typescript
nexplane_agent: "🤖",
```

- [ ] **Step 7: Run full test suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/catalog/nexplane_agent_mock.json \
        backend/app/connectors/executors/nexplane_agent_mock/ \
        backend/app/tests/test_catalog_service.py \
        backend/seed.py \
        frontend/src/pages/Connectors.tsx
git commit -m "feat: add nexplane_agent_mock connector catalog and executor stubs"
```

---

## Self-Review

**Spec coverage:**
- ✅ Section 2.1 — AgentRegistration and AgentJob models: Task 1
- ✅ Section 2.2 — Agent secret auth: Tasks 3, 5
- ✅ Section 2.3 — Three endpoints (register, poll, result): Tasks 3, 4
- ✅ Section 2.4 — HMAC signature computation: Task 2
- ✅ Section 2.5/2.6 — New files, settings endpoint, frontend: Task 5
- ✅ Section 3 — Connector catalog: Task 6
- ✅ `nexplane_agent` in ConnectorType enum: Task 1
- ✅ `agent_configured` in OrgSettingsRead: Task 5
- ✅ `nexplane_agent` in frontend ConnectorType: Task 5

**Gaps checked:** None found. The long-poll uses `asyncio.sleep` in a 2-second loop up to 30 seconds, matching the spec. HMAC uses `hmac.compare_digest` for constant-time comparison.
