# Connectors & Ingest Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new mock connectors (Active Directory, CrowdStrike, Tenable, Azure), an ingest action pipeline that upserts discovered assets into the database, expanded PaloAlto and SSH connectors, and 10 seeded example projects.

**Architecture:** Each new connector follows the existing pattern: a catalog JSON file defining actions with `action_type: "ingest"` or `"change"`, plus one executor module per action. A new `IngestService` dispatches ingest executors and upserts their returned asset payloads into the `assets` table using `(organization_id, name)` as the deduplication key. A new `POST /connectors/{id}/ingest/{action_id}` endpoint exposes this. The frontend gains a "Run Discovery" button per connector card.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic (PostgreSQL enum ALTER), Pydantic, React 18, TypeScript, TanStack Query, Tailwind CSS

---

## Codebase Context

Before starting, understand these conventions:
- Connector catalogs live in `backend/app/connectors/catalog/<connector_type>.json`
- Executors live in `backend/app/connectors/executors/<connector_type>/<action_id>.py`; each file has `async def execute(parameters: dict, asset_ids: list, connector) -> dict` and `async def rollback(...) -> dict`
- **Ingest executors** return `list[dict]` from `execute()` (not `dict`). They do not need `rollback`.
- The catalog service test `test_all_catalog_executors_resolve` will catch any missing executor modules automatically — run it after every new connector to verify.
- `test_load_indexes_all_connectors` in `test_catalog_service.py` hard-codes the set of connector types and must be updated when new catalogs are added.
- Fixed UUIDs for seed data: org `00000000-0000-0000-0000-000000000001`, admin user `00000000-0000-0000-0000-000000000010`.
- PostgreSQL enums require `ALTER TYPE ... ADD VALUE` — standard `op.add_column` won't work for enum changes. Downgrade is a no-op (PostgreSQL cannot remove enum values).
- The catalog file for SSH uses `"connector_type": "ssh_mock"` but the DB enum has `ssh_runner_mock`. Do not change this — leave it as-is.
- Run all tests with: `docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"`

---

## Task 1: Add `identity` asset type and new connector types — model + migration

**Files:**
- Modify: `backend/app/models/asset.py`
- Modify: `backend/app/models/connector.py`
- Create: `backend/alembic/versions/005_add_identity_type_and_connector_types.py`
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Write the failing test**

```python
# backend/app/tests/test_asset_schemas.py — add to existing file
def test_asset_type_includes_identity():
    from app.models.asset import AssetType
    assert AssetType.identity == "identity"

def test_connector_type_includes_new_connectors():
    from app.models.connector import ConnectorType
    assert ConnectorType.active_directory_mock == "active_directory_mock"
    assert ConnectorType.crowdstrike_mock == "crowdstrike_mock"
    assert ConnectorType.tenable_mock == "tenable_mock"
```

- [ ] **Step 2: Run test to verify it fails**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_asset_schemas.py::test_asset_type_includes_identity -v"
```
Expected: FAIL with `AttributeError: identity`

- [ ] **Step 3: Update `backend/app/models/asset.py`**

```python
class AssetType(str, enum.Enum):
    server = "server"
    cloud_account = "cloud_account"
    dns_zone = "dns_zone"
    firewall = "firewall"
    identity_provider = "identity_provider"
    application = "application"
    identity = "identity"
```

- [ ] **Step 4: Update `backend/app/models/connector.py`**

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
```

- [ ] **Step 5: Create migration `backend/alembic/versions/005_add_identity_type_and_connector_types.py`**

```python
"""add identity asset type and new connector types

Revision ID: 005
Revises: 004
Create Date: 2026-04-27 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'identity'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'active_directory_mock'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'crowdstrike_mock'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'tenable_mock'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values — downgrade is intentionally a no-op
    pass
```

- [ ] **Step 6: Run migration**

```
docker compose exec backend bash -c "cd /app && alembic upgrade head"
```
Expected: `005 (head)`

- [ ] **Step 7: Update `frontend/src/types/api.ts`**

Change `AssetType`:
```typescript
export type AssetType =
  | "server"
  | "cloud_account"
  | "dns_zone"
  | "firewall"
  | "identity_provider"
  | "application"
  | "identity";
```

Change `ConnectorType`:
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
  | "tenable_mock";
```

- [ ] **Step 8: Run tests to verify they pass**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_asset_schemas.py -v"
```
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/asset.py backend/app/models/connector.py \
        backend/alembic/versions/005_add_identity_type_and_connector_types.py \
        frontend/src/types/api.ts
git commit -m "feat: add identity asset type and new connector enum values"
```

---

## Task 2: IngestService + IngestResponse schema + ingest endpoint

**Files:**
- Create: `backend/app/services/ingest_service.py`
- Modify: `backend/app/schemas/connector.py`
- Modify: `backend/app/routers/connectors.py`
- Create: `backend/app/tests/test_ingest_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/app/tests/test_ingest_service.py
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ingest_service import IngestService
from app.models.asset import AssetType, Environment, Criticality


@pytest.mark.asyncio
async def test_ingest_creates_new_asset():
    """Executor returns one asset payload; service creates it and returns summary."""
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    # Mock executor module
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=[
        {
            "name": "web-server-01",
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["crowdstrike-managed"],
            "asset_metadata": {"os": "Ubuntu 22.04"},
        }
    ])

    # Mock catalog service
    mock_catalog = MagicMock()
    mock_catalog.get_action_def.return_value = {
        "action_id": "discover_endpoints",
        "action_type": "ingest",
        "executor": "crowdstrike_mock.discover_endpoints",
    }
    mock_catalog.get_executor.return_value = mock_executor

    # Mock connector
    mock_connector = MagicMock()
    mock_connector.connector_type = "crowdstrike_mock"

    # Use real DB session via conftest fixture
    # (tested via integration below; unit test uses mock session)
    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    service = IngestService(mock_catalog)
    result = await service.run("discover_endpoints", mock_connector, org_id, mock_db)

    assert result["created"] == 1
    assert result["updated"] == 0
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_updates_existing_asset():
    """Executor returns an asset that already exists; service updates it."""
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    from app.models.asset import Asset
    existing = Asset(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="web-server-01",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.low,
        asset_metadata={},
        tags=[],
    )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=[
        {
            "name": "web-server-01",
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["crowdstrike-managed"],
            "asset_metadata": {"os": "Ubuntu 22.04"},
        }
    ])

    mock_catalog = MagicMock()
    mock_catalog.get_action_def.return_value = {"action_type": "ingest", "executor": "crowdstrike_mock.discover_endpoints"}
    mock_catalog.get_executor.return_value = mock_executor

    mock_connector = MagicMock()
    mock_connector.connector_type = "crowdstrike_mock"

    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    service = IngestService(mock_catalog)
    result = await service.run("discover_endpoints", mock_connector, org_id, mock_db)

    assert result["created"] == 0
    assert result["updated"] == 1
    assert existing.criticality == Criticality.high
    assert "crowdstrike-managed" in existing.tags


@pytest.mark.asyncio
async def test_ingest_raises_for_change_action():
    """Calling ingest on a change action returns a 400-type error."""
    mock_catalog = MagicMock()
    mock_catalog.get_action_def.return_value = {"action_type": "change", "executor": "aws_mock.create_snapshot"}
    mock_connector = MagicMock()
    mock_db = AsyncMock(spec=AsyncSession)

    service = IngestService(mock_catalog)
    with pytest.raises(ValueError, match="not an ingest action"):
        await service.run("create_snapshot", mock_connector, uuid.uuid4(), mock_db)
```

- [ ] **Step 2: Run tests to verify they fail**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_ingest_service.py -v"
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ingest_service'`

- [ ] **Step 3: Create `backend/app/services/ingest_service.py`**

```python
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetType, Environment, Criticality
from app.connectors.catalog_service import ActionCatalogService


class IngestService:
    def __init__(self, catalog: ActionCatalogService):
        self._catalog = catalog

    async def run(
        self,
        action_id: str,
        connector,
        organization_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        action_def = self._catalog.get_action_def(connector.connector_type, action_id)
        if action_def.get("action_type") != "ingest":
            raise ValueError(f"'{action_id}' is not an ingest action")

        executor = self._catalog.get_executor(connector.connector_type, action_id)
        payloads: list[dict] = await executor.execute({}, [], connector)

        created = 0
        updated = 0
        upserted_assets = []

        for payload in payloads:
            name = payload["name"]
            result = await db.execute(
                select(Asset).where(
                    Asset.organization_id == organization_id,
                    Asset.name == name,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                if "asset_metadata" in payload:
                    existing.asset_metadata = {**existing.asset_metadata, **payload["asset_metadata"]}
                if "tags" in payload:
                    merged = list(set(existing.tags or []) | set(payload["tags"]))
                    existing.tags = merged
                if "criticality" in payload:
                    existing.criticality = Criticality(payload["criticality"])
                if "environment" in payload:
                    existing.environment = Environment(payload["environment"])
                upserted_assets.append(existing)
                updated += 1
            else:
                asset = Asset(
                    organization_id=organization_id,
                    name=name,
                    asset_type=AssetType(payload["asset_type"]),
                    environment=Environment(payload.get("environment", "prod")),
                    criticality=Criticality(payload.get("criticality", "medium")),
                    asset_metadata=payload.get("asset_metadata", {}),
                    tags=payload.get("tags", []),
                )
                db.add(asset)
                await db.flush()
                upserted_assets.append(asset)
                created += 1

        return {"created": created, "updated": updated, "assets": upserted_assets}
```

- [ ] **Step 4: Add `IngestResponse` to `backend/app/schemas/connector.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.connector import ConnectorType, ConnectorStatus
from app.schemas.asset import AssetRead


class ConnectorCreate(BaseModel):
    connector_type: ConnectorType
    name: str
    scoped_permissions: dict = {}


class ConnectorRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    connector_type: ConnectorType
    name: str
    status: ConnectorStatus
    scoped_permissions: dict
    created_at: datetime


class ConnectorTestResult(BaseModel):
    success: bool
    latency_ms: int
    message: str
    details: dict = {}


class IngestResponse(BaseModel):
    created: int
    updated: int
    assets: list[AssetRead]
```

- [ ] **Step 5: Add ingest endpoint to `backend/app/routers/connectors.py`**

```python
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.connector import Connector
from app.models.user import User
from app.routers import current_user
from app.schemas.connector import ConnectorCreate, ConnectorRead, ConnectorTestResult, IngestResponse
from app.services.connector_service import test_connector
from app.services.ingest_service import IngestService
from app.services.audit_service import record_event
from app.connectors.catalog_service import get_catalog_service

router = APIRouter(prefix="/connectors", tags=["Connectors"])


@router.get("", response_model=list[ConnectorRead])
async def list_connectors(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Connector).where(Connector.organization_id == user.organization_id))
    return result.scalars().all()


@router.post("", response_model=ConnectorRead, status_code=201)
async def create_connector(
    body: ConnectorCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = Connector(organization_id=user.organization_id, **body.model_dump())
    db.add(connector)
    await db.flush()
    await record_event(db, user.organization_id, "connector.created",
                       {"connector_id": str(connector.id), "type": connector.connector_type.value},
                       actor_id=user.id)
    await db.commit()
    await db.refresh(connector)
    return connector


@router.post("/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector_endpoint(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    result = await test_connector(connector.connector_type)
    return ConnectorTestResult(**result)


@router.post("/{connector_id}/ingest/{action_id}", response_model=IngestResponse)
async def run_ingest(
    connector_id: uuid.UUID,
    action_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    try:
        catalog = get_catalog_service()
        service = IngestService(catalog)
        result = await service.run(action_id, connector, user.organization_id, db)
        await db.commit()
        await record_event(db, user.organization_id, "connector.ingest_run",
                           {"connector_id": str(connector_id), "action_id": action_id,
                            "created": result["created"], "updated": result["updated"]},
                           actor_id=user.id)
        await db.commit()
        return IngestResponse(
            created=result["created"],
            updated=result["updated"],
            assets=result["assets"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (KeyError, ImportError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [ ] **Step 6: Run tests**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_ingest_service.py -v"
```
Expected: 3 passed

- [ ] **Step 7: Run full suite to check no regressions**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/ingest_service.py backend/app/schemas/connector.py \
        backend/app/routers/connectors.py backend/app/tests/test_ingest_service.py
git commit -m "feat: add IngestService, IngestResponse schema, and ingest endpoint"
```

---

## Task 3: Frontend — connector labels, icons, ingest API call, Run Discovery button

**Files:**
- Modify: `frontend/src/pages/Connectors.tsx`
- Modify: `frontend/src/api/endpoints.ts`
- Modify: `frontend/src/types/api.ts` (add `IngestResponse`)

- [ ] **Step 1: Add `IngestResponse` type to `frontend/src/types/api.ts`**

After the existing `ConnectorTestResult` interface, add:
```typescript
export interface IngestResponse {
  created: number;
  updated: number;
  assets: Asset[];
}
```

- [ ] **Step 2: Add `ingest` to `connectorsApi` in `frontend/src/api/endpoints.ts`**

```typescript
export const connectorsApi = {
  list: () => apiClient.get<Connector[]>("/connectors").then((r) => r.data),
  create: (data: ConnectorCreate) => apiClient.post<Connector>("/connectors", data).then((r) => r.data),
  test: (id: string) =>
    apiClient.post<ConnectorTestResult>(`/connectors/${id}/test`).then((r) => r.data),
  ingest: (id: string, actionId: string) =>
    apiClient.post<IngestResponse>(`/connectors/${id}/ingest/${actionId}`).then((r) => r.data),
};
```

Also add `IngestResponse` to the import at the top:
```typescript
import type {
  Token, User, Asset, AssetCreate, AssetUpdate, AssetListParams, BulkTagBody,
  Project, ProjectSummary, ProjectDetail, ProjectMember,
  ProjectCreate, ProjectUpdate, ProjectMemberCreate, ProjectMemberUpdate,
  OrgSettings, AIChatResponse, AIChatRequest,
  Connector, ConnectorCreate, ConnectorTestResult, IngestResponse,
  ChangeRequest, ChangeRequestSummary, ChangeRequestCreate,
  ChangePlan, Approval, ApprovalCreate, ExecutionRun, AuditEvent,
} from "../types/api";
```

- [ ] **Step 3: Update `frontend/src/pages/Connectors.tsx`**

Replace the entire file:

```typescript
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plug, CheckCircle2, XCircle, Loader2, Download } from "lucide-react";
import { connectorsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import type { ConnectorType, ConnectorTestResult, IngestResponse } from "../types/api";

const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  aws_mock: "AWS Mock",
  azure_mock: "Azure Mock",
  cloudflare_mock: "Cloudflare Mock",
  okta_mock: "Okta Mock",
  paloalto_mock: "Palo Alto Mock",
  ssh_runner_mock: "SSH Runner Mock",
  active_directory_mock: "Active Directory Mock",
  crowdstrike_mock: "CrowdStrike Falcon Mock",
  tenable_mock: "Tenable Mock",
};

const CONNECTOR_ICONS: Record<ConnectorType, string> = {
  aws_mock: "☁️",
  azure_mock: "🔷",
  cloudflare_mock: "🟠",
  okta_mock: "🔐",
  paloalto_mock: "🛡️",
  ssh_runner_mock: "🖥️",
  active_directory_mock: "🏢",
  crowdstrike_mock: "🦅",
  tenable_mock: "🔍",
};

// Ingest action IDs per connector type (connectors that support discovery)
const INGEST_ACTIONS: Partial<Record<ConnectorType, string>> = {
  active_directory_mock: "discover_computers",
  crowdstrike_mock: "discover_endpoints",
  tenable_mock: "discover_assets",
  azure_mock: "discover_vms",
  paloalto_mock: "ingest_traffic_logs",
};

export function Connectors() {
  const qc = useQueryClient();
  const [testResults, setTestResults] = useState<Record<string, ConnectorTestResult>>({});
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [ingestResults, setIngestResults] = useState<Record<string, IngestResponse>>({});

  const { data, isLoading } = useQuery({
    queryKey: ["connectors"],
    queryFn: connectorsApi.list,
  });

  async function testConnector(id: string) {
    setTesting((prev) => ({ ...prev, [id]: true }));
    try {
      const result = await connectorsApi.test(id);
      setTestResults((prev) => ({ ...prev, [id]: result }));
    } finally {
      setTesting((prev) => ({ ...prev, [id]: false }));
    }
  }

  const ingestMutation = useMutation({
    mutationFn: ({ id, actionId }: { id: string; actionId: string }) =>
      connectorsApi.ingest(id, actionId),
    onSuccess: (data, variables) => {
      setIngestResults((prev) => ({ ...prev, [variables.id]: data }));
      qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8">
      <PageHeader
        title="Connectors"
        subtitle="Mock connectors for infrastructure target systems"
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(data ?? []).map((connector) => {
          const testResult = testResults[connector.id];
          const isTesting = testing[connector.id];
          const ingestResult = ingestResults[connector.id];
          const ingestActionId = INGEST_ACTIONS[connector.connector_type];
          const isIngesting = ingestMutation.isPending && ingestMutation.variables?.id === connector.id;

          return (
            <div key={connector.id} className="bg-white border border-slate-200 rounded-lg p-5">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{CONNECTOR_ICONS[connector.connector_type]}</span>
                  <div>
                    <div className="text-sm font-semibold text-slate-900">{connector.name}</div>
                    <div className="text-xs text-slate-400">{CONNECTOR_LABELS[connector.connector_type]}</div>
                  </div>
                </div>
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                  connector.status === "active"
                    ? "bg-emerald-50 text-emerald-700"
                    : connector.status === "error"
                    ? "bg-red-50 text-red-700"
                    : "bg-slate-100 text-slate-500"
                }`}>
                  {connector.status}
                </span>
              </div>

              <div className="mb-3">
                <div className="text-xs text-slate-400 mb-1.5">Scoped Permissions</div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(connector.scoped_permissions).map(([scope, actions]) => (
                    <span key={scope} className="px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-xs font-mono">
                      {scope}: {(actions as string[]).join(", ")}
                    </span>
                  ))}
                </div>
              </div>

              {testResult && (
                <div className={`mb-3 p-2.5 rounded border text-xs ${
                  testResult.success
                    ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                    : "bg-red-50 border-red-200 text-red-700"
                }`}>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    {testResult.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                    <span className="font-medium">{testResult.message}</span>
                  </div>
                  <div className="text-slate-500">Latency: {testResult.latency_ms}ms</div>
                </div>
              )}

              {ingestResult && (
                <div className="mb-3 p-2.5 rounded border text-xs bg-brand-50 border-brand-200 text-brand-700">
                  <span className="font-medium">Discovery complete — </span>
                  {ingestResult.created} created, {ingestResult.updated} updated
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={() => testConnector(connector.id)}
                  disabled={isTesting}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-200 text-slate-700 text-sm rounded-md hover:bg-slate-50 disabled:opacity-50"
                >
                  {isTesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
                  {isTesting ? "Testing..." : "Test Connector"}
                </button>

                {ingestActionId && (
                  <button
                    onClick={() => ingestMutation.mutate({ id: connector.id, actionId: ingestActionId })}
                    disabled={isIngesting}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-brand-200 text-brand-700 text-sm rounded-md hover:bg-brand-50 disabled:opacity-50"
                  >
                    {isIngesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                    {isIngesting ? "Discovering..." : "Run Discovery"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Connectors.tsx frontend/src/api/endpoints.ts frontend/src/types/api.ts
git commit -m "feat: add Run Discovery button and ingest API client"
```

---

## Task 4: `active_directory_mock` connector

**Files:**
- Create: `backend/app/connectors/catalog/active_directory_mock.json`
- Create: `backend/app/connectors/executors/active_directory_mock/__init__.py`
- Create: `backend/app/connectors/executors/active_directory_mock/discover_computers.py`
- Create: `backend/app/connectors/executors/active_directory_mock/discover_identities.py`
- Create: `backend/app/connectors/executors/active_directory_mock/disable_account.py`
- Create: `backend/app/connectors/executors/active_directory_mock/enable_account.py`
- Create: `backend/app/connectors/executors/active_directory_mock/reset_password.py`
- Create: `backend/app/connectors/executors/active_directory_mock/add_to_group.py`
- Create: `backend/app/connectors/executors/active_directory_mock/remove_from_group.py`
- Create: `backend/app/connectors/executors/active_directory_mock/enforce_mfa.py`

- [ ] **Step 1: Verify the existing `test_all_catalog_executors_resolve` will catch missing executors**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_catalog_service.py::test_all_catalog_executors_resolve -v"
```
Expected: PASS (no new catalog yet)

- [ ] **Step 2: Create `backend/app/connectors/catalog/active_directory_mock.json`**

```json
{
  "connector_type": "active_directory_mock",
  "display_name": "Active Directory (Mock)",
  "actions": [
    {
      "action_id": "discover_computers",
      "generic_action": "discover_computers",
      "action_type": "ingest",
      "display_name": "Discover AD Computers",
      "description": "Discovers all AD-joined computers with OU path, OS, and last logon metadata",
      "produces_asset_types": ["server"],
      "applicable_asset_types": [],
      "executor": "active_directory_mock.discover_computers",
      "estimated_duration_seconds": 20
    },
    {
      "action_id": "discover_identities",
      "generic_action": "discover_identities",
      "action_type": "ingest",
      "display_name": "Discover AD Identities",
      "description": "Discovers users, service accounts, and admin accounts with group memberships and enabled status",
      "produces_asset_types": ["identity"],
      "applicable_asset_types": [],
      "executor": "active_directory_mock.discover_identities",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "disable_account",
      "generic_action": "disable_account",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Disable Account",
      "description": "Disables a user or service account in Active Directory",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true}
      ],
      "executor": "active_directory_mock.disable_account",
      "rollback_action": "enable_account",
      "estimated_duration_seconds": 5,
      "blast_radius_hint": "account_access_loss",
      "safety_notes": ["Verify no critical services depend on this account before disabling"]
    },
    {
      "action_id": "enable_account",
      "generic_action": "enable_account",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Enable Account",
      "description": "Re-enables a disabled Active Directory account",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true}
      ],
      "executor": "active_directory_mock.enable_account",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "reset_password",
      "generic_action": "reset_password",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Reset Password",
      "description": "Forces a password reset on next login for the specified account",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true}
      ],
      "executor": "active_directory_mock.reset_password",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "add_to_group",
      "generic_action": "add_to_group",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Add to Group",
      "description": "Adds an account to a security group",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true},
        {"name": "group_name", "type": "string", "required": true}
      ],
      "executor": "active_directory_mock.add_to_group",
      "rollback_action": "remove_from_group",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "remove_from_group",
      "generic_action": "remove_from_group",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remove from Group",
      "description": "Removes an account from a security group",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true},
        {"name": "group_name", "type": "string", "required": true}
      ],
      "executor": "active_directory_mock.remove_from_group",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "enforce_mfa",
      "generic_action": "enforce_mfa",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Enforce MFA",
      "description": "Flags the account for mandatory MFA enrollment on next login",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true}
      ],
      "executor": "active_directory_mock.enforce_mfa",
      "estimated_duration_seconds": 5
    }
  ]
}
```

- [ ] **Step 3: Run the executor resolution test to confirm it fails**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_catalog_service.py::test_all_catalog_executors_resolve -v"
```
Expected: FAIL — `ImportError: Executor module ... not found`

- [ ] **Step 4: Create `backend/app/connectors/executors/active_directory_mock/__init__.py`** (empty file)

- [ ] **Step 5: Create all executor stubs**

`discover_computers.py`:
```python
from datetime import datetime, timezone
import random

_OUS = ["OU=Servers,DC=acme,DC=example", "OU=Workstations,DC=acme,DC=example", "OU=DMZ,DC=acme,DC=example"]
_HOSTS = ["dc-01", "dc-02", "web-01", "web-02", "app-01", "app-02", "db-01", "payments-api-01", "bastion-01"]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": host,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["ad-joined"],
            "asset_metadata": {
                "os": "Windows Server 2022",
                "ou": random.choice(_OUS),
                "last_logon": now,
                "ad_source": "active_directory_mock",
            },
        }
        for host in _HOSTS
    ]
```

`discover_identities.py`:
```python
from datetime import datetime, timezone
import random

_USERS = [
    ("jsmith", "user", ["Domain Users", "VPN-Access"]),
    ("aadmin", "admin", ["Domain Admins", "Enterprise Admins"]),
    ("svc-backup", "service_account", ["Backup Operators"]),
    ("svc-monitoring", "service_account", ["Monitoring"]),
    ("bjones", "user", ["Domain Users"]),
    ("cwhite", "user", ["Domain Users", "Finance-Read"]),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for username, account_type, groups in _USERS:
        tag = f"ad-{account_type.replace('_', '-')}"
        results.append({
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if account_type in ("admin", "service_account") else "medium",
            "tags": [tag, "ad-user"],
            "asset_metadata": {
                "account_type": account_type,
                "groups": groups,
                "enabled": True,
                "last_login": now,
                "ad_source": "active_directory_mock",
            },
        })
    return results
```

`disable_account.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "disable_account", "username": parameters.get("username"), "disabled": True, "disabled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "enable_account", "username": parameters.get("username")}
```

`enable_account.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "enable_account", "username": parameters.get("username"), "enabled": True, "enabled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enable_account has no rollback"}
```

`reset_password.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "reset_password", "username": parameters.get("username"), "must_change_on_next_login": True, "reset_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "password reset has no rollback"}
```

`add_to_group.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "add_to_group", "username": parameters.get("username"), "group_name": parameters.get("group_name"), "added": True, "added_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "remove_from_group", "username": parameters.get("username"), "group_name": parameters.get("group_name")}
```

`remove_from_group.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "remove_from_group", "username": parameters.get("username"), "group_name": parameters.get("group_name"), "removed": True, "removed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove_from_group has no automatic rollback"}
```

`enforce_mfa.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "enforce_mfa", "username": parameters.get("username"), "mfa_required": True, "enforced_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enforce_mfa has no rollback"}
```

- [ ] **Step 6: Run executor resolution test**

```
docker compose exec backend bash -c "cd /app && python -m pytest app/tests/test_catalog_service.py::test_all_catalog_executors_resolve -v"
```
Expected: PASS

- [ ] **Step 7: Update `test_load_indexes_all_connectors` in `backend/app/tests/test_catalog_service.py`**

```python
def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock",
        "active_directory_mock",
    }
```

- [ ] **Step 8: Run full test suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/catalog/active_directory_mock.json \
        backend/app/connectors/executors/active_directory_mock/ \
        backend/app/tests/test_catalog_service.py
git commit -m "feat: add active_directory_mock connector with ingest and change actions"
```

---

## Task 5: `crowdstrike_mock` connector

**Files:**
- Create: `backend/app/connectors/catalog/crowdstrike_mock.json`
- Create: `backend/app/connectors/executors/crowdstrike_mock/__init__.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/discover_endpoints.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/discover_endpoint_users.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/discover_applications.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/deploy_sensor.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/remove_sensor.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/isolate_host.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/restore_host.py`
- Create: `backend/app/connectors/executors/crowdstrike_mock/contain_process.py`

- [ ] **Step 1: Create `backend/app/connectors/catalog/crowdstrike_mock.json`**

```json
{
  "connector_type": "crowdstrike_mock",
  "display_name": "CrowdStrike Falcon (Mock)",
  "actions": [
    {
      "action_id": "discover_endpoints",
      "generic_action": "discover_endpoints",
      "action_type": "ingest",
      "display_name": "Discover Endpoints",
      "description": "Discovers all managed and unprotected endpoints with OS, sensor version, open ports, and running processes",
      "produces_asset_types": ["server"],
      "applicable_asset_types": [],
      "executor": "crowdstrike_mock.discover_endpoints",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "discover_endpoint_users",
      "generic_action": "discover_endpoint_users",
      "action_type": "ingest",
      "display_name": "Discover Endpoint Users",
      "description": "Discovers the last logged-in user per endpoint",
      "produces_asset_types": ["identity"],
      "applicable_asset_types": [],
      "executor": "crowdstrike_mock.discover_endpoint_users",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "discover_applications",
      "generic_action": "discover_applications",
      "action_type": "ingest",
      "display_name": "Discover Applications",
      "description": "Discovers installed software across all managed endpoints",
      "produces_asset_types": ["application"],
      "applicable_asset_types": [],
      "executor": "crowdstrike_mock.discover_applications",
      "estimated_duration_seconds": 45
    },
    {
      "action_id": "deploy_sensor",
      "generic_action": "deploy_sensor",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Deploy Falcon Sensor",
      "description": "Installs the CrowdStrike Falcon sensor on the target host via SSH",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "sensor_version", "type": "string", "required": false, "default": "7.14.0"}
      ],
      "executor": "crowdstrike_mock.deploy_sensor",
      "rollback_action": "remove_sensor",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "host_service_interruption"
    },
    {
      "action_id": "remove_sensor",
      "generic_action": "remove_sensor",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Remove Falcon Sensor",
      "description": "Uninstalls the CrowdStrike Falcon sensor from the target host",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "crowdstrike_mock.remove_sensor",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "isolate_host",
      "generic_action": "isolate_host",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Isolate Host",
      "description": "Network-isolates an endpoint via the Falcon API — blocks all traffic except CrowdStrike management",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "crowdstrike_mock.isolate_host",
      "rollback_action": "restore_host",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "host_network_isolation",
      "safety_notes": ["Host will lose all network connectivity except CrowdStrike management channel"]
    },
    {
      "action_id": "restore_host",
      "generic_action": "restore_host",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Restore Host Network",
      "description": "Lifts network isolation from a previously isolated host",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "crowdstrike_mock.restore_host",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "contain_process",
      "generic_action": "contain_process",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Contain Process",
      "description": "Kills a named process on the target endpoint via Falcon RTR",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "process_name", "type": "string", "required": true}
      ],
      "executor": "crowdstrike_mock.contain_process",
      "estimated_duration_seconds": 5,
      "safety_notes": ["Verify process name is correct — killing the wrong process may destabilize the host"]
    }
  ]
}
```

- [ ] **Step 2: Create `backend/app/connectors/executors/crowdstrike_mock/__init__.py`** (empty)

- [ ] **Step 3: Create executor stubs**

`discover_endpoints.py`:
```python
from datetime import datetime, timezone
import random

_HOSTS = [
    ("payments-api-01", True, ["payments", "prod"]),
    ("payments-api-02", True, ["payments", "prod"]),
    ("web-frontend-01", True, ["web", "prod"]),
    ("db-primary-01", True, ["database", "prod"]),
    ("legacy-app-01", False, ["legacy", "prod"]),
    ("bastion-01", True, ["infra", "prod"]),
    ("dev-workstation-05", False, ["dev"]),
    ("ci-runner-01", False, ["infra", "dev"]),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for hostname, protected, tags in _HOSTS:
        status_tag = "crowdstrike-managed" if protected else "crowdstrike-unprotected"
        results.append({
            "name": hostname,
            "asset_type": "server",
            "environment": "dev" if "dev" in tags else "prod",
            "criticality": "critical" if "database" in tags else "high",
            "tags": [status_tag] + tags,
            "asset_metadata": {
                "os": "Ubuntu 22.04",
                "sensor_version": "7.14.0" if protected else None,
                "open_ports": [22, 443, 8080],
                "running_processes": ["nginx", "node", "postgres"] if "database" in tags else ["nginx", "node"],
                "last_seen": now,
                "crowdstrike_source": "crowdstrike_mock",
            },
        })
    return results
```

`discover_endpoint_users.py`:
```python
from datetime import datetime, timezone

_HOST_USERS = [
    ("payments-api-01", "svc-payments", "service_account"),
    ("payments-api-02", "svc-payments", "service_account"),
    ("web-frontend-01", "jsmith", "user"),
    ("db-primary-01", "svc-database", "service_account"),
    ("bastion-01", "aadmin", "admin"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if account_type in ("admin", "service_account") else "medium",
            "tags": ["crowdstrike-detected-user", f"ad-{account_type.replace('_', '-')}"],
            "asset_metadata": {
                "account_type": account_type,
                "last_seen_on_host": hostname,
                "last_seen": now,
                "crowdstrike_source": "crowdstrike_mock",
            },
        }
        for hostname, username, account_type in _HOST_USERS
    ]
```

`discover_applications.py`:
```python
from datetime import datetime, timezone

_APPS = [
    ("nginx", "1.24.0", "F5 Networks"),
    ("openssl", "1.1.1t", "OpenSSL Foundation"),
    ("log4j", "2.14.1", "Apache Software Foundation"),
    ("node", "18.17.0", "Node.js Foundation"),
    ("python3", "3.10.12", "Python Software Foundation"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, version, vendor in _APPS:
        tags = ["crowdstrike-detected"]
        # log4j 2.14.1 is vulnerable to Log4Shell
        if name == "log4j" and version == "2.14.1":
            tags.append("vuln-severity:critical")
            tags.append("vuln:CVE-2021-44228")
        results.append({
            "name": name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "version": version,
                "vendor": vendor,
                "discovered_at": now,
                "crowdstrike_source": "crowdstrike_mock",
            },
        })
    return results
```

`deploy_sensor.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "deploy_sensor", "sensor_version": parameters.get("sensor_version", "7.14.0"), "assets": asset_ids, "status": "installed", "deployed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "remove_sensor", "assets": execution_result.get("assets", [])}
```

`remove_sensor.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "remove_sensor", "assets": asset_ids, "status": "removed", "removed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove_sensor has no rollback"}
```

`isolate_host.py`:
```python
from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    isolation_id = "iso-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {"action": "isolate_host", "isolation_id": isolation_id, "assets": asset_ids, "isolated": True, "isolated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_host", "isolation_id": execution_result.get("isolation_id")}
```

`restore_host.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_host", "assets": asset_ids, "isolated": False, "restored_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_host has no rollback"}
```

`contain_process.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "contain_process", "process_name": parameters.get("process_name"), "assets": asset_ids, "killed": True, "contained_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "process termination has no rollback"}
```

- [ ] **Step 4: Update `test_load_indexes_all_connectors` in `backend/app/tests/test_catalog_service.py`**

```python
def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock",
        "active_directory_mock", "crowdstrike_mock",
    }
```

- [ ] **Step 5: Run full suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/crowdstrike_mock.json \
        backend/app/connectors/executors/crowdstrike_mock/ \
        backend/app/tests/test_catalog_service.py
git commit -m "feat: add crowdstrike_mock connector with endpoint discovery and response actions"
```

---

## Task 6: `tenable_mock` connector

**Files:**
- Create: `backend/app/connectors/catalog/tenable_mock.json`
- Create: `backend/app/connectors/executors/tenable_mock/__init__.py`
- Create: `backend/app/connectors/executors/tenable_mock/discover_assets.py`
- Create: `backend/app/connectors/executors/tenable_mock/discover_vulnerabilities.py`
- Create: `backend/app/connectors/executors/tenable_mock/discover_local_accounts.py`
- Create: `backend/app/connectors/executors/tenable_mock/trigger_scan.py`
- Create: `backend/app/connectors/executors/tenable_mock/verify_remediation.py`

- [ ] **Step 1: Create `backend/app/connectors/catalog/tenable_mock.json`**

```json
{
  "connector_type": "tenable_mock",
  "display_name": "Tenable Vulnerability Management (Mock)",
  "actions": [
    {
      "action_id": "discover_assets",
      "generic_action": "discover_assets",
      "action_type": "ingest",
      "display_name": "Discover Scanned Assets",
      "description": "Discovers all hosts scanned by Tenable with IP, OS, and open ports",
      "produces_asset_types": ["server"],
      "applicable_asset_types": [],
      "executor": "tenable_mock.discover_assets",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "discover_vulnerabilities",
      "generic_action": "discover_vulnerabilities",
      "action_type": "ingest",
      "display_name": "Ingest Vulnerability Findings",
      "description": "Enriches existing assets with CVE tags and severity ratings; creates application assets for vulnerable software",
      "produces_asset_types": ["server", "application"],
      "applicable_asset_types": [],
      "executor": "tenable_mock.discover_vulnerabilities",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "discover_local_accounts",
      "generic_action": "discover_local_accounts",
      "action_type": "ingest",
      "display_name": "Discover Local Accounts",
      "description": "Discovers local OS accounts found on scanned hosts",
      "produces_asset_types": ["identity"],
      "applicable_asset_types": [],
      "executor": "tenable_mock.discover_local_accounts",
      "estimated_duration_seconds": 20
    },
    {
      "action_id": "trigger_scan",
      "generic_action": "trigger_scan",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Trigger Vulnerability Scan",
      "description": "Launches a Tenable scan against target assets",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "scan_policy", "type": "string", "required": false, "default": "basic_network_scan"}
      ],
      "executor": "tenable_mock.trigger_scan",
      "estimated_duration_seconds": 300
    },
    {
      "action_id": "verify_remediation",
      "generic_action": "verify_remediation",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Verify Remediation",
      "description": "Re-scans a specific asset to confirm a vulnerability finding has been resolved",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "cve_id", "type": "string", "required": false}
      ],
      "executor": "tenable_mock.verify_remediation",
      "estimated_duration_seconds": 120
    }
  ]
}
```

- [ ] **Step 2: Create `backend/app/connectors/executors/tenable_mock/__init__.py`** (empty)

- [ ] **Step 3: Create executor stubs**

`discover_assets.py`:
```python
from datetime import datetime, timezone

_HOSTS = [
    ("payments-api-01", "10.0.1.10", [22, 443, 8080]),
    ("payments-api-02", "10.0.1.11", [22, 443, 8080]),
    ("web-frontend-01", "10.0.2.10", [22, 80, 443]),
    ("db-primary-01", "10.0.3.10", [22, 5432]),
    ("legacy-app-01", "10.0.4.10", [22, 80, 8443, 21]),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": hostname,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical" if "db" in hostname else "high",
            "tags": ["tenable-scanned"],
            "asset_metadata": {
                "ip_address": ip,
                "open_ports": ports,
                "os": "Ubuntu 22.04",
                "last_scanned": now,
                "tenable_source": "tenable_mock",
            },
        }
        for hostname, ip, ports in _HOSTS
    ]
```

`discover_vulnerabilities.py`:
```python
from datetime import datetime, timezone

_VULNS = [
    ("legacy-app-01", "application", "vsftpd", "3.0.3", "vuln:CVE-2021-44228", "vuln-severity:critical"),
    ("legacy-app-01", "server", "legacy-app-01", None, "vuln:CVE-2023-44487", "vuln-severity:high"),
    ("payments-api-01", "server", "payments-api-01", None, "vuln:CVE-2023-20198", "vuln-severity:critical"),
    ("web-frontend-01", "application", "openssl", "1.1.1t", "vuln:CVE-2022-0778", "vuln-severity:high"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for host, asset_type, name, version, cve_tag, severity_tag in _VULNS:
        payload = {
            "name": name,
            "asset_type": asset_type,
            "environment": "prod",
            "criticality": "critical" if "critical" in severity_tag else "high",
            "tags": [cve_tag, severity_tag, "tenable-scanned"],
            "asset_metadata": {
                "vulnerability_source": host,
                "last_scanned": now,
                "tenable_source": "tenable_mock",
            },
        }
        if version:
            payload["asset_metadata"]["version"] = version
        results.append(payload)
    return results
```

`discover_local_accounts.py`:
```python
from datetime import datetime, timezone

_ACCOUNTS = [
    ("root", "legacy-app-01"),
    ("ubuntu", "payments-api-01"),
    ("deploy", "web-frontend-01"),
    ("postgres", "db-primary-01"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if username == "root" else "medium",
            "tags": ["tenable-discovered-account"],
            "asset_metadata": {
                "account_type": "local_account",
                "found_on_host": host,
                "discovered_at": now,
                "tenable_source": "tenable_mock",
            },
        }
        for username, host in _ACCOUNTS
    ]
```

`trigger_scan.py`:
```python
from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    scan_id = "scan-" + "".join(random.choices(string.digits, k=8))
    return {"action": "trigger_scan", "scan_id": scan_id, "scan_policy": parameters.get("scan_policy", "basic_network_scan"), "assets": asset_ids, "status": "launched", "launched_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan trigger has no rollback"}
```

`verify_remediation.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "verify_remediation", "cve_id": parameters.get("cve_id"), "assets": asset_ids, "remediated": True, "verified_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remediation verification has no rollback"}
```

- [ ] **Step 4: Update `test_load_indexes_all_connectors`**

```python
def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock",
        "active_directory_mock", "crowdstrike_mock", "tenable_mock",
    }
```

- [ ] **Step 5: Run full suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/tenable_mock.json \
        backend/app/connectors/executors/tenable_mock/ \
        backend/app/tests/test_catalog_service.py
git commit -m "feat: add tenable_mock connector with vulnerability discovery and scan actions"
```

---

## Task 7: `azure_mock` connector

**Files:**
- Create: `backend/app/connectors/catalog/azure_mock.json`
- Create: `backend/app/connectors/executors/azure_mock/__init__.py`
- Create: `backend/app/connectors/executors/azure_mock/discover_vms.py`
- Create: `backend/app/connectors/executors/azure_mock/discover_storage_accounts.py`
- Create: `backend/app/connectors/executors/azure_mock/discover_nsgs.py`
- Create: `backend/app/connectors/executors/azure_mock/update_nsg_rule.py`
- Create: `backend/app/connectors/executors/azure_mock/restore_nsg_rule.py`
- Create: `backend/app/connectors/executors/azure_mock/disable_public_blob_access.py`
- Create: `backend/app/connectors/executors/azure_mock/enable_public_blob_access.py`
- Create: `backend/app/connectors/executors/azure_mock/rotate_storage_key.py`

- [ ] **Step 1: Create `backend/app/connectors/catalog/azure_mock.json`**

```json
{
  "connector_type": "azure_mock",
  "display_name": "Microsoft Azure (Mock)",
  "actions": [
    {
      "action_id": "discover_vms",
      "generic_action": "discover_vms",
      "action_type": "ingest",
      "display_name": "Discover Azure VMs",
      "description": "Discovers all VMs with size, region, OS, and public IP metadata",
      "produces_asset_types": ["server"],
      "applicable_asset_types": [],
      "executor": "azure_mock.discover_vms",
      "estimated_duration_seconds": 20
    },
    {
      "action_id": "discover_storage_accounts",
      "generic_action": "discover_storage_accounts",
      "action_type": "ingest",
      "display_name": "Discover Storage Accounts",
      "description": "Discovers storage accounts and flags those with public blob access enabled",
      "produces_asset_types": ["cloud_account"],
      "applicable_asset_types": [],
      "executor": "azure_mock.discover_storage_accounts",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "discover_nsgs",
      "generic_action": "discover_nsgs",
      "action_type": "ingest",
      "display_name": "Discover Network Security Groups",
      "description": "Discovers NSGs and flags those with overly permissive rules",
      "produces_asset_types": ["firewall"],
      "applicable_asset_types": [],
      "executor": "azure_mock.discover_nsgs",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "update_nsg_rule",
      "generic_action": "update_nsg_rule",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Update NSG Rule",
      "description": "Adds, modifies, or removes a rule on an Azure Network Security Group",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "rule_name", "type": "string", "required": true},
        {"name": "action", "type": "string", "required": true},
        {"name": "priority", "type": "integer", "required": false, "default": 100},
        {"name": "source", "type": "string", "required": false, "default": "*"},
        {"name": "destination_port", "type": "string", "required": false, "default": "*"}
      ],
      "executor": "azure_mock.update_nsg_rule",
      "rollback_action": "restore_nsg_rule",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "network_access_change",
      "safety_notes": ["Verify rule does not block critical management traffic"]
    },
    {
      "action_id": "restore_nsg_rule",
      "generic_action": "restore_nsg_rule",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Restore NSG Rule",
      "description": "Restores an NSG rule to its previous state",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "rule_name", "type": "string", "required": true},
        {"name": "previous_state", "type": "object", "required": true}
      ],
      "executor": "azure_mock.restore_nsg_rule",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "disable_public_blob_access",
      "generic_action": "disable_public_blob_access",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Disable Public Blob Access",
      "description": "Sets public blob access to disabled on an Azure storage account",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [],
      "executor": "azure_mock.disable_public_blob_access",
      "rollback_action": "enable_public_blob_access",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "public_access_loss"
    },
    {
      "action_id": "enable_public_blob_access",
      "generic_action": "enable_public_blob_access",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Enable Public Blob Access",
      "description": "Re-enables public blob access on a storage account (rollback only)",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [],
      "executor": "azure_mock.enable_public_blob_access",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "rotate_storage_key",
      "generic_action": "rotate_storage_key",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Rotate Storage Account Key",
      "description": "Regenerates a storage account access key",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "key_name", "type": "string", "required": false, "default": "key1"}
      ],
      "executor": "azure_mock.rotate_storage_key",
      "estimated_duration_seconds": 15,
      "safety_notes": ["Rotate consumers to new key before revoking old key"]
    }
  ]
}
```

- [ ] **Step 2: Create `backend/app/connectors/executors/azure_mock/__init__.py`** (empty)

- [ ] **Step 3: Create executor stubs**

`discover_vms.py`:
```python
from datetime import datetime, timezone

_VMS = [
    ("azure-web-01", "eastus", "Standard_D2s_v3", "10.1.1.10"),
    ("azure-web-02", "eastus", "Standard_D2s_v3", "10.1.1.11"),
    ("azure-db-01", "eastus", "Standard_E4s_v3", None),
    ("azure-bastion-01", "eastus", "Standard_B2s", "20.50.100.42"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical" if "db" in name else "high",
            "tags": ["azure"],
            "asset_metadata": {
                "region": region,
                "vm_size": size,
                "public_ip": public_ip,
                "os": "Ubuntu 22.04 LTS",
                "subscription": "prod-subscription-001",
                "discovered_at": now,
                "azure_source": "azure_mock",
            },
        }
        for name, region, size, public_ip in _VMS
    ]
```

`discover_storage_accounts.py`:
```python
from datetime import datetime, timezone

_STORAGE = [
    ("acmeprodbackups", False, "GRS"),
    ("acmepublicassets", True, "LRS"),
    ("acmelogarchive", False, "GRS"),
    ("acmeuploads", True, "LRS"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, public_access, replication in _STORAGE:
        tags = ["azure-storage"]
        if public_access:
            tags.append("azure-public-storage")
        results.append({
            "name": name,
            "asset_type": "cloud_account",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "public_blob_access": public_access,
                "replication_type": replication,
                "encryption_enabled": True,
                "region": "eastus",
                "discovered_at": now,
                "azure_source": "azure_mock",
            },
        })
    return results
```

`discover_nsgs.py`:
```python
from datetime import datetime, timezone

_NSGS = [
    ("payments-subnet-nsg", ["payments"], 12, False),
    ("web-tier-nsg", ["web", "dmz"], 8, True),
    ("mgmt-nsg", ["infra"], 5, False),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, subnets, rule_count, permissive in _NSGS:
        tags = ["azure-nsg"]
        if permissive:
            tags.append("azure-nsg-permissive")
        results.append({
            "name": name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "associated_subnets": subnets,
                "rule_count": rule_count,
                "has_any_source_rules": permissive,
                "region": "eastus",
                "discovered_at": now,
                "azure_source": "azure_mock",
            },
        })
    return results
```

`update_nsg_rule.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "update_nsg_rule", "rule_name": parameters.get("rule_name"), "nsg_action": parameters.get("action"), "priority": parameters.get("priority", 100), "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_nsg_rule", "rule_name": parameters.get("rule_name")}
```

`restore_nsg_rule.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_nsg_rule", "rule_name": parameters.get("rule_name"), "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_nsg_rule has no rollback"}
```

`disable_public_blob_access.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "disable_public_blob_access", "assets": asset_ids, "public_access": False, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "enable_public_blob_access", "assets": execution_result.get("assets", [])}
```

`enable_public_blob_access.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "enable_public_blob_access", "assets": asset_ids, "public_access": True, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enable_public_blob_access has no rollback"}
```

`rotate_storage_key.py`:
```python
from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    new_key = "".join(random.choices(string.ascii_letters + string.digits, k=64))
    return {"action": "rotate_storage_key", "key_name": parameters.get("key_name", "key1"), "assets": asset_ids, "new_key_fingerprint": new_key[:8] + "...", "rotated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key rotation has no rollback — update consumers to new key"}
```

- [ ] **Step 4: Update `test_load_indexes_all_connectors`**

```python
def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock",
        "active_directory_mock", "crowdstrike_mock", "tenable_mock", "azure_mock",
    }
```

- [ ] **Step 5: Run full suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/azure_mock.json \
        backend/app/connectors/executors/azure_mock/ \
        backend/app/tests/test_catalog_service.py
git commit -m "feat: add azure_mock connector with VM/storage/NSG discovery and remediation actions"
```

---

## Task 8: Expand `paloalto_mock` — ingest traffic logs + chokepoint actions

**Files:**
- Modify: `backend/app/connectors/catalog/paloalto_mock.json`
- Create: `backend/app/connectors/executors/paloalto_mock/ingest_traffic_logs.py`
- Create: `backend/app/connectors/executors/paloalto_mock/ingest_security_events.py`
- Create: `backend/app/connectors/executors/paloalto_mock/enable_traffic_logging.py`
- Create: `backend/app/connectors/executors/paloalto_mock/disable_traffic_logging.py`
- Create: `backend/app/connectors/executors/paloalto_mock/update_chokepoint_rule.py`
- Create: `backend/app/connectors/executors/paloalto_mock/restore_chokepoint_rule.py`

- [ ] **Step 1: Add new actions to `backend/app/connectors/catalog/paloalto_mock.json`**

Append these actions to the existing `"actions"` array (after `validate_staged`):

```json
    ,{
      "action_id": "ingest_traffic_logs",
      "generic_action": "ingest_traffic_logs",
      "action_type": "ingest",
      "display_name": "Ingest Traffic Logs",
      "description": "Ingests observed traffic flows as application assets; tags high-volume flows and enriches existing server and firewall assets with last-seen-traffic metadata",
      "produces_asset_types": ["application", "server", "firewall"],
      "applicable_asset_types": [],
      "executor": "paloalto_mock.ingest_traffic_logs",
      "estimated_duration_seconds": 45
    },
    {
      "action_id": "ingest_security_events",
      "generic_action": "ingest_security_events",
      "action_type": "ingest",
      "display_name": "Ingest Security Events",
      "description": "Tags existing assets with palo-threat-detected when the firewall logged a threat against them",
      "produces_asset_types": ["server"],
      "applicable_asset_types": [],
      "executor": "paloalto_mock.ingest_security_events",
      "estimated_duration_seconds": 20
    },
    {
      "action_id": "enable_traffic_logging",
      "generic_action": "enable_traffic_logging",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Enable Traffic Logging",
      "description": "Enables enhanced traffic logging for a zone or policy rule",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "zone_name", "type": "string", "required": false},
        {"name": "rule_name", "type": "string", "required": false}
      ],
      "executor": "paloalto_mock.enable_traffic_logging",
      "rollback_action": "disable_traffic_logging",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "disable_traffic_logging",
      "generic_action": "disable_traffic_logging",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Disable Traffic Logging",
      "description": "Disables enhanced traffic logging (rollback for enable_traffic_logging)",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "zone_name", "type": "string", "required": false},
        {"name": "rule_name", "type": "string", "required": false}
      ],
      "executor": "paloalto_mock.disable_traffic_logging",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "update_chokepoint_rule",
      "generic_action": "update_chokepoint_rule",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Update Chokepoint Rule",
      "description": "Modifies a policy rule at an identified traffic chokepoint",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "rule_name", "type": "string", "required": true},
        {"name": "new_action", "type": "string", "required": true},
        {"name": "destination_zones", "type": "array", "required": false}
      ],
      "executor": "paloalto_mock.update_chokepoint_rule",
      "rollback_action": "restore_chokepoint_rule",
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "network_access_change",
      "safety_notes": ["Verify rule change does not block critical flows before applying"]
    },
    {
      "action_id": "restore_chokepoint_rule",
      "generic_action": "restore_chokepoint_rule",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Restore Chokepoint Rule",
      "description": "Restores a chokepoint rule to its previous state",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "rule_name", "type": "string", "required": true},
        {"name": "previous_state", "type": "object", "required": true}
      ],
      "executor": "paloalto_mock.restore_chokepoint_rule",
      "estimated_duration_seconds": 10
    }
```

- [ ] **Step 2: Create new executor stubs**

`ingest_traffic_logs.py`:
```python
from datetime import datetime, timezone

_FLOWS = [
    ("https-payments", "payments-api-01", "10.0.1.10", 443, 95000000, True),
    ("postgres-db", "db-primary-01", "10.0.3.10", 5432, 42000000, False),
    ("ssh-mgmt", "bastion-01", "10.0.5.10", 22, 1200000, False),
    ("http-web", "web-frontend-01", "10.0.2.10", 80, 200000000, True),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for service_name, host, dest_ip, port, bytes_total, high_volume in _FLOWS:
        tags = ["palo-observed"]
        if high_volume:
            tags.append("high-volume")
        results.append({
            "name": service_name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "source_host": host,
                "destination_ip": dest_ip,
                "port": port,
                "protocol": "TCP",
                "bytes_total": bytes_total,
                "last_seen": now,
                "paloalto_source": "paloalto_mock",
            },
        })
    return results
```

`ingest_security_events.py`:
```python
from datetime import datetime, timezone

_THREATS = [
    ("legacy-app-01", "exploit", "CVE-2023-44487"),
    ("payments-api-01", "spyware", "trojan-generic"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": host,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical",
            "tags": ["palo-threat-detected"],
            "asset_metadata": {
                "threat_category": threat_type,
                "threat_id": threat_id,
                "last_threat_seen": now,
                "paloalto_source": "paloalto_mock",
            },
        }
        for host, threat_type, threat_id in _THREATS
    ]
```

`enable_traffic_logging.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "enable_traffic_logging", "zone_name": parameters.get("zone_name"), "rule_name": parameters.get("rule_name"), "logging_enabled": True, "enabled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "disable_traffic_logging"}
```

`disable_traffic_logging.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "disable_traffic_logging", "zone_name": parameters.get("zone_name"), "rule_name": parameters.get("rule_name"), "logging_enabled": False, "disabled_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable_traffic_logging has no rollback"}
```

`update_chokepoint_rule.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "update_chokepoint_rule", "rule_name": parameters.get("rule_name"), "new_action": parameters.get("new_action"), "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_chokepoint_rule", "rule_name": parameters.get("rule_name")}
```

`restore_chokepoint_rule.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_chokepoint_rule", "rule_name": parameters.get("rule_name"), "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_chokepoint_rule has no rollback"}
```

- [ ] **Step 3: Run full suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/catalog/paloalto_mock.json \
        backend/app/connectors/executors/paloalto_mock/
git commit -m "feat: expand paloalto_mock with traffic log ingest and chokepoint change actions"
```

---

## Task 9: Expand `ssh_mock` — SELinux and containerize actions

**Files:**
- Modify: `backend/app/connectors/catalog/ssh_mock.json`
- Create: `backend/app/connectors/executors/ssh_mock/apply_selinux_policy.py`
- Create: `backend/app/connectors/executors/ssh_mock/revert_selinux_policy.py`
- Create: `backend/app/connectors/executors/ssh_mock/containerize_workload.py`
- Create: `backend/app/connectors/executors/ssh_mock/restore_bare_metal_service.py`

- [ ] **Step 1: Append new actions to `backend/app/connectors/catalog/ssh_mock.json`**

Add after `collect_output`:
```json
    ,{
      "action_id": "apply_selinux_policy",
      "generic_action": "apply_selinux_policy",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Apply SELinux Policy",
      "description": "Applies a named SELinux policy module to a target host to restrict process permissions",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "policy_name", "type": "string", "required": true},
        {"name": "policy_version", "type": "string", "required": false, "default": "latest"}
      ],
      "executor": "ssh_mock.apply_selinux_policy",
      "rollback_action": "revert_selinux_policy",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "host_service_interruption",
      "safety_notes": ["Verify policy does not block required process access before applying to production"]
    },
    {
      "action_id": "revert_selinux_policy",
      "generic_action": "revert_selinux_policy",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Revert SELinux Policy",
      "description": "Reverts SELinux policy to the previously active state",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "previous_policy_id", "type": "string", "required": true}
      ],
      "executor": "ssh_mock.revert_selinux_policy",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "containerize_workload",
      "generic_action": "containerize_workload",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Containerize Workload",
      "description": "Wraps a running process in a container namespace with cgroup and seccomp restrictions using an approved template",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "process_name", "type": "string", "required": true},
        {"name": "template_id", "type": "string", "required": true}
      ],
      "executor": "ssh_mock.containerize_workload",
      "rollback_action": "restore_bare_metal_service",
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "host_service_interruption",
      "safety_notes": ["Service will restart during containerization — schedule a maintenance window"]
    },
    {
      "action_id": "restore_bare_metal_service",
      "generic_action": "restore_bare_metal_service",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Restore Bare-Metal Service",
      "description": "Removes container wrapper and restores the service as a bare-metal process",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "process_name", "type": "string", "required": true}
      ],
      "executor": "ssh_mock.restore_bare_metal_service",
      "estimated_duration_seconds": 30
    }
```

- [ ] **Step 2: Create executor stubs**

`apply_selinux_policy.py`:
```python
from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    policy_id = "pol-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return {"action": "apply_selinux_policy", "policy_name": parameters.get("policy_name"), "policy_id": policy_id, "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "revert_selinux_policy", "previous_policy_id": execution_result.get("policy_id")}
```

`revert_selinux_policy.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "revert_selinux_policy", "previous_policy_id": parameters.get("previous_policy_id"), "assets": asset_ids, "reverted": True, "reverted_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revert_selinux_policy has no rollback"}
```

`containerize_workload.py`:
```python
from datetime import datetime, timezone
import random, string

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    container_id = "ctr-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return {"action": "containerize_workload", "process_name": parameters.get("process_name"), "template_id": parameters.get("template_id"), "container_id": container_id, "assets": asset_ids, "containerized": True, "containerized_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_bare_metal_service", "process_name": parameters.get("process_name")}
```

`restore_bare_metal_service.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "restore_bare_metal_service", "process_name": parameters.get("process_name"), "assets": asset_ids, "restored": True, "restored_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_bare_metal_service has no rollback"}
```

- [ ] **Step 3: Run full suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/catalog/ssh_mock.json \
        backend/app/connectors/executors/ssh_mock/apply_selinux_policy.py \
        backend/app/connectors/executors/ssh_mock/revert_selinux_policy.py \
        backend/app/connectors/executors/ssh_mock/containerize_workload.py \
        backend/app/connectors/executors/ssh_mock/restore_bare_metal_service.py
git commit -m "feat: expand ssh_mock with SELinux policy and workload containerization actions"
```

---

## Task 10: Seed example projects and new connectors

**Files:**
- Modify: `backend/seed.py`

This task adds the 10 example projects and the 3 new connectors (CrowdStrike, AD, Tenable) to the existing seed script. Because `seed.py` skips if the org already exists, we add a secondary check for projects and new connectors.

- [ ] **Step 1: Add UUIDs and seed logic to `backend/seed.py`**

After the existing `CONNECTOR_IDS` dict, add:

```python
NEW_CONNECTOR_IDS = {
    "active_directory": uuid.UUID("00000000-0000-0000-0002-000000000005"),
    "crowdstrike": uuid.UUID("00000000-0000-0000-0002-000000000006"),
    "tenable": uuid.UUID("00000000-0000-0000-0002-000000000007"),
    "azure": uuid.UUID("00000000-0000-0000-0002-000000000008"),
}

PROJECT_IDS = {
    "isolate_investigate": uuid.UUID("00000000-0000-0000-0004-000000000001"),
    "deploy_edr": uuid.UUID("00000000-0000-0000-0004-000000000002"),
    "remediate_cve": uuid.UUID("00000000-0000-0000-0004-000000000003"),
    "offboard_employee": uuid.UUID("00000000-0000-0000-0004-000000000004"),
    "azure_storage": uuid.UUID("00000000-0000-0000-0004-000000000005"),
    "tighten_firewall": uuid.UUID("00000000-0000-0000-0004-000000000006"),
    "mfa_enforcement": uuid.UUID("00000000-0000-0000-0004-000000000007"),
    "microsegmentation": uuid.UUID("00000000-0000-0000-0004-000000000008"),
    "workload_harden": uuid.UUID("00000000-0000-0000-0004-000000000009"),
    "firewall_chokepoints": uuid.UUID("00000000-0000-0000-0004-000000000010"),
}
```

After the existing `await db.commit()` at the end of `seed()`, add a call to a new function `seed_expansion(db)`. Also add this import at the top:

```python
from app.models.project import Project, ProjectStatus
```

Add the `seed_expansion` function before `if __name__ == "__main__":`:

```python
async def seed_expansion():
    async with AsyncSessionLocal() as db:
        # Skip if new connectors already seeded
        existing = await db.get(Connector, NEW_CONNECTOR_IDS["crowdstrike"])
        if existing:
            print("Expansion seed already exists — skipping.")
            return

        new_connectors = [
            Connector(id=NEW_CONNECTOR_IDS["active_directory"], organization_id=ORG_ID,
                      connector_type=ConnectorType.active_directory_mock,
                      name="Active Directory Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"ldap": ["read"], "accounts": ["disable", "enable", "reset"], "groups": ["read", "write"]}),
            Connector(id=NEW_CONNECTOR_IDS["crowdstrike"], organization_id=ORG_ID,
                      connector_type=ConnectorType.crowdstrike_mock,
                      name="CrowdStrike Falcon Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"hosts": ["read", "isolate", "restore"], "sensors": ["deploy", "remove"], "rtr": ["execute"]}),
            Connector(id=NEW_CONNECTOR_IDS["tenable"], organization_id=ORG_ID,
                      connector_type=ConnectorType.tenable_mock,
                      name="Tenable Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"scans": ["read", "launch"], "assets": ["read"], "vulnerabilities": ["read"]}),
            Connector(id=NEW_CONNECTOR_IDS["azure"], organization_id=ORG_ID,
                      connector_type=ConnectorType.azure_mock,
                      name="Azure Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"compute": ["read"], "storage": ["read", "write"], "network": ["read", "write"]}),
        ]
        db.add_all(new_connectors)

        projects = [
            Project(id=PROJECT_IDS["isolate_investigate"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Isolate and Investigate Compromised Endpoint",
                    description="Contain a suspected compromise on a production host.",
                    goal="Isolate the compromised endpoint via CrowdStrike, disable the associated user account in Active Directory, and snapshot the machine in AWS for forensic review."),
            Project(id=PROJECT_IDS["deploy_edr"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Deploy EDR to Unprotected Hosts",
                    description="Close sensor coverage gaps identified in the CrowdStrike inventory.",
                    goal="Discover all hosts missing CrowdStrike Falcon sensor coverage and deploy the sensor to every unprotected endpoint in the production environment."),
            Project(id=PROJECT_IDS["remediate_cve"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Remediate Critical CVE Across Fleet",
                    description="Patch a critical vulnerability identified in the latest Tenable scan.",
                    goal="Identify all assets affected by a critical CVE, apply the patch via SSH remote command, and verify remediation with a follow-up Tenable scan."),
            Project(id=PROJECT_IDS["offboard_employee"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Offboard Departed Employee",
                    description="Remove access for a departed employee across all identity systems.",
                    goal="Disable the user's Active Directory account, remove them from all security groups, and revoke their Okta API credentials."),
            Project(id=PROJECT_IDS["azure_storage"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Remediate Public Azure Storage",
                    description="Fix public blob access misconfiguration found in Azure posture scan.",
                    goal="Discover all Azure storage accounts with public blob access enabled and disable public access on all affected accounts."),
            Project(id=PROJECT_IDS["tighten_firewall"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Tighten Firewall After Vulnerability Scan",
                    description="Reduce attack surface based on Tenable open port findings.",
                    goal="Use Tenable scan findings to identify unnecessary open ports and tighten the corresponding AWS security group and Azure NSG rules."),
            Project(id=PROJECT_IDS["mfa_enforcement"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="MFA Enforcement for Non-Compliant Accounts",
                    description="Enforce MFA across accounts identified as non-compliant in the AD audit.",
                    goal="Discover all Active Directory identity assets without MFA enabled and enforce MFA enrollment across every non-compliant account."),
            Project(id=PROJECT_IDS["microsegmentation"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Microsegmentation for Payments Subnet",
                    description="Apply zero-trust segmentation between the payments app tier and database tier.",
                    goal="Analyze current traffic flows to the payments subnet, generate a microsegmentation policy diff, stage and apply the new PaloAlto policy, and update AWS security groups to match."),
            Project(id=PROJECT_IDS["workload_harden"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Harden Payments-API Workload Isolation",
                    description="Reduce the blast radius of payments-api by applying OS-level controls.",
                    goal="Snapshot the payments-api host, apply a SELinux policy to restrict process permissions, and containerize the application for workload isolation."),
            Project(id=PROJECT_IDS["firewall_chokepoints"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Identify and Respond to Firewall Chokepoints",
                    description="Use PaloAlto traffic data to find and remediate policy bottlenecks.",
                    goal="Enable enhanced traffic logging on the production firewall, ingest flow data to identify high-volume chokepoints, analyze flows, and update policy rules at identified bottlenecks."),
        ]
        db.add_all(projects)
        await db.commit()
        print("Expansion seed (new connectors + example projects) created successfully.")
```

Update the `if __name__ == "__main__":` block at the bottom:

```python
async def main():
    await seed()
    await seed_expansion()

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the seed script**

```
docker compose exec backend bash -c "cd /app && python seed.py"
```
Expected output includes: `Expansion seed (new connectors + example projects) created successfully.`

- [ ] **Step 3: Verify projects appear in the API**

```
docker compose exec backend bash -c "cd /app && python -c \"
import asyncio
from sqlalchemy import select, func
from app.database import AsyncSessionLocal
from app.models.project import Project

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.count()).select_from(Project))
        print('Projects:', result.scalar())

asyncio.run(check())
\""
```
Expected: `Projects: 10`

- [ ] **Step 4: Run full suite**

```
docker compose exec backend bash -c "cd /app && python -m pytest --tb=short -q"
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/seed.py
git commit -m "feat: seed example projects and new connector instances"
```
