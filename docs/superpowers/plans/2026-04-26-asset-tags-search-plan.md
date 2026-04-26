# Asset Tags & Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add free-form tags to assets, server-side search/filter on the asset list, bulk tag management, and an asset detail/edit page.

**Architecture:** Tags stored as a JSON array column on the `assets` table (cross-DB compatible). Backend gains four new/updated endpoints: filtered `GET /assets`, `GET /assets/tags`, `PATCH /assets/{id}`, and `PATCH /assets/bulk-tag`. The Assets page is rewritten with URL-driven search state (`useSearchParams`), filter dropdowns, checkboxes for bulk tagging, and a new `/assets/:id` detail/edit page.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 / Alembic / PostgreSQL 16 — React 18 / TypeScript / TanStack Query / React Router v6 / Tailwind CSS

**Note on tag filtering:** Tags are stored as JSON (`list[str]`). SQL handles name/env/type/criticality filters; tag filtering is applied as a Python post-filter on the already-scoped queryset. This is correct for MVP; the column can be migrated to `ARRAY(String)` with native PostgreSQL operators when scale requires it.

**Test commands:**
- Backend unit tests: `docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -v --tb=short"`
- Run migrations: `docker compose exec backend alembic upgrade head`
- Rebuild backend: `docker compose up --build -d backend`
- Frontend: hot-reloads at http://localhost:3000 when files in `frontend/src/` are saved

---

### Task 1: Alembic migration — add tags column

**Files:**
- Create: `backend/alembic/versions/002_add_asset_tags.py`

- [ ] **Step 1: Create the migration file**

```python
# backend/alembic/versions/002_add_asset_tags.py
"""add tags to assets

Revision ID: 002
Revises: 001
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("tags", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("assets", "tags")
```

- [ ] **Step 2: Run the migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected output ends with: `Running upgrade 001 -> 002, add tags to assets`

- [ ] **Step 3: Verify column exists**

```bash
docker compose exec db psql -U nexplane -d nexplane -c "\d assets"
```

Expected: `tags` column of type `json` appears in the output.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/002_add_asset_tags.py
git commit -m "feat: add tags column to assets table"
```

---

### Task 2: Asset model + schema updates

**Files:**
- Modify: `backend/app/models/asset.py`
- Modify: `backend/app/schemas/asset.py`
- Create: `backend/app/tests/test_asset_schemas.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_asset_schemas.py`:
```python
import pytest
from pydantic import ValidationError
from app.schemas.asset import AssetUpdate, BulkTagOperation


def test_asset_update_all_fields_optional():
    # Empty update is valid
    u = AssetUpdate()
    assert u.name is None
    assert u.tags is None


def test_asset_update_tags_accepts_list():
    u = AssetUpdate(tags=["pci-scope", "payments"])
    assert u.tags == ["pci-scope", "payments"]


def test_asset_update_tags_empty_list_clears_tags():
    u = AssetUpdate(tags=[])
    assert u.tags == []


def test_bulk_tag_operation_valid_add():
    import uuid
    op = BulkTagOperation(
        asset_ids=[uuid.uuid4(), uuid.uuid4()],
        operation="add",
        tags=["pci-scope"],
    )
    assert op.operation == "add"
    assert len(op.tags) == 1


def test_bulk_tag_operation_requires_at_least_one_tag():
    import uuid
    with pytest.raises(ValidationError, match="tags"):
        BulkTagOperation(
            asset_ids=[uuid.uuid4()],
            operation="add",
            tags=[],
        )


def test_bulk_tag_operation_invalid_operation():
    import uuid
    with pytest.raises(ValidationError):
        BulkTagOperation(
            asset_ids=[uuid.uuid4()],
            operation="replace_all",
            tags=["x"],
        )
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_asset_schemas.py -v" 2>&1 | tail -15
```

Expected: `ImportError` — `AssetUpdate` and `BulkTagOperation` don't exist yet.

- [ ] **Step 3: Update `backend/app/models/asset.py`**

Add the `tags` column (add `JSON` to the import line and add the column):

```python
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class AssetType(str, enum.Enum):
    server = "server"
    cloud_account = "cloud_account"
    dns_zone = "dns_zone"
    firewall = "firewall"
    identity_provider = "identity_provider"
    application = "application"


class Environment(str, enum.Enum):
    dev = "dev"
    staging = "staging"
    prod = "prod"


class Criticality(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(SAEnum(AssetType, name="asset_type"), nullable=False)
    environment: Mapped[Environment] = mapped_column(SAEnum(Environment, name="environment"), nullable=False)
    criticality: Mapped[Criticality] = mapped_column(SAEnum(Criticality, name="criticality"), nullable=False)
    asset_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship("Organization", back_populates="assets")
```

- [ ] **Step 4: Update `backend/app/schemas/asset.py`**

```python
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, field_validator
from app.models.asset import AssetType, Environment, Criticality


class AssetCreate(BaseModel):
    name: str
    asset_type: AssetType
    environment: Environment
    criticality: Criticality
    asset_metadata: dict = {}
    tags: list[str] = []


class AssetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    asset_type: AssetType
    environment: Environment
    criticality: Criticality
    asset_metadata: dict
    tags: list[str]
    created_at: datetime


class AssetUpdate(BaseModel):
    name: str | None = None
    criticality: Criticality | None = None
    asset_metadata: dict | None = None
    tags: list[str] | None = None  # None = no change; [] = clear all tags


class BulkTagOperation(BaseModel):
    asset_ids: list[uuid.UUID]
    operation: Literal["add", "remove", "set"]
    tags: list[str]

    @field_validator("tags")
    @classmethod
    def tags_not_empty(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("tags must contain at least one entry")
        return v
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_asset_schemas.py -v"
```

Expected: 6 tests PASSED.

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -v --tb=short" 2>&1 | tail -5
```

Expected: all tests pass (75 + 6 = 81 total).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/asset.py backend/app/schemas/asset.py backend/app/tests/test_asset_schemas.py
git commit -m "feat: add tags field to Asset model and schemas"
```

---

### Task 3: GET /assets — filter query params

**Files:**
- Modify: `backend/app/routers/assets.py`

Update `list_assets` to accept filter params. Tag filtering is Python post-filter; all other filters are SQL-level.

- [ ] **Step 1: Replace `list_assets` in `backend/app/routers/assets.py`**

```python
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Environment, AssetType, Criticality
from app.models.user import User
from app.routers import current_user
from app.schemas.asset import AssetCreate, AssetRead, AssetUpdate, BulkTagOperation
from app.services.audit_service import record_event

router = APIRouter(prefix="/assets", tags=["Assets"])


@router.get("", response_model=list[AssetRead])
async def list_assets(
    q: str | None = Query(None, description="Asset name substring search"),
    env: str | None = Query(None, description="Filter by environment"),
    asset_type: str | None = Query(None, description="Filter by asset type"),
    criticality: str | None = Query(None, description="Filter by criticality"),
    tag: str | None = Query(None, description="Filter by tag (asset must have this tag)"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Asset).where(Asset.organization_id == user.organization_id)

    if q:
        stmt = stmt.where(Asset.name.ilike(f"%{q}%"))
    if env:
        try:
            stmt = stmt.where(Asset.environment == Environment(env))
        except ValueError:
            pass  # invalid enum value — ignore filter
    if asset_type:
        try:
            stmt = stmt.where(Asset.asset_type == AssetType(asset_type))
        except ValueError:
            pass
    if criticality:
        try:
            stmt = stmt.where(Asset.criticality == Criticality(criticality))
        except ValueError:
            pass

    result = await db.execute(stmt.order_by(Asset.name))
    assets = result.scalars().all()

    # Tag filter is applied in Python (JSON array containment)
    if tag:
        assets = [a for a in assets if tag in (a.tags or [])]

    return assets
```

- [ ] **Step 2: Rebuild backend and smoke-test the filter**

```bash
docker compose up --build -d backend
```

Wait ~5 seconds, then:

```bash
curl -s -H "Authorization: Bearer $(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')" "http://localhost:8000/assets?env=prod" | python3 -c "import sys,json; data=json.load(sys.stdin); print(f'Got {len(data)} assets')"
```

Expected: `Got N assets` (only prod assets, no errors).

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/assets.py
git commit -m "feat: add search/filter params to GET /assets"
```

---

### Task 4: GET /assets/tags endpoint

**Files:**
- Modify: `backend/app/routers/assets.py`

This route **must be registered before** `GET /assets/{asset_id}` to avoid FastAPI treating `tags` as an asset UUID.

- [ ] **Step 1: Add `get_asset_tags` route to `assets.py`**

Add this function after `list_assets` and **before** the `get_asset` function:

```python
@router.get("/tags", response_model=list[str])
async def get_asset_tags(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all unique tags used across the org's assets, sorted alphabetically."""
    result = await db.execute(
        select(Asset.tags).where(Asset.organization_id == user.organization_id)
    )
    all_tags: set[str] = set()
    for (tags,) in result:
        if tags:
            all_tags.update(tags)
    return sorted(all_tags)
```

- [ ] **Step 2: Verify route ordering in `assets.py`**

The file should have routes in this order:
1. `GET ""` — list_assets
2. `GET "/tags"` — get_asset_tags  ← must come before /{asset_id}
3. `POST ""` — create_asset
4. `GET "/{asset_id}"` — get_asset

Confirm by reading the file and checking route order.

- [ ] **Step 3: Rebuild and test**

```bash
docker compose up --build -d backend
```

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/assets/tags
```

Expected: `[]` (no tags yet — correct, the column is empty by default).

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/assets.py
git commit -m "feat: add GET /assets/tags endpoint"
```

---

### Task 5: PATCH /assets/{id} — partial update

**Files:**
- Modify: `backend/app/routers/assets.py`

- [ ] **Step 1: Add `update_asset` route to `assets.py`**

Add after `get_asset`:

```python
@router.patch("/{asset_id}", response_model=AssetRead)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    if body.name is not None:
        asset.name = body.name
    if body.criticality is not None:
        asset.criticality = body.criticality
    if body.asset_metadata is not None:
        asset.asset_metadata = body.asset_metadata
    if body.tags is not None:
        asset.tags = body.tags

    await record_event(db, user.organization_id, "asset.updated",
                       {"asset_id": str(asset.id), "changes": body.model_dump(exclude_none=True)},
                       actor_id=user.id)
    await db.commit()
    await db.refresh(asset)
    return asset
```

- [ ] **Step 2: Rebuild and test**

```bash
docker compose up --build -d backend
```

Get an asset ID from the list, then patch it:
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
ASSET_ID=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/assets | python3 -c "import sys,json; assets=json.load(sys.stdin); print(assets[0]['id']) if assets else print('no assets')")
curl -s -X PATCH "http://localhost:8000/assets/$ASSET_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["pci-scope", "payments"]}' | python3 -c "import sys,json; a=json.load(sys.stdin); print(f'tags: {a[\"tags\"]}')"
```

Expected: `tags: ['pci-scope', 'payments']`

Then check `GET /assets/tags` returns `["payments", "pci-scope"]`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/assets.py
git commit -m "feat: add PATCH /assets/{id} partial update endpoint"
```

---

### Task 6: PATCH /assets/bulk-tag — bulk operations

**Files:**
- Modify: `backend/app/routers/assets.py`

**Route ordering note:** `PATCH /assets/bulk-tag` must be registered **before** `PATCH /assets/{asset_id}` to avoid `bulk-tag` being treated as a UUID (it isn't, but FastAPI would fail to parse it). Add this route before the `update_asset` function.

- [ ] **Step 1: Add `bulk_tag_assets` route**

Add before `update_asset` in `assets.py`:

```python
@router.patch("/bulk-tag")
async def bulk_tag_assets(
    body: BulkTagOperation,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify all asset_ids belong to this org
    result = await db.execute(
        select(Asset).where(
            Asset.id.in_(body.asset_ids),
            Asset.organization_id == user.organization_id,
        )
    )
    assets = result.scalars().all()

    if len(assets) != len(body.asset_ids):
        raise HTTPException(status_code=403, detail="One or more assets not found or not accessible")

    for asset in assets:
        current_tags: list[str] = asset.tags or []
        if body.operation == "add":
            new_tags = list(dict.fromkeys(current_tags + body.tags))  # preserve order, deduplicate
        elif body.operation == "remove":
            new_tags = [t for t in current_tags if t not in body.tags]
        else:  # "set"
            new_tags = list(body.tags)
        asset.tags = new_tags

    await record_event(
        db, user.organization_id, "asset.bulk_tagged",
        {"operation": body.operation, "tags": body.tags, "asset_count": len(assets)},
        actor_id=user.id,
    )
    await db.commit()
    return {"updated": len(assets)}
```

- [ ] **Step 2: Rebuild and test add operation**

```bash
docker compose up --build -d backend
```

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
# Get first two asset IDs
IDS=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/assets | python3 -c "import sys,json; assets=json.load(sys.stdin); print(','.join([a['id'] for a in assets[:2]]))")
ID1=$(echo $IDS | cut -d',' -f1)
ID2=$(echo $IDS | cut -d',' -f2)
curl -s -X PATCH http://localhost:8000/assets/bulk-tag \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"asset_ids\": [\"$ID1\", \"$ID2\"], \"operation\": \"add\", \"tags\": [\"microseg-phase-1\"]}" | python3 -c "import sys,json; print(json.load(sys.stdin))"
```

Expected: `{'updated': 2}`

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/assets.py
git commit -m "feat: add PATCH /assets/bulk-tag endpoint"
```

---

### Task 7: Add asset_id filter to GET /change-requests

**Files:**
- Modify: `backend/app/routers/change_requests.py`

Needed for the AssetDetail page's linked change requests panel.

- [ ] **Step 1: Add `asset_id` query param to `list_change_requests`**

In `backend/app/routers/change_requests.py`, update the `list_change_requests` function signature to add `asset_id`:

```python
@router.get("", response_model=list[ChangeRequestSummary])
async def list_change_requests(
    status: str | None = Query(None),
    risk_level: str | None = Query(None),
    change_type: str | None = Query(None),
    asset_id: str | None = Query(None, description="Filter to CRs targeting this asset"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
```

Then after the existing `if status:` / `if risk_level:` / `if change_type:` blocks, add:

```python
    result = await db.execute(q)
    crs = result.scalars().all()

    # asset_id filter applied in Python (target_asset_ids is a JSON array of strings)
    if asset_id:
        crs = [cr for cr in crs if asset_id in (cr.target_asset_ids or [])]

    return crs
```

**Important:** The existing code runs `result = await db.execute(q)` and then `return result.scalars().all()`. Replace that final block with the above so the Python post-filter is applied before returning.

- [ ] **Step 2: Rebuild and verify**

```bash
docker compose up --build -d backend
```

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/change-requests?asset_id=nonexistent-id" | python3 -c "import sys,json; print(f'Result: {json.load(sys.stdin)}')"
```

Expected: `Result: []` (empty list for non-existent asset_id, no error).

- [ ] **Step 3: Run full backend test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -v --tb=short" 2>&1 | tail -5
```

Expected: all 81 tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/change_requests.py
git commit -m "feat: add asset_id filter to GET /change-requests"
```

---

### Task 8: Frontend types + API client

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/api/endpoints.ts`

- [ ] **Step 1: Update `frontend/src/types/api.ts`**

Add `tags` to `Asset`, and add `AssetUpdate`, `AssetListParams`, and `BulkTagOperation` types. Update the `Asset` interface and `AssetCreate` interface, and add the new types after `AssetCreate`:

```typescript
export interface Asset {
  id: string;
  organization_id: string;
  name: string;
  asset_type: AssetType;
  environment: Environment;
  criticality: Criticality;
  asset_metadata: Record<string, unknown>;
  tags: string[];
  created_at: string;
}

export interface AssetCreate {
  name: string;
  asset_type: AssetType;
  environment: Environment;
  criticality: Criticality;
  asset_metadata?: Record<string, unknown>;
  tags?: string[];
}

export interface AssetUpdate {
  name?: string;
  criticality?: Criticality;
  asset_metadata?: Record<string, unknown>;
  tags?: string[];
}

export interface AssetListParams {
  q?: string;
  env?: string;
  asset_type?: string;
  criticality?: string;
  tag?: string;
}

export type BulkTagOperation = "add" | "remove" | "set";

export interface BulkTagBody {
  asset_ids: string[];
  operation: BulkTagOperation;
  tags: string[];
}
```

- [ ] **Step 2: Update `frontend/src/api/endpoints.ts`**

Replace the `assetsApi` object:

```typescript
import type {
  Token, User, Asset, AssetCreate, AssetUpdate, AssetListParams, BulkTagBody,
  Connector, ConnectorCreate, ConnectorTestResult,
  ChangeRequest, ChangeRequestSummary, ChangeRequestCreate,
  ChangePlan, Approval, ApprovalCreate, ExecutionRun, AuditEvent,
} from "../types/api";

// Assets
export const assetsApi = {
  list: (params?: AssetListParams) =>
    apiClient.get<Asset[]>("/assets", { params }).then((r) => r.data),
  get: (id: string) =>
    apiClient.get<Asset>(`/assets/${id}`).then((r) => r.data),
  create: (data: AssetCreate) =>
    apiClient.post<Asset>("/assets", data).then((r) => r.data),
  update: (id: string, data: AssetUpdate) =>
    apiClient.patch<Asset>(`/assets/${id}`, data).then((r) => r.data),
  tags: () =>
    apiClient.get<string[]>("/assets/tags").then((r) => r.data),
  bulkTag: (data: BulkTagBody) =>
    apiClient.patch<{ updated: number }>("/assets/bulk-tag", data).then((r) => r.data),
};
```

Also update `changeRequestsApi.list` to accept `asset_id`:

```typescript
export const changeRequestsApi = {
  list: (params?: { status?: string; risk_level?: string; change_type?: string; asset_id?: string }) =>
    apiClient.get<ChangeRequestSummary[]>("/change-requests", { params }).then((r) => r.data),
  // ... rest unchanged
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
docker compose exec frontend npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/api/endpoints.ts
git commit -m "feat: add asset tags types and API client methods"
```

---

### Task 9: Rewrite Assets.tsx — search, filter, bulk tagging

**Files:**
- Modify: `frontend/src/pages/Assets.tsx`

This is a full rewrite. The new page:
- Uses `useSearchParams` to sync filter state with the URL
- Parses token syntax from the search bar (`env:prod`, `tag:pci-scope`, etc.)
- Shows filter dropdowns for Environment, Type, Criticality, and a tag type-ahead
- Shows asset cards with tags displayed as chips, each card clickable to navigate to `/assets/:id`
- Shows checkboxes per row for bulk selection
- Shows a sticky bulk toolbar when ≥1 asset is selected

- [ ] **Step 1: Write the new `Assets.tsx`**

```tsx
import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, useNavigate } from "react-router-dom";
import { Plus, Search, Tag, X, ChevronRight } from "lucide-react";
import { assetsApi } from "../api/endpoints";
import { RiskBadge } from "../components/RiskBadge";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import type { AssetType, Environment, Criticality, Asset } from "../types/api";

const ASSET_TYPE_ICONS: Record<AssetType, string> = {
  server: "🖥",
  cloud_account: "☁️",
  dns_zone: "🌐",
  firewall: "🛡",
  identity_provider: "🔑",
  application: "📦",
};

// Parses "payments env:prod tag:pci-scope" into { q: "payments", filters: { env: "prod", tag: "pci-scope" } }
function parseSearch(input: string): { q: string; filters: Record<string, string> } {
  const KEY_MAP: Record<string, string> = {
    env: "env",
    type: "asset_type",
    criticality: "criticality",
    tag: "tag",
  };
  const filters: Record<string, string> = {};
  let remaining = input;
  const tokenRegex = /\b(\w+):(\S+)/g;
  let match;
  while ((match = tokenRegex.exec(input)) !== null) {
    const [full, key, value] = match;
    if (KEY_MAP[key]) {
      filters[KEY_MAP[key]] = value;
      remaining = remaining.replace(full, "").trim();
    }
  }
  return { q: remaining.trim(), filters };
}

function buildSearchString(q: string, filters: Record<string, string>): string {
  const REVERSE_MAP: Record<string, string> = {
    env: "env",
    asset_type: "type",
    criticality: "criticality",
    tag: "tag",
  };
  const tokens = Object.entries(filters)
    .filter(([, v]) => v)
    .map(([k, v]) => `${REVERSE_MAP[k] ?? k}:${v}`);
  return [q, ...tokens].filter(Boolean).join(" ");
}

export function Assets() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<AssetType>("server");
  const [newEnv, setNewEnv] = useState<Environment>("dev");
  const [newCrit, setNewCrit] = useState<Criticality>("medium");

  // Bulk selection
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showBulkAdd, setShowBulkAdd] = useState(false);
  const [showBulkRemove, setShowBulkRemove] = useState(false);
  const [bulkTagInput, setBulkTagInput] = useState("");

  // Derive filter params from URL
  const rawSearch = searchParams.get("search") ?? "";
  const { q, filters } = parseSearch(rawSearch);

  const apiParams = {
    ...(q && { q }),
    ...filters,
  };
  const hasFilters = rawSearch.length > 0;

  const { data: assets, isLoading } = useQuery({
    queryKey: ["assets", apiParams],
    queryFn: () => assetsApi.list(apiParams),
  });

  const { data: allTags } = useQuery({
    queryKey: ["asset-tags"],
    queryFn: assetsApi.tags,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      assetsApi.create({ name: newName, asset_type: newType, environment: newEnv, criticality: newCrit }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      setShowForm(false);
      setNewName("");
    },
  });

  const bulkTagMutation = useMutation({
    mutationFn: ({ operation, tags }: { operation: "add" | "remove" | "set"; tags: string[] }) =>
      assetsApi.bulkTag({ asset_ids: Array.from(selected), operation, tags }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset-tags"] });
      setSelected(new Set());
      setShowBulkAdd(false);
      setShowBulkRemove(false);
      setBulkTagInput("");
    },
  });

  function setSearch(value: string) {
    if (value) {
      setSearchParams({ search: value });
    } else {
      setSearchParams({});
    }
    setSelected(new Set());
  }

  function setFilter(key: string, value: string) {
    const newFilters = { ...filters, [key]: value };
    if (!value) delete newFilters[key];
    setSearch(buildSearchString(q, newFilters));
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (!assets) return;
    if (selected.size === assets.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(assets.map((a) => a.id)));
    }
  }

  // Tags on selected assets (union) — for the remove popover
  const selectedAssets = (assets ?? []).filter((a) => selected.has(a.id));
  const selectedTagUnion = Array.from(new Set(selectedAssets.flatMap((a) => a.tags ?? [])));

  if (isLoading) return <PageLoading />;

  // Grouped view only when no filters active
  const groupedByEnv = {
    prod: (assets ?? []).filter((a) => a.environment === "prod"),
    staging: (assets ?? []).filter((a) => a.environment === "staging"),
    dev: (assets ?? []).filter((a) => a.environment === "dev"),
  };

  return (
    <div className="p-8">
      <PageHeader
        title="Asset Inventory"
        subtitle={`${assets?.length ?? 0} assets`}
        actions={
          <button
            onClick={() => setShowForm(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            <Plus className="w-4 h-4" />
            Add Asset
          </button>
        }
      />

      {/* Search bar + filter dropdowns */}
      <div className="mb-4 flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Search assets… or use env:prod tag:pci-scope"
            value={rawSearch}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          {rawSearch && (
            <button onClick={() => setSearch("")} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
        <select
          value={filters.env ?? ""}
          onChange={(e) => setFilter("env", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All Environments</option>
          {["dev", "staging", "prod"].map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
        <select
          value={filters.asset_type ?? ""}
          onChange={(e) => setFilter("asset_type", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All Types</option>
          {["server", "cloud_account", "dns_zone", "firewall", "identity_provider", "application"].map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
          ))}
        </select>
        <select
          value={filters.criticality ?? ""}
          onChange={(e) => setFilter("criticality", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All Criticalities</option>
          {["low", "medium", "high", "critical"].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {/* Tag filter: native datalist for type-ahead */}
        <div className="relative">
          <Tag className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            list="tag-options"
            placeholder="Filter by tag…"
            value={filters.tag ?? ""}
            onChange={(e) => setFilter("tag", e.target.value)}
            className="pl-7 pr-3 py-2 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-500 w-44"
          />
          <datalist id="tag-options">
            {(allTags ?? []).map((t) => <option key={t} value={t} />)}
          </datalist>
        </div>
      </div>

      {/* Add asset form */}
      {showForm && (
        <div className="mb-6 bg-white border border-slate-200 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Add Asset</h3>
          <div className="grid grid-cols-2 gap-3 mb-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Name</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Type</label>
              <select value={newType} onChange={(e) => setNewType(e.target.value as AssetType)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["server","cloud_account","dns_zone","firewall","identity_provider","application"].map((t) => (
                  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Environment</label>
              <select value={newEnv} onChange={(e) => setNewEnv(e.target.value as Environment)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["dev","staging","prod"].map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Criticality</label>
              <select value={newCrit} onChange={(e) => setNewCrit(e.target.value as Criticality)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["low","medium","high","critical"].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => createMutation.mutate()} disabled={!newName || createMutation.isPending}
              className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50">
              {createMutation.isPending ? "Adding…" : "Add Asset"}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-3 py-1.5 border border-slate-200 text-slate-600 text-sm rounded hover:bg-slate-50">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Bulk toolbar */}
      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 bg-brand-50 border border-brand-200 rounded-lg px-4 py-2.5">
          <span className="text-sm font-medium text-brand-700">{selected.size} asset{selected.size > 1 ? "s" : ""} selected</span>
          <div className="flex gap-2 ml-2">
            <div className="relative">
              <button onClick={() => { setShowBulkAdd(!showBulkAdd); setShowBulkRemove(false); }}
                className="px-3 py-1 text-sm bg-white border border-slate-200 rounded hover:bg-slate-50">
                Add tags
              </button>
              {showBulkAdd && (
                <div className="absolute top-8 left-0 z-10 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-56">
                  <label className="block text-xs text-slate-500 mb-1">Tags to add (comma-separated)</label>
                  <input
                    list="tag-options-bulk"
                    value={bulkTagInput}
                    onChange={(e) => setBulkTagInput(e.target.value)}
                    placeholder="e.g. pci-scope, payments"
                    className="w-full text-sm border border-slate-200 rounded px-2 py-1 mb-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                  <datalist id="tag-options-bulk">
                    {(allTags ?? []).map((t) => <option key={t} value={t} />)}
                  </datalist>
                  <button
                    disabled={!bulkTagInput.trim() || bulkTagMutation.isPending}
                    onClick={() => {
                      const tags = bulkTagInput.split(",").map((t) => t.trim()).filter(Boolean);
                      if (tags.length) bulkTagMutation.mutate({ operation: "add", tags });
                    }}
                    className="w-full px-2 py-1 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50"
                  >
                    {bulkTagMutation.isPending ? "Applying…" : "Apply"}
                  </button>
                </div>
              )}
            </div>
            <div className="relative">
              <button onClick={() => { setShowBulkRemove(!showBulkRemove); setShowBulkAdd(false); }}
                className="px-3 py-1 text-sm bg-white border border-slate-200 rounded hover:bg-slate-50">
                Remove tags
              </button>
              {showBulkRemove && (
                <div className="absolute top-8 left-0 z-10 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-56">
                  <label className="block text-xs text-slate-500 mb-2">Tags on selected assets</label>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {selectedTagUnion.length === 0 && (
                      <span className="text-xs text-slate-400">No tags on selected assets</span>
                    )}
                    {selectedTagUnion.map((t) => (
                      <button key={t}
                        onClick={() => bulkTagMutation.mutate({ operation: "remove", tags: [t] })}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 text-slate-700 rounded-full hover:bg-red-100 hover:text-red-700">
                        {t} <X className="w-3 h-3" />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
          <button onClick={() => setSelected(new Set())}
            className="ml-auto text-sm text-slate-500 hover:text-slate-700">
            Clear selection
          </button>
        </div>
      )}

      {/* Asset list */}
      {hasFilters ? (
        // Flat list when filters are active
        <div>
          {/* Select all row */}
          <div className="flex items-center gap-2 mb-2 px-1">
            <input type="checkbox"
              checked={selected.size === (assets?.length ?? 0) && (assets?.length ?? 0) > 0}
              onChange={toggleSelectAll}
              className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            />
            <span className="text-xs text-slate-400">Select all {assets?.length} results</span>
          </div>
          <div className="space-y-2">
            {(assets ?? []).map((asset) => (
              <AssetRow key={asset.id} asset={asset} selected={selected.has(asset.id)}
                onToggle={() => toggleSelected(asset.id)} onClick={() => navigate(`/assets/${asset.id}`)} />
            ))}
            {assets?.length === 0 && (
              <div className="text-center py-12 text-slate-400 text-sm">No assets match your search.</div>
            )}
          </div>
        </div>
      ) : (
        // Grouped view when no filters
        <div className="space-y-6">
          {(["prod", "staging", "dev"] as Environment[]).map((env) => {
            const envAssets = groupedByEnv[env];
            if (envAssets.length === 0) return null;
            return (
              <div key={env}>
                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">
                  {env} ({envAssets.length})
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {envAssets.map((asset) => (
                    <AssetCard key={asset.id} asset={asset} selected={selected.has(asset.id)}
                      onToggle={() => toggleSelected(asset.id)} onClick={() => navigate(`/assets/${asset.id}`)} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface AssetRowProps { asset: Asset; selected: boolean; onToggle: () => void; onClick: () => void; }

function AssetRow({ asset, selected, onToggle, onClick }: AssetRowProps) {
  return (
    <div className={`flex items-center gap-3 bg-white border rounded-lg px-4 py-3 hover:border-brand-300 transition-colors ${selected ? "border-brand-300 bg-brand-50" : "border-slate-200"}`}>
      <input type="checkbox" checked={selected} onChange={onToggle} onClick={(e) => e.stopPropagation()}
        className="rounded border-slate-300 text-brand-600 focus:ring-brand-500 shrink-0" />
      <span className="text-lg">{ASSET_TYPE_ICONS[asset.asset_type]}</span>
      <button onClick={onClick} className="flex-1 text-left">
        <div className="text-sm font-medium text-slate-900">{asset.name}</div>
        <div className="text-xs text-slate-400">{asset.asset_type.replace(/_/g, " ")} · {asset.environment}</div>
      </button>
      <div className="flex flex-wrap gap-1">
        {(asset.tags ?? []).slice(0, 3).map((t) => (
          <span key={t} className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-600 rounded-full">{t}</span>
        ))}
        {(asset.tags ?? []).length > 3 && (
          <span className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-400 rounded-full">+{asset.tags.length - 3}</span>
        )}
      </div>
      <RiskBadge level={asset.criticality} size="sm" />
      <ChevronRight className="w-4 h-4 text-slate-300 shrink-0" />
    </div>
  );
}

interface AssetCardProps { asset: Asset; selected: boolean; onToggle: () => void; onClick: () => void; }

function AssetCard({ asset, selected, onToggle, onClick }: AssetCardProps) {
  return (
    <div className={`bg-white border rounded-lg p-4 hover:border-brand-300 transition-colors cursor-pointer ${selected ? "border-brand-300 bg-brand-50" : "border-slate-200"}`}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <input type="checkbox" checked={selected} onChange={onToggle} onClick={(e) => e.stopPropagation()}
            className="rounded border-slate-300 text-brand-600 focus:ring-brand-500" />
          <span className="text-lg">{ASSET_TYPE_ICONS[asset.asset_type]}</span>
          <button onClick={onClick} className="text-left">
            <div className="text-sm font-medium text-slate-900">{asset.name}</div>
            <div className="text-xs text-slate-400">{asset.asset_type.replace(/_/g, " ")}</div>
          </button>
        </div>
        <RiskBadge level={asset.criticality} size="sm" />
      </div>
      {(asset.tags ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {asset.tags.map((t) => (
            <span key={t} className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-600 rounded-full">{t}</span>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify in browser**

Open http://localhost:3000/assets

Verify:
- Search bar appears at top
- Filter dropdowns are present
- Asset cards show (seeded data should be visible)
- Typing `env:prod` in the search bar filters to prod assets
- Selecting checkboxes shows the bulk toolbar
- Clicking an asset card navigates to `/assets/:id` (page will 404 — that's fixed in Task 11)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Assets.tsx
git commit -m "feat: rewrite Assets page with search, filter, and bulk tagging"
```

---

### Task 10: AssetDetail.tsx — new page

**Files:**
- Create: `frontend/src/pages/AssetDetail.tsx`

- [ ] **Step 1: Create `frontend/src/pages/AssetDetail.tsx`**

```tsx
import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Edit, Save, X, Plus } from "lucide-react";
import { assetsApi } from "../api/endpoints";
import { changeRequestsApi } from "../api/endpoints";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { PageLoading } from "../components/LoadingSpinner";
import type { Criticality } from "../types/api";

export function AssetDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editCriticality, setEditCriticality] = useState<Criticality>("medium");
  const [editTags, setEditTags] = useState<string[]>([]);
  const [editMetadata, setEditMetadata] = useState("");
  const [metadataError, setMetadataError] = useState("");
  const [tagInput, setTagInput] = useState("");

  const { data: asset, isLoading } = useQuery({
    queryKey: ["asset", id],
    queryFn: () => assetsApi.get(id!),
    enabled: !!id,
  });

  const { data: allTags } = useQuery({
    queryKey: ["asset-tags"],
    queryFn: assetsApi.tags,
  });

  const { data: linkedCRs } = useQuery({
    queryKey: ["change-requests", { asset_id: id }],
    queryFn: () => changeRequestsApi.list({ asset_id: id }),
    enabled: !!id,
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      let metadata: Record<string, unknown> | undefined;
      if (editMetadata.trim()) {
        try {
          metadata = JSON.parse(editMetadata);
        } catch {
          setMetadataError("Invalid JSON");
          throw new Error("Invalid JSON");
        }
      }
      return assetsApi.update(id!, {
        name: editName !== asset?.name ? editName : undefined,
        criticality: editCriticality !== asset?.criticality ? editCriticality : undefined,
        tags: editTags,
        ...(metadata !== undefined && { asset_metadata: metadata }),
      });
    },
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["asset", id] });
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset-tags"] });
      setEditing(false);
    },
  });

  function startEdit() {
    if (!asset) return;
    setEditName(asset.name);
    setEditCriticality(asset.criticality);
    setEditTags([...(asset.tags ?? [])]);
    setEditMetadata(JSON.stringify(asset.asset_metadata, null, 2));
    setMetadataError("");
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setMetadataError("");
    setTagInput("");
  }

  function addTag() {
    const tag = tagInput.trim();
    if (tag && !editTags.includes(tag)) {
      setEditTags([...editTags, tag]);
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setEditTags(editTags.filter((t) => t !== tag));
  }

  if (isLoading) return <PageLoading />;
  if (!asset) return <div className="p-8 text-slate-500">Asset not found.</div>;

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate("/assets")}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded hover:bg-slate-100">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{asset.name}</h1>
            <div className="text-sm text-slate-400">{asset.asset_type.replace(/_/g, " ")} · {asset.environment}</div>
          </div>
        </div>
        <div className="flex gap-2">
          {!editing ? (
            <button onClick={startEdit}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50">
              <Edit className="w-4 h-4" /> Edit
            </button>
          ) : (
            <>
              <button onClick={cancelEdit}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50">
                <X className="w-4 h-4" /> Cancel
              </button>
              <button
                onClick={() => updateMutation.mutate()}
                disabled={updateMutation.isPending || !!metadataError}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50">
                <Save className="w-4 h-4" />
                {updateMutation.isPending ? "Saving…" : "Save"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Properties + Metadata */}
        <div className="lg:col-span-2 space-y-5">
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Properties</h2>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
              <div>
                <dt className="text-xs text-slate-400">Name</dt>
                {editing ? (
                  <input value={editName} onChange={(e) => setEditName(e.target.value)}
                    className="mt-0.5 w-full text-sm border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500" />
                ) : (
                  <dd className="text-sm text-slate-900 font-medium mt-0.5">{asset.name}</dd>
                )}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Criticality</dt>
                {editing ? (
                  <select value={editCriticality} onChange={(e) => setEditCriticality(e.target.value as Criticality)}
                    className="mt-0.5 w-full text-sm border border-slate-200 rounded px-2 py-1">
                    {["low", "medium", "high", "critical"].map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : (
                  <dd className="mt-0.5"><RiskBadge level={asset.criticality} size="sm" /></dd>
                )}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Type</dt>
                <dd className="text-sm text-slate-900 mt-0.5">{asset.asset_type.replace(/_/g, " ")}</dd>
                {editing && <p className="text-xs text-slate-400 mt-0.5">Cannot be changed after creation.</p>}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Environment</dt>
                <dd className="text-sm text-slate-900 mt-0.5">{asset.environment}</dd>
                {editing && <p className="text-xs text-slate-400 mt-0.5">Cannot be changed after creation.</p>}
              </div>
            </dl>
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Metadata</h2>
            {editing ? (
              <>
                <textarea
                  value={editMetadata}
                  onChange={(e) => { setEditMetadata(e.target.value); setMetadataError(""); }}
                  rows={8}
                  className={`w-full text-xs font-mono border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 ${metadataError ? "border-red-400" : "border-slate-200"}`}
                />
                {metadataError && <p className="text-xs text-red-500 mt-1">{metadataError}</p>}
              </>
            ) : (
              Object.keys(asset.asset_metadata).length === 0 ? (
                <p className="text-sm text-slate-400">No metadata.</p>
              ) : (
                <pre className="text-xs font-mono text-slate-700 bg-slate-50 rounded p-3 overflow-auto">
                  {JSON.stringify(asset.asset_metadata, null, 2)}
                </pre>
              )
            )}
          </div>
        </div>

        {/* Right: Tags + Change Requests */}
        <div className="space-y-5">
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Tags</h2>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {(editing ? editTags : (asset.tags ?? [])).map((t) => (
                <span key={t}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 text-slate-700 rounded-full">
                  {t}
                  {editing && (
                    <button onClick={() => removeTag(t)} className="text-slate-400 hover:text-red-500">
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </span>
              ))}
              {!editing && (asset.tags ?? []).length === 0 && (
                <span className="text-xs text-slate-400">No tags. Click Edit to add.</span>
              )}
            </div>
            {editing && (
              <div className="flex gap-1 mt-2">
                <input
                  list="tag-options-detail"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTag(); } }}
                  placeholder="Add tag…"
                  className="flex-1 text-sm border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
                <datalist id="tag-options-detail">
                  {(allTags ?? []).map((t) => <option key={t} value={t} />)}
                </datalist>
                <button onClick={addTag}
                  className="p-1.5 bg-brand-600 text-white rounded hover:bg-brand-700">
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Change Requests</h2>
            {!linkedCRs || linkedCRs.length === 0 ? (
              <p className="text-xs text-slate-400">No change requests targeting this asset.</p>
            ) : (
              <div className="space-y-2">
                {linkedCRs.slice(0, 10).map((cr) => (
                  <button key={cr.id}
                    onClick={() => navigate(`/change-requests/${cr.id}`)}
                    className="w-full text-left p-2 rounded hover:bg-slate-50 border border-transparent hover:border-slate-200">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-900 truncate">{cr.title}</span>
                      <StatusBadge status={cr.status} size="sm" />
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {new Date(cr.created_at).toLocaleDateString()}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
docker compose exec frontend npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/AssetDetail.tsx
git commit -m "feat: add AssetDetail page with edit mode and tags"
```

---

### Task 11: Wire up routing and final verification

**Files:**
- Modify: `frontend/src/routes/index.tsx`

- [ ] **Step 1: Add `/assets/:id` route**

Update `frontend/src/routes/index.tsx`:

```tsx
import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "../components/Layout";
import { Dashboard } from "../pages/Dashboard";
import { ChangeRequestList } from "../pages/ChangeRequestList";
import { ChangeRequestDetail } from "../pages/ChangeRequestDetail";
import { CreateChangeRequest } from "../pages/CreateChangeRequest";
import { ApprovalsQueue } from "../pages/ApprovalsQueue";
import { Assets } from "../pages/Assets";
import { AssetDetail } from "../pages/AssetDetail";
import { Connectors } from "../pages/Connectors";
import { useAuth } from "../hooks/useAuth";

export function AppRoutes() {
  const { user, isLoading } = useAuth();

  if (isLoading) return null;
  if (!user) return <Navigate to="/login" replace />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/change-requests" element={<ChangeRequestList />} />
        <Route path="/change-requests/new" element={<CreateChangeRequest />} />
        <Route path="/change-requests/:id" element={<ChangeRequestDetail />} />
        <Route path="/approvals" element={<ApprovalsQueue />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/:id" element={<AssetDetail />} />
        <Route path="/connectors" element={<Connectors />} />
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
docker compose exec frontend npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: End-to-end manual verification**

Open http://localhost:3000/assets and verify:

1. **Search:** Type `env:prod` in the search bar → only prod assets show in a flat list. URL changes to `/assets?search=env%3Aprod`.
2. **Environment dropdown:** Select "staging" → `type:staging` token appended to search bar → staging assets shown.
3. **Clear:** Click ✕ on the search bar → URL resets → grouped view returns.
4. **Tags on cards:** After the PATCH calls in Task 5, assets with tags should show tag chips on their cards.
5. **Checkboxes:** Check 2 assets → bulk toolbar appears with "2 assets selected".
6. **Bulk add tags:** Click "Add tags", type `microseg-phase-1`, click Apply → both assets gain the tag.
7. **Bulk remove tags:** Check the same assets → Click "Remove tags" → tag chip appears → click it → tag removed.
8. **Click asset:** Click an asset name → navigates to `/assets/:id` → detail page loads.
9. **Edit:** Click Edit → Name and Criticality become inputs → Type field shows "Cannot be changed after creation." tooltip.
10. **Add tag on detail page:** In edit mode, type a tag in the input → press Enter → chip appears → Save → tag persists.
11. **Browser back:** From detail page, press browser back → returns to `/assets?search=env%3Aprod` (search preserved).
12. **Linked CRs:** Any asset that is `target_asset_ids` of a change request shows it in the panel.

- [ ] **Step 4: Run full backend test suite one final time**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -v --tb=short" 2>&1 | tail -5
```

Expected: 81 tests pass, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/index.tsx
git commit -m "feat: add /assets/:id route for asset detail page"
```
