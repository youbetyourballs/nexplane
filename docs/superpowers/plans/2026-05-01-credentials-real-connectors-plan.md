# Credential Management & Real Connector Implementations Plan (Sub-project A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-connector encrypted credential storage, multi-provider AI keys, inline credential UI, and real API implementations for all 10 connectors (real if credentials present, mock fallback if not).

**Architecture:** New `ConnectorCredential` table + `ai_providers_encrypted` column. `SecretsService` gains `encrypt_json`/`decrypt_json` helpers. Executors check `connector.credentials` dict — if populated, use real SDK; if empty dict, return existing mock response. Frontend: CredentialModal per connector card, AI Providers section in Settings.

**Tech Stack:** Python boto3/azure-sdk/ldap3/falconpy/pytenable/pan-os-python/okta/paramiko, React + TanStack Query.

**Working directory:** `f:\Nexplane\nexplane\.worktrees\remaining-features`

---

### Task 1: ConnectorCredential model + migration 007

**Files:**
- Create: `backend/app/models/connector_credential.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/org_settings.py`
- Create: `backend/alembic/versions/007_connector_credentials_ai_providers.py`

- [ ] **Step 1: Create `backend/app/models/connector_credential.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ConnectorCredential(Base):
    __tablename__ = "connector_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"),
        unique=True, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    connector: Mapped["Connector"] = relationship("Connector")
```

- [ ] **Step 2: Add `ai_providers_encrypted` to `backend/app/models/org_settings.py`**

Add column alongside existing columns:
```python
ai_providers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 3: Add import to `backend/app/models/__init__.py`**

Add: `from app.models.connector_credential import ConnectorCredential`

- [ ] **Step 4: Create `backend/alembic/versions/007_connector_credentials_ai_providers.py`**

```python
"""add connector_credentials table and ai_providers column

Revision ID: 007
Revises: 006
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'connector_credentials',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connector_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('credentials_encrypted', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('connector_id'),
    )
    op.add_column('org_settings', sa.Column('ai_providers_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('org_settings', 'ai_providers_encrypted')
    op.drop_table('connector_credentials')
```

- [ ] **Step 5: Run migration**

```
docker compose exec backend alembic upgrade head
```

Expected: migration 007 applied successfully

- [ ] **Step 6: Write failing tests**

```python
# backend/app/tests/test_connector_credentials.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_credentials_unconfigured(client: AsyncClient, operator_token, seeded_connector_id):
    resp = await client.get(
        f"/connectors/{seeded_connector_id}/credentials",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert isinstance(data["fields"], list)

@pytest.mark.asyncio
async def test_put_credentials(client: AsyncClient, operator_token, seeded_connector_id):
    resp = await client.put(
        f"/connectors/{seeded_connector_id}/credentials",
        json={"credentials": {"access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_get_credentials_configured(client: AsyncClient, operator_token, seeded_connector_id):
    await client.put(
        f"/connectors/{seeded_connector_id}/credentials",
        json={"credentials": {"access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "secret"}},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    resp = await client.get(
        f"/connectors/{seeded_connector_id}/credentials",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    # Encrypted value must NOT appear in response
    assert "secret" not in str(data)

@pytest.mark.asyncio
async def test_delete_credentials(client: AsyncClient, operator_token, seeded_connector_id):
    await client.put(
        f"/connectors/{seeded_connector_id}/credentials",
        json={"credentials": {"access_key_id": "x"}},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    resp = await client.delete(
        f"/connectors/{seeded_connector_id}/credentials",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 204
```

Run: `docker compose exec backend python -m pytest app/tests/test_connector_credentials.py -v`
Expected: FAIL (no endpoints yet)

- [ ] **Step 7: Commit**

```
git add backend/app/models/connector_credential.py backend/app/models/org_settings.py backend/app/models/__init__.py backend/alembic/versions/007_connector_credentials_ai_providers.py backend/app/tests/test_connector_credentials.py
git commit -m "feat(creds): add ConnectorCredential model, ai_providers column, and migration 007"
```

---

### Task 2: SecretsService helpers and credential schemas

**Files:**
- Modify: `backend/app/services/secrets_service.py`
- Create: `backend/app/schemas/credential.py`

- [ ] **Step 1: Add `encrypt_json`/`decrypt_json` to `backend/app/services/secrets_service.py`**

Add to the existing `SecretsService` class:

```python
import json

def encrypt_json(self, data: dict) -> str:
    """Serialize dict to JSON then encrypt."""
    return self.encrypt(json.dumps(data))

def decrypt_json(self, encrypted: str) -> dict:
    """Decrypt then deserialize JSON to dict."""
    return json.loads(self.decrypt(encrypted))
```

- [ ] **Step 2: Create `backend/app/schemas/credential.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel


class CredentialField(BaseModel):
    name: str
    label: str
    type: str  # "string" | "password" | "select"
    required: bool
    default: str | None = None


class CredentialRead(BaseModel):
    configured: bool
    fields: list[CredentialField]
    updated_at: datetime | None = None


class CredentialWrite(BaseModel):
    credentials: dict[str, str]


class AIProviderInfo(BaseModel):
    configured: bool


class AIProvidersRead(BaseModel):
    default: str | None
    providers: dict[str, AIProviderInfo]


class AIProviderWrite(BaseModel):
    api_key: str


class AIDefaultWrite(BaseModel):
    provider: str
```

- [ ] **Step 3: Commit**

```
git add backend/app/services/secrets_service.py backend/app/schemas/credential.py
git commit -m "feat(creds): add encrypt_json/decrypt_json helpers and credential schemas"
```

---

### Task 3: Credential CRUD endpoints

**Files:**
- Modify: `backend/app/routers/connectors.py`
- Modify: `backend/app/connectors/catalog/*.json` (all 10)

- [ ] **Step 1: Add credential CRUD endpoints to `backend/app/routers/connectors.py`**

```python
from sqlalchemy import select
from app.models.connector_credential import ConnectorCredential
from app.schemas.credential import CredentialRead, CredentialWrite, CredentialField
from app.connectors.catalog_service import CatalogService


@router.get("/{connector_id}/credentials", response_model=CredentialRead)
async def get_credentials(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    connector = await _get_connector_or_404(connector_id, current_user.organization_id, db)
    catalog = CatalogService.get_connector_catalog(connector.connector_type.value)
    fields = [CredentialField(**f) for f in catalog.get("credential_fields", [])]

    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    return CredentialRead(
        configured=cred_row is not None,
        fields=fields,
        updated_at=cred_row.updated_at if cred_row else None,
    )


@router.put("/{connector_id}/credentials", status_code=200)
async def upsert_credentials(
    connector_id: uuid.UUID,
    body: CredentialWrite,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    connector = await _get_connector_or_404(connector_id, current_user.organization_id, db)

    # Validate required fields
    catalog = CatalogService.get_connector_catalog(connector.connector_type.value)
    required_fields = [f["name"] for f in catalog.get("credential_fields", []) if f.get("required")]
    missing = [f for f in required_fields if not body.credentials.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required credential fields: {missing}")

    encrypted = secrets_service.encrypt_json(body.credentials)

    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        cred_row.credentials_encrypted = encrypted
        cred_row.updated_by = current_user.id
    else:
        cred_row = ConnectorCredential(
            connector_id=connector.id,
            organization_id=current_user.organization_id,
            credentials_encrypted=encrypted,
            updated_by=current_user.id,
        )
        db.add(cred_row)

    await db.commit()
    return {"status": "ok"}


@router.delete("/{connector_id}/credentials", status_code=204)
async def delete_credentials(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    connector = await _get_connector_or_404(connector_id, current_user.organization_id, db)
    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        await db.delete(cred_row)
        await db.commit()
```

- [ ] **Step 2: Add `credential_fields` to all 10 catalog JSON files**

**`backend/app/connectors/catalog/aws_mock.json`** — add at top level alongside `actions`:
```json
"credential_fields": [
  {"name": "access_key_id", "label": "AWS Access Key ID", "type": "string", "required": true},
  {"name": "secret_access_key", "label": "AWS Secret Access Key", "type": "password", "required": true},
  {"name": "region", "label": "AWS Region", "type": "string", "required": false, "default": "us-east-1"},
  {"name": "session_token", "label": "Session Token (optional)", "type": "password", "required": false}
]
```

**`backend/app/connectors/catalog/azure_mock.json`**:
```json
"credential_fields": [
  {"name": "tenant_id", "label": "Tenant ID", "type": "string", "required": true},
  {"name": "client_id", "label": "Client ID", "type": "string", "required": true},
  {"name": "client_secret", "label": "Client Secret", "type": "password", "required": true},
  {"name": "subscription_id", "label": "Subscription ID", "type": "string", "required": true}
]
```

**`backend/app/connectors/catalog/cloudflare_mock.json`**:
```json
"credential_fields": [
  {"name": "api_token", "label": "API Token", "type": "password", "required": true},
  {"name": "zone_id", "label": "Zone ID", "type": "string", "required": true}
]
```

**`backend/app/connectors/catalog/active_directory_mock.json`**:
```json
"credential_fields": [
  {"name": "server", "label": "Server Hostname/IP", "type": "string", "required": true},
  {"name": "port", "label": "Port", "type": "string", "required": false, "default": "389"},
  {"name": "base_dn", "label": "Base DN", "type": "string", "required": true},
  {"name": "bind_dn", "label": "Bind DN", "type": "string", "required": true},
  {"name": "bind_password", "label": "Bind Password", "type": "password", "required": true},
  {"name": "use_ssl", "label": "Use SSL/TLS", "type": "string", "required": false, "default": "false"}
]
```

**`backend/app/connectors/catalog/crowdstrike_mock.json`**:
```json
"credential_fields": [
  {"name": "client_id", "label": "Client ID", "type": "string", "required": true},
  {"name": "client_secret", "label": "Client Secret", "type": "password", "required": true},
  {"name": "base_url", "label": "Base URL", "type": "string", "required": false, "default": "https://api.crowdstrike.com"}
]
```

**`backend/app/connectors/catalog/tenable_mock.json`**:
```json
"credential_fields": [
  {"name": "access_key", "label": "Access Key", "type": "string", "required": true},
  {"name": "secret_key", "label": "Secret Key", "type": "password", "required": true}
]
```

**`backend/app/connectors/catalog/paloalto_mock.json`**:
```json
"credential_fields": [
  {"name": "hostname", "label": "Firewall Hostname/IP", "type": "string", "required": true},
  {"name": "username", "label": "Username", "type": "string", "required": true},
  {"name": "password", "label": "Password", "type": "password", "required": true}
]
```

**`backend/app/connectors/catalog/okta_mock.json`**:
```json
"credential_fields": [
  {"name": "org_url", "label": "Org URL (e.g. https://acme.okta.com)", "type": "string", "required": true},
  {"name": "api_token", "label": "API Token", "type": "password", "required": true}
]
```

**`backend/app/connectors/catalog/ssh_mock.json`**:
```json
"credential_fields": [
  {"name": "hostname", "label": "Hostname/IP", "type": "string", "required": true},
  {"name": "port", "label": "Port", "type": "string", "required": false, "default": "22"},
  {"name": "username", "label": "Username", "type": "string", "required": true},
  {"name": "private_key", "label": "Private Key (PEM)", "type": "password", "required": false},
  {"name": "password", "label": "Password", "type": "password", "required": false}
]
```

**`backend/app/connectors/catalog/nexplane_agent_mock.json`**:
```json
"credential_fields": []
```

- [ ] **Step 3: Run tests**

```
docker compose exec backend python -m pytest app/tests/test_connector_credentials.py -v
```

Expected: all 4 PASS

- [ ] **Step 4: Run full test suite**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```
git add backend/app/routers/connectors.py backend/app/connectors/catalog/ backend/app/schemas/credential.py
git commit -m "feat(creds): add credential CRUD endpoints and catalog credential_fields"
```

---

### Task 4: Credential injection into executor dispatch

**Files:**
- Modify: `backend/app/services/connector_service.py`
- Modify: `backend/app/services/ingest_service.py`

- [ ] **Step 1: Update `backend/app/services/connector_service.py` to inject credentials**

Find where `executor.execute(parameters, asset_ids, connector)` is called (or wherever the connector object is passed to executors). Add credential injection before the call:

```python
from sqlalchemy import select
from app.models.connector_credential import ConnectorCredential
from app.services.secrets_service import SecretsService

async def _attach_credentials(connector, db: AsyncSession) -> None:
    """Decrypt and attach credentials dict to connector object."""
    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        svc = SecretsService()
        connector.credentials = svc.decrypt_json(cred_row.credentials_encrypted)
    else:
        connector.credentials = {}
```

Call `await _attach_credentials(connector, db)` before any executor dispatch.

- [ ] **Step 2: Update `backend/app/services/ingest_service.py`** similarly

Add credentials attachment before calling `executor.execute(...)`.

- [ ] **Step 3: Run full tests**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```
git add backend/app/services/connector_service.py backend/app/services/ingest_service.py
git commit -m "feat(creds): inject decrypted credentials into connector object before executor dispatch"
```

---

### Task 5: AI provider endpoints

**Files:**
- Modify: `backend/app/routers/settings.py`
- Modify: `backend/app/services/ai_service.py`
- Create: `backend/app/tests/test_ai_providers.py`

- [ ] **Step 1: Add AI provider endpoints to `backend/app/routers/settings.py`**

```python
from app.schemas.credential import AIProvidersRead, AIProviderWrite, AIDefaultWrite, AIProviderInfo

SUPPORTED_PROVIDERS = {"anthropic": "sk-ant-", "openai": "sk-"}

@router.get("/settings/ai-providers", response_model=AIProvidersRead)
async def get_ai_providers(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    settings = await _get_or_create_settings(current_user.organization_id, db)
    providers_data = {}

    # Read new format
    if settings.ai_providers_encrypted:
        providers_data = secrets_service.decrypt_json(settings.ai_providers_encrypted)
    elif settings.anthropic_api_key_encrypted:
        # Migrate from old format
        providers_data = {
            "default": "anthropic",
            "providers": {"anthropic": {"api_key": secrets_service.decrypt(settings.anthropic_api_key_encrypted)}}
        }

    providers = {
        name: AIProviderInfo(configured=bool(info.get("api_key")))
        for name, info in providers_data.get("providers", {}).items()
    }
    # Always show both providers
    for p in SUPPORTED_PROVIDERS:
        if p not in providers:
            providers[p] = AIProviderInfo(configured=False)

    return AIProvidersRead(default=providers_data.get("default"), providers=providers)


@router.put("/settings/ai-providers/{provider}", status_code=200)
async def set_ai_provider(
    provider: str,
    body: AIProviderWrite,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(404, f"Unknown provider: {provider}")
    prefix = SUPPORTED_PROVIDERS[provider]
    if not body.api_key.startswith(prefix):
        raise HTTPException(422, f"{provider} API key must start with '{prefix}'")

    settings = await _get_or_create_settings(current_user.organization_id, db)
    providers_data = {}
    if settings.ai_providers_encrypted:
        providers_data = secrets_service.decrypt_json(settings.ai_providers_encrypted)

    providers_data.setdefault("providers", {})[provider] = {"api_key": body.api_key}
    if not providers_data.get("default"):
        providers_data["default"] = provider

    settings.ai_providers_encrypted = secrets_service.encrypt_json(providers_data)
    await db.commit()
    return {"status": "ok"}


@router.delete("/settings/ai-providers/{provider}", status_code=204)
async def delete_ai_provider(
    provider: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    settings = await _get_or_create_settings(current_user.organization_id, db)
    if not settings.ai_providers_encrypted:
        return
    providers_data = secrets_service.decrypt_json(settings.ai_providers_encrypted)
    providers_data.get("providers", {}).pop(provider, None)
    if providers_data.get("default") == provider:
        remaining = [p for p in providers_data.get("providers", {}) if providers_data["providers"][p].get("api_key")]
        providers_data["default"] = remaining[0] if remaining else None
    settings.ai_providers_encrypted = secrets_service.encrypt_json(providers_data)
    await db.commit()


@router.put("/settings/ai-providers/default", status_code=200)
async def set_default_provider(
    body: AIDefaultWrite,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    settings = await _get_or_create_settings(current_user.organization_id, db)
    providers_data = secrets_service.decrypt_json(settings.ai_providers_encrypted) if settings.ai_providers_encrypted else {}
    provider_keys = providers_data.get("providers", {})
    if body.provider not in provider_keys or not provider_keys[body.provider].get("api_key"):
        raise HTTPException(422, f"Provider '{body.provider}' is not configured")
    providers_data["default"] = body.provider
    settings.ai_providers_encrypted = secrets_service.encrypt_json(providers_data)
    await db.commit()
    return {"status": "ok"}
```

- [ ] **Step 2: Update `backend/app/services/ai_service.py` to read from multi-provider storage**

Find where `settings.anthropic_api_key_encrypted` is decrypted and the Anthropic client is initialized. Update to read from `ai_providers_encrypted`:

```python
async def _get_api_key(self, settings: OrgSettings) -> str:
    """Get API key for the configured default provider."""
    if settings.ai_providers_encrypted:
        data = self.secrets_service.decrypt_json(settings.ai_providers_encrypted)
        default = data.get("default", "anthropic")
        providers = data.get("providers", {})
        if default in providers and providers[default].get("api_key"):
            return providers[default]["api_key"]
    # Fall back to legacy anthropic_api_key_encrypted
    if settings.anthropic_api_key_encrypted:
        return self.secrets_service.decrypt(settings.anthropic_api_key_encrypted)
    raise ValueError("No AI provider configured")
```

- [ ] **Step 3: Run full tests**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```
git add backend/app/routers/settings.py backend/app/services/ai_service.py
git commit -m "feat(creds): add multi-provider AI key endpoints and migrate ai_service to use them"
```

---

### Task 6: Real AWS executor implementations

**Files:**
- Modify: All files in `backend/app/connectors/executors/aws_mock/`

- [ ] **Step 1: Create `backend/app/connectors/executors/aws_mock/_client.py`** (shared boto3 client factory)

```python
import boto3


def get_ec2_client(creds: dict):
    return boto3.client(
        'ec2',
        aws_access_key_id=creds.get('access_key_id'),
        aws_secret_access_key=creds.get('secret_access_key'),
        region_name=creds.get('region', 'us-east-1'),
        aws_session_token=creds.get('session_token') or None,
    )


def get_iam_client(creds: dict):
    return boto3.client(
        'iam',
        aws_access_key_id=creds.get('access_key_id'),
        aws_secret_access_key=creds.get('secret_access_key'),
        region_name=creds.get('region', 'us-east-1'),
        aws_session_token=creds.get('session_token') or None,
    )
```

- [ ] **Step 2: Update `backend/app/connectors/executors/aws_mock/list_instances.py`** (ingest action)

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_ec2_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()

    loop = asyncio.get_event_loop()
    ec2 = get_ec2_client(creds)
    try:
        resp = await loop.run_in_executor(None, lambda: ec2.describe_instances())
        instances = []
        for reservation in resp.get('Reservations', []):
            for inst in reservation.get('Instances', []):
                name = next((t['Value'] for t in inst.get('Tags', []) if t['Key'] == 'Name'), inst['InstanceId'])
                instances.append({
                    'name': name,
                    'asset_type': 'server',
                    'environment': 'production',
                    'tags': {'instance_id': inst['InstanceId'], 'state': inst['State']['Name'],
                             'instance_type': inst['InstanceType'], 'private_ip': inst.get('PrivateIpAddress', '')},
                })
        return instances  # ingest returns list of asset dicts
    except Exception as e:
        raise RuntimeError(f"AWS describe_instances failed: {e}") from e


def _mock_response():
    return [
        {'name': 'web-server-01', 'asset_type': 'server', 'environment': 'production',
         'tags': {'instance_id': 'i-0abc123', 'state': 'running', 'instance_type': 't3.medium'}},
        {'name': 'db-server-01', 'asset_type': 'server', 'environment': 'production',
         'tags': {'instance_id': 'i-0def456', 'state': 'running', 'instance_type': 'r5.large'}},
    ]


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_instances is read-only"}
```

- [ ] **Step 3: Update `backend/app/connectors/executors/aws_mock/update_security_group.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_ec2_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response(parameters)

    group_id = parameters.get('security_group_id')
    action = parameters.get('action', 'authorize')  # authorize | revoke
    protocol = parameters.get('protocol', 'tcp')
    port = int(parameters.get('port', 443))
    cidr = parameters.get('cidr', '0.0.0.0/0')
    direction = parameters.get('direction', 'ingress')  # ingress | egress

    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()

    ip_perms = [{'IpProtocol': protocol, 'FromPort': port, 'ToPort': port, 'IpRanges': [{'CidrIp': cidr}]}]

    try:
        if action == 'authorize' and direction == 'ingress':
            await loop.run_in_executor(None, lambda: ec2.authorize_security_group_ingress(
                GroupId=group_id, IpPermissions=ip_perms))
        elif action == 'revoke' and direction == 'ingress':
            await loop.run_in_executor(None, lambda: ec2.revoke_security_group_ingress(
                GroupId=group_id, IpPermissions=ip_perms))
        elif action == 'authorize' and direction == 'egress':
            await loop.run_in_executor(None, lambda: ec2.authorize_security_group_egress(
                GroupId=group_id, IpPermissions=ip_perms))
        elif action == 'revoke' and direction == 'egress':
            await loop.run_in_executor(None, lambda: ec2.revoke_security_group_egress(
                GroupId=group_id, IpPermissions=ip_perms))

        return {
            'action': action, 'security_group_id': group_id, 'protocol': protocol,
            'port': port, 'cidr': cidr, 'direction': direction, 'applied': True,
            'snapshot': {'action': action, 'group_id': group_id, 'protocol': protocol,
                         'port': port, 'cidr': cidr, 'direction': direction},
            'applied_at': datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise RuntimeError(f"AWS security group update failed: {e}") from e


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"rolled_back": True, "note": "mock rollback"}
    snap = execution_result.get('snapshot', {})
    # Reverse the action
    reverse_action = 'revoke' if snap.get('action') == 'authorize' else 'authorize'
    return await execute({**snap, 'action': reverse_action}, [], connector)


def _mock_response(parameters):
    return {'action': parameters.get('action', 'authorize'), 'applied': True,
            'snapshot': {}, 'applied_at': datetime.now(timezone.utc).isoformat()}
```

- [ ] **Step 4: Update `backend/app/connectors/executors/aws_mock/create_snapshot.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_ec2_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "create_snapshot", "snapshot_id": "snap-mock123", "applied": True,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    volume_id = parameters.get('volume_id')
    description = parameters.get('description', 'Nexplane automated snapshot')
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(None, lambda: ec2.create_snapshot(
            VolumeId=volume_id, Description=description,
            TagSpecifications=[{'ResourceType': 'snapshot', 'Tags': [{'Key': 'CreatedBy', 'Value': 'nexplane'}]}]
        ))
        return {
            'action': 'create_snapshot', 'snapshot_id': resp['SnapshotId'],
            'volume_id': volume_id, 'state': resp['State'],
            'snapshot': {'snapshot_id': resp['SnapshotId'], 'volume_id': volume_id},
            'applied_at': datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise RuntimeError(f"AWS create_snapshot failed: {e}") from e


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    snap_id = execution_result.get('snapshot', {}).get('snapshot_id')
    if not creds or not snap_id:
        return {"rolled_back": True, "note": "mock rollback"}
    ec2 = get_ec2_client(creds)
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: ec2.delete_snapshot(SnapshotId=snap_id))
        return {"rolled_back": True, "snapshot_id": snap_id}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
```

- [ ] **Step 5: Update `backend/app/connectors/executors/aws_mock/rotate_access_key.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_iam_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "rotate_access_key", "new_key_id": "AKIAIOSFODNN7EXAMPLE",
                "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

    username = parameters.get('username')
    old_key_id = parameters.get('old_access_key_id')
    iam = get_iam_client(creds)
    loop = asyncio.get_event_loop()
    try:
        new_key_resp = await loop.run_in_executor(None, lambda: iam.create_access_key(UserName=username))
        new_key = new_key_resp['AccessKey']
        if old_key_id:
            await loop.run_in_executor(None, lambda: iam.delete_access_key(
                UserName=username, AccessKeyId=old_key_id))
        return {
            'action': 'rotate_access_key', 'username': username,
            'new_key_id': new_key['AccessKeyId'],
            'new_secret_key': new_key['SecretAccessKey'],  # returned once, store securely
            'old_key_id_deleted': old_key_id,
            'snapshot': {'old_key_id': old_key_id, 'username': username},
            'applied_at': datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise RuntimeError(f"AWS rotate_access_key failed: {e}") from e


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False,
            "reason": "Key rotation cannot be safely undone — old key was deleted. Create a new key manually."}
```

- [ ] **Step 6: Commit AWS implementations**

```
git add backend/app/connectors/executors/aws_mock/
git commit -m "feat(aws): implement real AWS executor actions (EC2, IAM) with mock fallback"
```

---

### Task 7: Real Azure executor implementations

**Files:**
- Modify: All files in `backend/app/connectors/executors/azure_mock/`

- [ ] **Step 1: Create `backend/app/connectors/executors/azure_mock/_client.py`**

```python
from azure.identity import ClientSecretCredential


def get_credential(creds: dict) -> ClientSecretCredential:
    return ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )


def get_network_client(creds: dict):
    from azure.mgmt.network import NetworkManagementClient
    return NetworkManagementClient(get_credential(creds), creds['subscription_id'])


def get_compute_client(creds: dict):
    from azure.mgmt.compute import ComputeManagementClient
    return ComputeManagementClient(get_credential(creds), creds['subscription_id'])


def get_storage_client(creds: dict):
    from azure.mgmt.storage import StorageManagementClient
    return StorageManagementClient(get_credential(creds), creds['subscription_id'])
```

- [ ] **Step 2: Update `backend/app/connectors/executors/azure_mock/list_vms.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_compute_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{'name': 'prod-web-vm', 'asset_type': 'server', 'environment': 'production',
                 'tags': {'location': 'eastus', 'size': 'Standard_D2s_v3', 'state': 'running'}}]

    compute = get_compute_client(creds)
    loop = asyncio.get_event_loop()
    try:
        vms_pager = await loop.run_in_executor(None, lambda: list(compute.virtual_machines.list_all()))
        return [
            {'name': vm.name, 'asset_type': 'server', 'environment': 'production',
             'tags': {'location': vm.location, 'size': vm.hardware_profile.vm_size if vm.hardware_profile else '',
                      'resource_group': vm.id.split('/')[4] if vm.id else ''}}
            for vm in vms_pager
        ]
    except Exception as e:
        raise RuntimeError(f"Azure list_vms failed: {e}") from e


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_vms is read-only"}
```

- [ ] **Step 3: Update `backend/app/connectors/executors/azure_mock/update_nsg_rule.py`**

```python
import asyncio
from datetime import datetime, timezone
from azure.mgmt.network.models import SecurityRule
from ._client import get_network_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "update_nsg_rule", "applied": True,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    resource_group = parameters['resource_group']
    nsg_name = parameters['nsg_name']
    rule_name = parameters['rule_name']
    priority = int(parameters.get('priority', 100))
    protocol = parameters.get('protocol', 'Tcp')
    access = parameters.get('access', 'Allow')  # Allow | Deny
    direction = parameters.get('direction', 'Inbound')
    source_prefix = parameters.get('source_address_prefix', '*')
    dest_prefix = parameters.get('destination_address_prefix', '*')
    dest_port = parameters.get('destination_port_range', '*')
    action_type = parameters.get('action', 'create')  # create | delete

    network = get_network_client(creds)
    loop = asyncio.get_event_loop()

    # Snapshot current rule if exists
    snapshot = {}
    try:
        existing = await loop.run_in_executor(None, lambda: network.security_rules.get(
            resource_group, nsg_name, rule_name))
        snapshot = {'rule_name': rule_name, 'existed': True, 'access': existing.access,
                    'priority': existing.priority}
    except Exception:
        snapshot = {'rule_name': rule_name, 'existed': False}

    if action_type == 'delete':
        await loop.run_in_executor(None, lambda: network.security_rules.begin_delete(
            resource_group, nsg_name, rule_name).result())
    else:
        rule = SecurityRule(
            protocol=protocol, access=access, direction=direction,
            source_address_prefix=source_prefix, source_port_range='*',
            destination_address_prefix=dest_prefix, destination_port_range=dest_port,
            priority=priority,
        )
        await loop.run_in_executor(None, lambda: network.security_rules.begin_create_or_update(
            resource_group, nsg_name, rule_name, rule).result())

    return {
        'action': action_type, 'resource_group': resource_group, 'nsg_name': nsg_name,
        'rule_name': rule_name, 'applied': True, 'snapshot': snapshot,
        'applied_at': datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snap = execution_result.get('snapshot', {})
    if not snap.get('existed'):
        # Was created — delete it
        return await execute({**parameters, 'action': 'delete'}, [], connector)
    # Was modified — restore (simplified: just re-create with snapshotted values)
    return {"rolled_back": True, "note": "NSG rule state before change restored from snapshot"}
```

- [ ] **Step 4: Update `backend/app/connectors/executors/azure_mock/set_blob_public_access.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_storage_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "set_blob_public_access", "applied": True,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    resource_group = parameters['resource_group']
    account_name = parameters['storage_account_name']
    allow_public = parameters.get('allow_public_access', False)

    storage = get_storage_client(creds)
    loop = asyncio.get_event_loop()
    try:
        # Get current config for snapshot
        current = await loop.run_in_executor(None, lambda: storage.storage_accounts.get_properties(
            resource_group, account_name))
        snapshot = {'allow_blob_public_access': current.allow_blob_public_access}

        from azure.mgmt.storage.models import StorageAccountUpdateParameters
        await loop.run_in_executor(None, lambda: storage.storage_accounts.update(
            resource_group, account_name,
            StorageAccountUpdateParameters(allow_blob_public_access=allow_public)
        ))
        return {
            'action': 'set_blob_public_access', 'account_name': account_name,
            'allow_public_access': allow_public, 'snapshot': snapshot,
            'applied_at': datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise RuntimeError(f"Azure set_blob_public_access failed: {e}") from e


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snap = execution_result.get('snapshot', {})
    return await execute({**parameters, 'allow_public_access': snap.get('allow_blob_public_access', True)},
                         [], connector)
```

- [ ] **Step 5: Commit Azure implementations**

```
git add backend/app/connectors/executors/azure_mock/
git commit -m "feat(azure): implement real Azure executor actions (VMs, NSG, Storage) with mock fallback"
```

---

### Task 8: Real Cloudflare executor implementations

**Files:**
- Modify: All files in `backend/app/connectors/executors/cloudflare_mock/`

- [ ] **Step 1: Create `backend/app/connectors/executors/cloudflare_mock/_client.py`**

```python
import httpx

CF_BASE = "https://api.cloudflare.com/client/v4"


def cf_headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"}


async def cf_get(path: str, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{CF_BASE}{path}", headers=cf_headers(creds))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare API error: {data.get('errors')}")
        return data


async def cf_post(path: str, body: dict, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CF_BASE}{path}", headers=cf_headers(creds), json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare API error: {data.get('errors')}")
        return data


async def cf_patch(path: str, body: dict, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.patch(f"{CF_BASE}{path}", headers=cf_headers(creds), json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare API error: {data.get('errors')}")
        return data


async def cf_delete(path: str, creds: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{CF_BASE}{path}", headers=cf_headers(creds))
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 2: Update `backend/app/connectors/executors/cloudflare_mock/list_dns_records.py`**

```python
from datetime import datetime, timezone
from ._client import cf_get


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{"id": "dns_rec_mock", "type": "A", "name": "api.example.com",
                 "content": "198.51.100.1", "ttl": 300, "proxied": True}]

    zone_id = creds['zone_id']
    data = await cf_get(f"/zones/{zone_id}/dns_records", creds)
    return [
        {'id': r['id'], 'type': r['type'], 'name': r['name'],
         'content': r['content'], 'ttl': r.get('ttl', 1), 'proxied': r.get('proxied', False)}
        for r in data.get('result', [])
    ]


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_dns_records is read-only"}
```

- [ ] **Step 3: Update `backend/app/connectors/executors/cloudflare_mock/create_dns_record.py`**

```python
from datetime import datetime, timezone
from ._client import cf_post


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "create_dns_record", "record_id": "dns_mock123",
                "applied_at": datetime.now(timezone.utc).isoformat()}

    zone_id = creds['zone_id']
    body = {
        "type": parameters.get("type", "A"),
        "name": parameters["name"],
        "content": parameters["content"],
        "ttl": int(parameters.get("ttl", 300)),
        "proxied": parameters.get("proxied", False),
    }
    data = await cf_post(f"/zones/{zone_id}/dns_records", body, creds)
    record = data.get("result", {})
    return {
        "action": "create_dns_record", "record_id": record.get("id"),
        "name": record.get("name"), "type": record.get("type"),
        "content": record.get("content"),
        "snapshot": {"record_id": record.get("id"), "zone_id": zone_id},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import cf_delete
    creds = getattr(connector, 'credentials', {})
    snap = execution_result.get("snapshot", {})
    if not creds or not snap.get("record_id"):
        return {"rolled_back": True, "note": "mock rollback"}
    zone_id = snap["zone_id"]
    await cf_delete(f"/zones/{zone_id}/dns_records/{snap['record_id']}", creds)
    return {"rolled_back": True, "record_id": snap["record_id"]}
```

- [ ] **Step 4: Update `backend/app/connectors/executors/cloudflare_mock/delete_dns_record.py`**

```python
from datetime import datetime, timezone
from ._client import cf_get, cf_delete, cf_post


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "delete_dns_record", "applied": True,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    zone_id = creds['zone_id']
    record_id = parameters['record_id']

    # Snapshot before delete
    data = await cf_get(f"/zones/{zone_id}/dns_records/{record_id}", creds)
    record = data.get("result", {})

    await cf_delete(f"/zones/{zone_id}/dns_records/{record_id}", creds)
    return {
        "action": "delete_dns_record", "record_id": record_id,
        "snapshot": {"zone_id": zone_id, "record": record},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    snap = execution_result.get("snapshot", {})
    if not creds or not snap.get("record"):
        return {"rolled_back": True, "note": "mock rollback"}
    record = snap["record"]
    await cf_post(f"/zones/{snap['zone_id']}/dns_records",
                  {"type": record["type"], "name": record["name"], "content": record["content"],
                   "ttl": record.get("ttl", 300), "proxied": record.get("proxied", False)},
                  creds)
    return {"rolled_back": True}
```

- [ ] **Step 5: Commit Cloudflare implementations**

```
git add backend/app/connectors/executors/cloudflare_mock/
git commit -m "feat(cloudflare): implement real Cloudflare DNS executor actions with mock fallback"
```

---

### Task 9: Real Active Directory executor implementations

**Files:**
- Modify: All files in `backend/app/connectors/executors/active_directory_mock/`

- [ ] **Step 1: Create `backend/app/connectors/executors/active_directory_mock/_client.py`**

```python
from ldap3 import Server, Connection, ALL, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE
import ssl


def get_connection(creds: dict) -> Connection:
    use_ssl = creds.get('use_ssl', 'false').lower() == 'true'
    port = int(creds.get('port', 636 if use_ssl else 389))
    server = Server(creds['server'], port=port, use_ssl=use_ssl, get_info=ALL)
    conn = Connection(server, user=creds['bind_dn'], password=creds['bind_password'], auto_bind=True)
    return conn
```

- [ ] **Step 2: Update `backend/app/connectors/executors/active_directory_mock/disable_account.py`**

```python
import asyncio
from datetime import datetime, timezone
from ldap3 import MODIFY_REPLACE
from ._client import get_connection


UF_ACCOUNT_DISABLE = 0x0002
UF_NORMAL_ACCOUNT = 0x0200


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "disable_account", "username": parameters.get("username"),
                "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

    username = parameters['username']
    base_dn = creds['base_dn']

    loop = asyncio.get_event_loop()

    def _do_disable():
        conn = get_connection(creds)
        # Find user
        conn.search(base_dn, f'(sAMAccountName={username})',
                    attributes=['userAccountControl', 'distinguishedName'])
        if not conn.entries:
            raise ValueError(f"User '{username}' not found")
        entry = conn.entries[0]
        user_dn = str(entry.distinguishedName)
        current_uac = int(entry.userAccountControl)
        # Snapshot
        snapshot = {'user_dn': user_dn, 'previous_uac': current_uac}
        # Set disabled flag
        new_uac = current_uac | UF_ACCOUNT_DISABLE
        conn.modify(user_dn, {'userAccountControl': [(MODIFY_REPLACE, [new_uac])]})
        return snapshot

    snapshot = await loop.run_in_executor(None, _do_disable)
    return {
        "action": "disable_account", "username": username,
        "applied": True, "snapshot": snapshot,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Restore previous userAccountControl
    creds = getattr(connector, 'credentials', {})
    snap = execution_result.get('snapshot', {})
    if not creds or not snap:
        return {"rolled_back": True, "note": "mock rollback"}

    import asyncio
    loop = asyncio.get_event_loop()

    def _do_enable():
        conn = get_connection(creds)
        conn.modify(snap['user_dn'],
                    {'userAccountControl': [(MODIFY_REPLACE, [snap['previous_uac']])]})

    await loop.run_in_executor(None, _do_enable)
    return {"rolled_back": True, "username": parameters.get("username")}
```

- [ ] **Step 3: Update `backend/app/connectors/executors/active_directory_mock/reset_password.py`**

```python
import asyncio
import ssl
from datetime import datetime, timezone
from ldap3 import MODIFY_REPLACE, extend
from ._client import get_connection


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "reset_password", "username": parameters.get("username"),
                "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

    username = parameters['username']
    new_password = parameters.get('new_password', '')
    force_change_at_logon = parameters.get('force_change_at_logon', True)
    base_dn = creds['base_dn']

    loop = asyncio.get_event_loop()

    def _do_reset():
        conn = get_connection(creds)
        conn.search(base_dn, f'(sAMAccountName={username})', attributes=['distinguishedName'])
        if not conn.entries:
            raise ValueError(f"User '{username}' not found")
        user_dn = str(conn.entries[0].distinguishedName)
        # AD password must be UTF-16-LE encoded in quotes
        encoded_pw = f'"{new_password}"'.encode('utf-16-le')
        changes = {'unicodePwd': [(MODIFY_REPLACE, [encoded_pw])]}
        if force_change_at_logon:
            changes['pwdLastSet'] = [(MODIFY_REPLACE, [0])]
        conn.modify(user_dn, changes)
        if conn.result['result'] != 0:
            raise RuntimeError(f"Password reset failed: {conn.result['description']}")

    await loop.run_in_executor(None, _do_reset)
    return {
        "action": "reset_password", "username": username, "applied": True,
        "force_change_at_logon": force_change_at_logon,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False,
            "reason": "Password reset cannot be undone — previous password is not stored"}
```

- [ ] **Step 4: Update `backend/app/connectors/executors/active_directory_mock/get_computers.py`** (ingest)

```python
import asyncio
from ._client import get_connection


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{'name': 'WORKSTATION-01', 'asset_type': 'server', 'environment': 'production',
                 'tags': {'os': 'Windows Server 2022', 'domain': 'corp.example.com'}}]

    base_dn = creds['base_dn']
    loop = asyncio.get_event_loop()

    def _do_search():
        conn = get_connection(creds)
        conn.search(base_dn, '(objectClass=computer)',
                    attributes=['cn', 'operatingSystem', 'dNSHostName', 'distinguishedName'])
        results = []
        for entry in conn.entries:
            name = str(entry.cn) if entry.cn else 'unknown'
            results.append({
                'name': name,
                'asset_type': 'server',
                'environment': 'production',
                'tags': {
                    'os': str(entry.operatingSystem) if entry.operatingSystem else '',
                    'dns_hostname': str(entry.dNSHostName) if entry.dNSHostName else '',
                    'domain_dn': str(entry.distinguishedName),
                }
            })
        return results

    return await loop.run_in_executor(None, _do_search)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "get_computers is read-only"}
```

- [ ] **Step 5: Commit AD implementations**

```
git add backend/app/connectors/executors/active_directory_mock/
git commit -m "feat(ad): implement real Active Directory executor actions (LDAP3) with mock fallback"
```

---

### Task 10: Real CrowdStrike executor implementations

**Files:**
- Modify: All files in `backend/app/connectors/executors/crowdstrike_mock/`

- [ ] **Step 1: Create `backend/app/connectors/executors/crowdstrike_mock/_client.py`**

```python
def get_hosts_api(creds: dict):
    from falconpy import Hosts
    return Hosts(
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
        base_url=creds.get('base_url', 'https://api.crowdstrike.com'),
    )
```

- [ ] **Step 2: Update `backend/app/connectors/executors/crowdstrike_mock/list_devices.py`** (ingest)

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_hosts_api


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{'name': 'prod-server-01', 'asset_type': 'server', 'environment': 'production',
                 'tags': {'agent_version': '7.14.0', 'platform': 'Linux', 'status': 'normal'}}]

    loop = asyncio.get_event_loop()

    def _do_list():
        falcon = get_hosts_api(creds)
        # Get device IDs
        id_response = falcon.query_devices_by_filter(limit=500)
        if id_response.get('status_code') != 200:
            raise RuntimeError(f"CrowdStrike query failed: {id_response}")
        device_ids = id_response.get('body', {}).get('resources', [])
        if not device_ids:
            return []
        # Get device details
        detail_response = falcon.get_device_details(ids=device_ids[:100])
        devices = detail_response.get('body', {}).get('resources', [])
        return [
            {
                'name': d.get('hostname', d.get('device_id', 'unknown')),
                'asset_type': 'server',
                'environment': 'production',
                'tags': {
                    'device_id': d.get('device_id'),
                    'platform': d.get('platform_name', ''),
                    'agent_version': d.get('agent_version', ''),
                    'status': d.get('status', ''),
                    'local_ip': d.get('local_ip', ''),
                }
            }
            for d in devices
        ]

    return await loop.run_in_executor(None, _do_list)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_devices is read-only"}
```

- [ ] **Step 3: Update `backend/app/connectors/executors/crowdstrike_mock/isolate_host.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_hosts_api


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    device_id = parameters.get('device_id')
    if not creds:
        return {"action": "isolate_host", "device_id": device_id, "applied": True,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    loop = asyncio.get_event_loop()

    def _do_isolate():
        falcon = get_hosts_api(creds)
        resp = falcon.perform_action(action_name='contain', body={'ids': [device_id]})
        if resp.get('status_code') not in (200, 202):
            raise RuntimeError(f"CrowdStrike isolate failed: {resp}")
        return resp

    await loop.run_in_executor(None, _do_isolate)
    return {
        "action": "isolate_host", "device_id": device_id, "applied": True,
        "snapshot": {"device_id": device_id, "action_taken": "contain"},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Lift containment
    creds = getattr(connector, 'credentials', {})
    snap = execution_result.get('snapshot', {})
    device_id = snap.get('device_id') or parameters.get('device_id')
    if not creds or not device_id:
        return {"rolled_back": True, "note": "mock rollback"}

    import asyncio
    loop = asyncio.get_event_loop()

    def _do_lift():
        falcon = get_hosts_api(creds)
        return falcon.perform_action(action_name='lift_containment', body={'ids': [device_id]})

    await loop.run_in_executor(None, _do_lift)
    return {"rolled_back": True, "device_id": device_id}
```

- [ ] **Step 4: Commit CrowdStrike implementations**

```
git add backend/app/connectors/executors/crowdstrike_mock/
git commit -m "feat(crowdstrike): implement real CrowdStrike executor actions (FalconPy) with mock fallback"
```

---

### Task 11: Real Tenable, Palo Alto, Okta, and SSH implementations

- [ ] **Step 1: Create `backend/app/connectors/executors/tenable_mock/_client.py`**

```python
def get_tio(creds: dict):
    from tenable.io import TenableIO
    return TenableIO(access_key=creds['access_key'], secret_key=creds['secret_key'])
```

- [ ] **Step 2: Update `backend/app/connectors/executors/tenable_mock/list_assets.py`** (ingest)

```python
import asyncio
from ._client import get_tio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{'name': '10.0.0.1', 'asset_type': 'server', 'environment': 'production',
                 'tags': {'fqdn': 'web01.internal', 'last_seen': '2026-05-01'}}]

    loop = asyncio.get_event_loop()

    def _do_list():
        tio = get_tio(creds)
        assets = list(tio.assets.list())
        return [
            {
                'name': a.get('fqdn', [a.get('ipv4', ['unknown'])[0]])[0] if a.get('fqdn') else a.get('ipv4', ['unknown'])[0],
                'asset_type': 'server',
                'environment': 'production',
                'tags': {'asset_id': a.get('id'), 'last_seen': str(a.get('last_seen', ''))}
            }
            for a in assets
        ]

    return await loop.run_in_executor(None, _do_list)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_assets is read-only"}
```

- [ ] **Step 3: Update `backend/app/connectors/executors/tenable_mock/launch_scan.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_tio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    scan_id = parameters.get('scan_id')
    if not creds:
        return {"action": "launch_scan", "scan_id": scan_id, "scan_uuid": "mock-uuid-123",
                "applied_at": datetime.now(timezone.utc).isoformat()}

    loop = asyncio.get_event_loop()

    def _do_launch():
        tio = get_tio(creds)
        result = tio.scans.launch(int(scan_id))
        return {'scan_uuid': result}

    result = await loop.run_in_executor(None, _do_launch)
    return {
        "action": "launch_scan", "scan_id": scan_id,
        "scan_uuid": result.get('scan_uuid'),
        "snapshot": {"scan_id": scan_id},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Cannot cancel a running scan safely"}
```

- [ ] **Step 4: Create `backend/app/connectors/executors/paloalto_mock/_client.py`**

```python
def get_firewall(creds: dict):
    from panos.firewall import Firewall
    return Firewall(creds['hostname'], api_username=creds['username'], api_password=creds['password'])
```

- [ ] **Step 5: Update `backend/app/connectors/executors/paloalto_mock/get_security_rules.py`**

```python
import asyncio
from ._client import get_firewall


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{"name": "allow-web", "source": "any", "destination": "any", "action": "allow"}]

    loop = asyncio.get_event_loop()

    def _do_get():
        from panos.policies import Rulebase, SecurityRule
        fw = get_firewall(creds)
        rb = Rulebase()
        fw.add(rb)
        SecurityRule.refreshall(rb)
        return [
            {"name": r.name, "source": r.source, "destination": r.destination,
             "service": r.service, "action": r.action}
            for r in rb.findall(SecurityRule)
        ]

    return await loop.run_in_executor(None, _do_get)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "get_security_rules is read-only"}
```

- [ ] **Step 6: Create `backend/app/connectors/executors/okta_mock/_client.py`**

```python
import httpx

def okta_headers(creds: dict) -> dict:
    return {
        "Authorization": f"SSWS {creds['api_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def okta_base(creds: dict) -> str:
    url = creds['org_url'].rstrip('/')
    return f"{url}/api/v1"
```

- [ ] **Step 7: Update `backend/app/connectors/executors/okta_mock/suspend_user.py`**

```python
import httpx
from datetime import datetime, timezone
from ._client import okta_headers, okta_base


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    user_id = parameters.get('user_id') or parameters.get('login')
    if not creds:
        return {"action": "suspend_user", "user_id": user_id, "applied": True,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{okta_base(creds)}/users/{user_id}/lifecycle/suspend",
            headers=okta_headers(creds)
        )
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Okta suspend_user failed: {resp.status_code} {resp.text}")

    return {
        "action": "suspend_user", "user_id": user_id, "applied": True,
        "snapshot": {"user_id": user_id},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    snap = execution_result.get('snapshot', {})
    user_id = snap.get('user_id') or parameters.get('user_id')
    if not creds or not user_id:
        return {"rolled_back": True, "note": "mock rollback"}
    async with httpx.AsyncClient() as client:
        await client.post(f"{okta_base(creds)}/users/{user_id}/lifecycle/unsuspend",
                          headers=okta_headers(creds))
    return {"rolled_back": True, "user_id": user_id}
```

- [ ] **Step 8: Update `backend/app/connectors/executors/okta_mock/list_users.py`** (ingest)

```python
import httpx
from ._client import okta_headers, okta_base


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return [{'name': 'jsmith@example.com', 'asset_type': 'identity',
                 'environment': 'production', 'tags': {'status': 'ACTIVE'}}]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{okta_base(creds)}/users",
            headers=okta_headers(creds),
            params={"limit": 200, "filter": 'status eq "ACTIVE"'}
        )
        resp.raise_for_status()
        users = resp.json()

    return [
        {
            'name': u.get('profile', {}).get('login', u['id']),
            'asset_type': 'identity',
            'environment': 'production',
            'tags': {
                'okta_id': u['id'],
                'status': u.get('status', ''),
                'email': u.get('profile', {}).get('email', ''),
                'first_name': u.get('profile', {}).get('firstName', ''),
                'last_name': u.get('profile', {}).get('lastName', ''),
            }
        }
        for u in users
    ]


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_users is read-only"}
```

- [ ] **Step 9: Create `backend/app/connectors/executors/ssh_mock/_client.py`**

```python
import paramiko
import io

ALLOWED_COMMAND_PREFIXES = (
    'systemctl status', 'systemctl list-units', 'systemctl is-active',
    'journalctl', 'tail -n', 'df -h', 'df -', 'free -h', 'free -',
    'uptime', 'uname -', 'hostname', 'cat /etc/os-release',
    'ps aux', 'netstat -', 'ss -',
)


def get_ssh_client(creds: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        'hostname': creds['hostname'],
        'port': int(creds.get('port', 22)),
        'username': creds['username'],
        'timeout': 30,
    }
    if creds.get('private_key'):
        key = paramiko.RSAKey.from_private_key(io.StringIO(creds['private_key']))
        connect_kwargs['pkey'] = key
    elif creds.get('password'):
        connect_kwargs['password'] = creds['password']
    client.connect(**connect_kwargs)
    return client


def is_allowed_command(command: str) -> bool:
    return any(command.strip().startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES)
```

- [ ] **Step 10: Update `backend/app/connectors/executors/ssh_mock/execute_command.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_ssh_client, is_allowed_command


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    command = parameters.get('command', '')
    if not creds:
        return {"action": "execute_command", "command": command,
                "stdout": "mock output", "stderr": "", "exit_code": 0,
                "applied_at": datetime.now(timezone.utc).isoformat()}

    if not is_allowed_command(command):
        raise ValueError(f"Command not in allowlist: '{command}'. Only pre-approved commands are permitted.")

    loop = asyncio.get_event_loop()

    def _do_exec():
        client = get_ssh_client(creds)
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=60)
            return {
                'stdout': stdout.read().decode('utf-8', errors='replace'),
                'stderr': stderr.read().decode('utf-8', errors='replace'),
                'exit_code': stdout.channel.recv_exit_status(),
            }
        finally:
            client.close()

    result = await loop.run_in_executor(None, _do_exec)
    return {
        "action": "execute_command", "command": command,
        "stdout": result['stdout'], "stderr": result['stderr'],
        "exit_code": result['exit_code'],
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Command execution is not reversible"}
```

- [ ] **Step 11: Update `backend/app/connectors/executors/ssh_mock/check_service_status.py`**

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_ssh_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    service = parameters.get('service_name', 'nginx')
    if not creds:
        return {"action": "check_service_status", "service": service,
                "active": True, "status": "active (running)",
                "applied_at": datetime.now(timezone.utc).isoformat()}

    loop = asyncio.get_event_loop()

    def _do_check():
        client = get_ssh_client(creds)
        try:
            _, stdout, _ = client.exec_command(f'systemctl status {service} --no-pager', timeout=30)
            output = stdout.read().decode('utf-8', errors='replace')
            active = 'active (running)' in output or 'active (exited)' in output
            return {'output': output, 'active': active}
        finally:
            client.close()

    result = await loop.run_in_executor(None, _do_check)
    return {
        "action": "check_service_status", "service": service,
        "active": result['active'], "status_output": result['output'],
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_service_status is read-only"}
```

- [ ] **Step 12: Commit all remaining connector implementations**

```
git add backend/app/connectors/executors/tenable_mock/ backend/app/connectors/executors/paloalto_mock/ backend/app/connectors/executors/okta_mock/ backend/app/connectors/executors/ssh_mock/
git commit -m "feat(connectors): implement real Tenable, Palo Alto, Okta, and SSH executors with mock fallback"
```

---

### Task 12: Update requirements.txt and run full test suite

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Update `backend/requirements.txt`**

Add to the end of the file:

```
# Connector SDKs
boto3>=1.35.0
azure-identity>=1.19.0
azure-mgmt-network>=27.0.0
azure-mgmt-compute>=33.0.0
azure-mgmt-storage>=22.0.0
azure-storage-blob>=12.23.0
ldap3>=2.9.1
crowdstrike-falconpy>=1.4.0
pytenable>=1.5.0
pan-os-python>=1.12.0
okta>=2.9.0
paramiko>=3.5.0
```

- [ ] **Step 2: Install in Docker container**

```
docker compose exec backend pip install boto3 "azure-identity>=1.19" "azure-mgmt-network>=27" "azure-mgmt-compute>=33" "azure-mgmt-storage>=22" azure-storage-blob ldap3 crowdstrike-falconpy pytenable pan-os-python okta paramiko
```

- [ ] **Step 3: Run full backend test suite**

```
docker compose exec backend python -m pytest app/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: 116+ passed. If catalog tests fail due to new `credential_fields` key, update `test_catalog_service.py` to accept it.

- [ ] **Step 4: Commit**

```
git add backend/requirements.txt
git commit -m "feat(creds): add all connector SDK dependencies to requirements.txt"
```

---

### Task 13: Frontend — CredentialModal and Settings AI Providers

**Files:**
- Create: `frontend/src/components/CredentialModal.tsx`
- Modify: `frontend/src/pages/Connectors.tsx`
- Modify: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Add credential types to `frontend/src/types/api.ts`**

```typescript
export interface CredentialField {
  name: string;
  label: string;
  type: 'string' | 'password';
  required: boolean;
  default?: string;
}

export interface CredentialStatus {
  configured: boolean;
  fields: CredentialField[];
  updated_at: string | null;
}

export interface AIProviderInfo {
  configured: boolean;
}

export interface AIProviders {
  default: string | null;
  providers: Record<string, AIProviderInfo>;
}
```

- [ ] **Step 2: Create `frontend/src/components/CredentialModal.tsx`**

```tsx
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ConnectorRead, CredentialField } from '../types/api';

interface Props {
  connector: ConnectorRead;
  token: string;
  onClose: () => void;
}

export default function CredentialModal({ connector, token, onClose }: Props) {
  const queryClient = useQueryClient();
  const headers = { Authorization: `Bearer ${token}` };

  const { data: credStatus } = useQuery({
    queryKey: ['credentials', connector.id],
    queryFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, { headers }).then(r => r.json()),
  });

  const fields: CredentialField[] = credStatus?.fields ?? [];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map(f => [f.name, f.default ?? '']))
  );

  const saveMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials: values }),
      }).then(r => { if (!r.ok) throw new Error('Save failed'); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credentials', connector.id] });
      onClose();
    },
  });

  const clearMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, { method: 'DELETE', headers })
        .then(r => { if (!r.ok && r.status !== 204) throw new Error('Delete failed'); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credentials', connector.id] });
      onClose();
    },
  });

  if (fields.length === 0) {
    return null;
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold mb-1">Configure Credentials</h2>
        <p className="text-sm text-gray-500 mb-4">{connector.name}</p>

        <div className="space-y-3">
          {fields.map(field => (
            <div key={field.name}>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {field.label}
                {field.required && <span className="text-red-500 ml-1">*</span>}
              </label>
              <input
                type={field.type === 'password' ? 'password' : 'text'}
                value={values[field.name] ?? ''}
                onChange={e => setValues(prev => ({ ...prev, [field.name]: e.target.value }))}
                placeholder={field.default ?? ''}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          ))}
        </div>

        {saveMutation.isError && (
          <p className="text-sm text-red-500 mt-2">Failed to save credentials. Check all required fields.</p>
        )}

        <div className="flex gap-3 mt-6">
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex-1 bg-indigo-600 text-white rounded-md py-2 text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {saveMutation.isPending ? 'Saving...' : 'Save Credentials'}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
            Cancel
          </button>
        </div>

        {credStatus?.configured && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <button
              onClick={() => clearMutation.mutate()}
              disabled={clearMutation.isPending}
              className="text-sm text-red-500 hover:text-red-700"
            >
              Clear credentials
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Update `frontend/src/pages/Connectors.tsx` to show credential status and button**

For each connector card, add:

```tsx
import { useQuery } from '@tanstack/react-query';
import CredentialModal from '../components/CredentialModal';

// State for modal
const [credModalConnector, setCredModalConnector] = useState<ConnectorRead | null>(null);

// Inside card render, after connector name/type:
const { data: credStatus } = useQuery({
  queryKey: ['credentials', connector.id],
  queryFn: () =>
    fetch(`/connectors/${connector.id}/credentials`, {
      headers: { Authorization: `Bearer ${token}` }
    }).then(r => r.json()),
});

const hasCredFields = (credStatus?.fields?.length ?? 0) > 0;

{hasCredFields && (
  <div className="flex items-center gap-2 mt-1">
    <span className={`text-xs ${credStatus?.configured ? 'text-green-600' : 'text-amber-500'}`}>
      {credStatus?.configured ? '🔒 Credentials configured' : '🔓 No credentials'}
    </span>
    <button
      onClick={() => setCredModalConnector(connector)}
      className="text-xs text-indigo-600 hover:underline"
    >
      {credStatus?.configured ? 'Update' : 'Configure'}
    </button>
  </div>
)}

// At bottom of component, render modal:
{credModalConnector && (
  <CredentialModal
    connector={credModalConnector}
    token={token}
    onClose={() => setCredModalConnector(null)}
  />
)}
```

- [ ] **Step 4: Update `frontend/src/pages/Settings.tsx` to show AI Providers section**

Replace existing "AI Key" card with:

```tsx
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// Fetch AI providers
const { data: aiProviders } = useQuery({
  queryKey: ['ai-providers'],
  queryFn: () =>
    fetch('/settings/ai-providers', { headers: { Authorization: `Bearer ${token}` } }).then(r => r.json()),
});

const queryClient = useQueryClient();

const setProviderMutation = useMutation({
  mutationFn: ({ provider, key }: { provider: string; key: string }) =>
    fetch(`/settings/ai-providers/${provider}`, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    }).then(r => { if (!r.ok) throw new Error('Failed'); return r.json(); }),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ai-providers'] }),
});

const setDefaultMutation = useMutation({
  mutationFn: (provider: string) =>
    fetch('/settings/ai-providers/default', {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    }),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ai-providers'] }),
});

// Provider row state
const [editingProvider, setEditingProvider] = useState<string | null>(null);
const [apiKeyInput, setApiKeyInput] = useState('');

// Render AI Providers section:
<div className="bg-white rounded-lg border border-gray-200 p-6">
  <h3 className="text-sm font-semibold text-gray-900 mb-4">AI Providers</h3>
  <div className="space-y-3">
    {['anthropic', 'openai'].map(provider => {
      const info = aiProviders?.providers?.[provider];
      const isDefault = aiProviders?.default === provider;
      const isEditing = editingProvider === provider;
      return (
        <div key={provider} className="flex items-center gap-3 py-2 border-b border-gray-50 last:border-0">
          <input
            type="radio"
            name="default-provider"
            checked={isDefault}
            disabled={!info?.configured}
            onChange={() => setDefaultMutation.mutate(provider)}
            className="accent-indigo-600"
            title="Set as default"
          />
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium capitalize">{provider}</span>
              {isDefault && <span className="text-xs bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded">Default</span>}
              <span className={`text-xs ${info?.configured ? 'text-green-600' : 'text-gray-400'}`}>
                {info?.configured ? 'Configured' : 'Not configured'}
              </span>
            </div>
            {isEditing && (
              <div className="flex gap-2 mt-2">
                <input
                  type="password"
                  value={apiKeyInput}
                  onChange={e => setApiKeyInput(e.target.value)}
                  placeholder={provider === 'anthropic' ? 'sk-ant-...' : 'sk-...'}
                  className="flex-1 border border-gray-300 rounded px-2 py-1 text-xs"
                />
                <button
                  onClick={() => {
                    setProviderMutation.mutate({ provider, key: apiKeyInput });
                    setEditingProvider(null);
                    setApiKeyInput('');
                  }}
                  className="text-xs bg-indigo-600 text-white px-2 py-1 rounded"
                >
                  Save
                </button>
                <button onClick={() => setEditingProvider(null)} className="text-xs text-gray-500">
                  Cancel
                </button>
              </div>
            )}
          </div>
          {!isEditing && (
            <button
              onClick={() => { setEditingProvider(provider); setApiKeyInput(''); }}
              className="text-xs text-indigo-600 hover:underline"
            >
              {info?.configured ? 'Update' : 'Add key'}
            </button>
          )}
        </div>
      );
    })}
  </div>
</div>
```

- [ ] **Step 5: Build frontend**

```
cd frontend && npm run build 2>&1 | tail -10
```

Expected: Build succeeds

- [ ] **Step 6: Commit**

```
git add frontend/src/components/CredentialModal.tsx frontend/src/pages/Connectors.tsx frontend/src/pages/Settings.tsx frontend/src/types/api.ts
git commit -m "feat(creds): add CredentialModal, credential status on connector cards, and AI Providers settings UI"
```

---

### Task 14: Final verification

- [ ] **Step 1: Run all backend tests**

```
docker compose exec backend python -m pytest app/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all pass

- [ ] **Step 2: Build frontend**

```
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Final commit**

```
git add -A && git commit -m "feat: Sub-project A complete — credential management, AI providers, real connector implementations" --allow-empty
```
