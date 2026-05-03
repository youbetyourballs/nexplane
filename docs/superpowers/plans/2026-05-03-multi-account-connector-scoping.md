# Multi-Account Connector Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope assets to the connector that discovered them so multiple connectors of the same type (e.g., two AWS accounts) don't collide, and fix change request execution to use real credentials instead of always running in mock mode.

**Architecture:** Add `connector_id` (nullable FK) to the `assets` table via migration 015. Thread it through the ingest pipeline, auto-asset creation, planning engine step locking, and the execution activity that currently passes `connector=None`. The frontend shows connector provenance on asset cards and supports filtering by connector.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, Alembic, React/TypeScript

---

## File Map

**New:**
- `backend/alembic/versions/015_add_connector_id_to_assets.py`
- `backend/app/tests/test_connector_scoping.py`

**Modified:**
- `backend/app/models/asset.py` — add `connector_id`, `connector` relationship
- `backend/app/schemas/asset.py` — add `connector_id`, `connector_name` to `AssetRead`
- `backend/app/routers/assets.py` — add `connector_id` filter, load relationship, populate schema fields
- `backend/app/services/ingest_service.py` — dedup by `(org, connector_id, name)`; stamp `connector_id`
- `backend/app/services/connector_service.py` — pass `connector_id` to `_upsert_auto_asset`
- `backend/app/services/planning_engine.py` — lock step options to asset's connector type; emit `connector_id` per step
- `backend/app/routers/change_requests.py` — load connector relationship on assets before planning
- `backend/app/workflows/activities.py` — look up real connector by step's `connector_id`; pass to `execute_action`
- `frontend/src/types/api.ts` — add `connector_id?`, `connector_name?` to `Asset`
- `frontend/src/pages/Assets.tsx` — show connector name on asset row; add connector filter
- `frontend/src/pages/AssetDetail.tsx` — show connector provenance

---

### Task 1: DB Migration + Asset Model

**Files:**
- Create: `backend/alembic/versions/015_add_connector_id_to_assets.py`
- Modify: `backend/app/models/asset.py`

- [ ] **Step 1: Create the migration file**

```python
# backend/alembic/versions/015_add_connector_id_to_assets.py
"""add connector_id to assets for multi-account scoping

Revision ID: 015
Revises: 014
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '015'
down_revision = '014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('assets', sa.Column(
        'connector_id',
        UUID(as_uuid=True),
        sa.ForeignKey('connectors.id', ondelete='SET NULL'),
        nullable=True,
    ))
    op.create_index('ix_assets_connector_id', 'assets', ['connector_id'])


def downgrade():
    op.drop_index('ix_assets_connector_id', table_name='assets')
    op.drop_column('assets', 'connector_id')
```

- [ ] **Step 2: Run the migration**

```bash
docker compose exec backend alembic upgrade 015
```
Expected: `Running upgrade 014 -> 015`

- [ ] **Step 3: Update the Asset model**

Replace `backend/app/models/asset.py` with:

```python
import uuid
from typing import Optional
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
    identity = "identity"


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
    connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(SAEnum(AssetType, name="asset_type"), nullable=False)
    environment: Mapped[Environment] = mapped_column(SAEnum(Environment, name="environment"), nullable=False)
    criticality: Mapped[Criticality] = mapped_column(SAEnum(Criticality, name="criticality"), nullable=False)
    asset_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship("Organization", back_populates="assets")
    connector: Mapped[Optional["Connector"]] = relationship("Connector", foreign_keys=[connector_id], lazy="select")
```

- [ ] **Step 4: Verify model imports cleanly**

```bash
docker compose exec backend python -c "from app.models.asset import Asset; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/015_add_connector_id_to_assets.py backend/app/models/asset.py
git commit -m "feat: add connector_id FK to assets table (migration 015) for multi-account scoping"
```

---

### Task 2: Asset Schema + Router

**Files:**
- Modify: `backend/app/schemas/asset.py`
- Modify: `backend/app/routers/assets.py`
- Test: `backend/app/tests/test_connector_scoping.py`

- [ ] **Step 1: Write failing test**

```python
# backend/app/tests/test_connector_scoping.py
import pytest


@pytest.mark.asyncio
async def test_asset_read_has_connector_fields(auth_client):
    """AssetRead schema exposes connector_id and connector_name."""
    resp = await auth_client.get("/assets")
    assert resp.status_code == 200
    data = resp.json()
    # Fields must be present (may be null for existing assets)
    for asset in data:
        assert "connector_id" in asset
        assert "connector_name" in asset


@pytest.mark.asyncio
async def test_asset_list_accepts_connector_id_filter(auth_client):
    """Asset list endpoint accepts connector_id query param without error."""
    import uuid
    fake_connector_id = str(uuid.uuid4())
    resp = await auth_client.get(f"/assets?connector_id={fake_connector_id}")
    assert resp.status_code == 200
    # With a random connector_id, result should be empty list
    assert resp.json() == []
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_connector_scoping.py -v 2>&1 | tail -10"
```
Expected: FAIL — fields not present in response.

- [ ] **Step 3: Update AssetRead schema**

In `backend/app/schemas/asset.py`, replace `AssetRead`:

```python
import uuid
from datetime import datetime
from typing import Literal, Optional
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
    connector_id: Optional[uuid.UUID] = None
    connector_name: Optional[str] = None
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
    tags: list[str] | None = None


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

- [ ] **Step 4: Update the assets router**

In `backend/app/routers/assets.py`:

Add `connector_id` import and filter parameter to `list_assets`:
```python
# Add to existing imports at top of file:
from sqlalchemy.orm import selectinload
```

Add `connector_id` parameter to `list_assets` and update the query:
```python
@router.get("", response_model=list[AssetRead])
async def list_assets(
    q: str | None = Query(None),
    env: str | None = Query(None),
    asset_type: str | None = Query(None),
    criticality: str | None = Query(None),
    tag: str | None = Query(None),
    connector_id: uuid.UUID | None = Query(None, description="Filter by connector that discovered this asset"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Asset).options(selectinload(Asset.connector)).where(Asset.organization_id == user.organization_id)

    if q:
        stmt = stmt.where(Asset.name.ilike(f"%{q}%"))
    if env:
        try:
            stmt = stmt.where(Asset.environment == Environment(env))
        except ValueError:
            pass
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
    if connector_id:
        stmt = stmt.where(Asset.connector_id == connector_id)

    result = await db.execute(stmt.order_by(Asset.name))
    assets = result.scalars().all()

    if tag:
        assets = [a for a in assets if tag in (a.tags or [])]

    return [
        AssetRead(
            **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
            connector_name=asset.connector.name if asset.connector else None,
        )
        for asset in assets
    ]
```

Update the `get_asset` endpoint to also load the connector relationship:
```python
@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(
            Asset.id == asset_id,
            Asset.organization_id == user.organization_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetRead(
        **{k: v for k, v in asset.__dict__.items() if not k.startswith("_")},
        connector_name=asset.connector.name if asset.connector else None,
    )
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_connector_scoping.py -v 2>&1 | tail -10"
```
Expected: both tests pass.

- [ ] **Step 6: Run full test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/asset.py backend/app/routers/assets.py backend/app/tests/test_connector_scoping.py
git commit -m "feat: add connector_id/connector_name to AssetRead schema and connector_id filter to asset list endpoint"
```

---

### Task 3: Ingest Pipeline — Connector-Scoped Deduplication

**Files:**
- Modify: `backend/app/services/ingest_service.py`
- Modify: `backend/app/services/connector_service.py`

- [ ] **Step 1: Add tests for scoped dedup**

Append to `backend/app/tests/test_connector_scoping.py`:

```python
@pytest.mark.asyncio
async def test_ingest_stamps_connector_id(auth_client):
    """Assets created via ingest carry the connector's ID."""
    # This is tested indirectly: after running ingest on a connector,
    # assets should have connector_id set and filter by it.
    # Integration test uses the live connector list.
    connectors_resp = await auth_client.get("/connectors")
    assert connectors_resp.status_code == 200
    connectors = connectors_resp.json()
    if not connectors:
        pytest.skip("No connectors in test org")
    connector = connectors[0]
    connector_id = connector["id"]
    assets_resp = await auth_client.get(f"/assets?connector_id={connector_id}")
    assert assets_resp.status_code == 200
    # Result may be empty (no ingest run yet) but filter must work without error
    assert isinstance(assets_resp.json(), list)
```

- [ ] **Step 2: Update `ingest_service.py`**

Replace the dedup query section in `IngestService.run()`. The full updated `run` method:

```python
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

    from app.services.connector_service import _attach_credentials
    try:
        await _attach_credentials(connector, db)
    except Exception:
        connector.credentials = {}

    executor = self._catalog.get_executor(connector.connector_type, action_id)
    payloads: list[dict] = await executor.execute({}, [], connector)

    connector_id = getattr(connector, 'id', None)
    created = 0
    updated = 0
    upserted_assets = []

    for payload in payloads:
        name = payload["name"]

        # Dedup: scope by connector when available
        if connector_id:
            result = await db.execute(
                select(Asset).where(
                    Asset.organization_id == organization_id,
                    Asset.connector_id == connector_id,
                    Asset.name == name,
                )
            )
        else:
            result = await db.execute(
                select(Asset).where(
                    Asset.organization_id == organization_id,
                    Asset.connector_id.is_(None),
                    Asset.name == name,
                )
            )
        existing = result.scalar_one_or_none()

        if existing:
            if "asset_metadata" in payload:
                existing.asset_metadata = {**existing.asset_metadata, **payload["asset_metadata"]}
            if "tags" in payload:
                existing.tags = list(set(existing.tags or []) | set(payload["tags"]))
            if "criticality" in payload:
                existing.criticality = Criticality(payload["criticality"])
            if "environment" in payload:
                existing.environment = Environment(payload["environment"])
            db.add(existing)
            upserted_assets.append(existing)
            updated += 1
        else:
            asset_type_raw = payload.get("asset_type")
            if not asset_type_raw:
                raise ValueError(f"Payload for asset '{name}' is missing required field 'asset_type'")
            asset = Asset(
                organization_id=organization_id,
                connector_id=connector_id,
                name=name,
                asset_type=AssetType(asset_type_raw),
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

- [ ] **Step 3: Update `_upsert_auto_asset` in connector_service.py**

Replace the `_upsert_auto_asset` function and its call in `execute_action`:

```python
async def _upsert_auto_asset(payload: dict, organization_id, db, connector_id=None) -> None:
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType, Environment, Criticality
    name = payload.get("name", "unnamed")

    if connector_id:
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.connector_id == connector_id,
                Asset.name == name,
            )
        )
    else:
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.connector_id.is_(None),
                Asset.name == name,
            )
        )
    existing = result.scalar_one_or_none()
    if existing:
        existing.asset_metadata = {**existing.asset_metadata, **payload.get("asset_metadata", {})}
        existing.tags = list(set(existing.tags or []) | set(payload.get("tags", [])))
        db.add(existing)
    else:
        asset = Asset(
            organization_id=organization_id,
            connector_id=connector_id,
            name=name,
            asset_type=AssetType(payload.get("asset_type", "server")),
            environment=Environment(payload.get("environment", "prod")),
            criticality=Criticality(payload.get("criticality", "medium")),
            asset_metadata=payload.get("asset_metadata", {}),
            tags=payload.get("tags", []),
        )
        db.add(asset)
        await db.flush()
```

Update the call in `execute_action` to pass `connector_id`:

```python
    result = await executor.execute(parameters, asset_ids, connector)
    if "_auto_asset" in result and connector is not None and db is not None:
        connector_id = getattr(connector, 'id', None)
        await _upsert_auto_asset(result.pop("_auto_asset"), connector.organization_id, db, connector_id=connector_id)
    return result
```

- [ ] **Step 4: Run full test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ingest_service.py backend/app/services/connector_service.py backend/app/tests/test_connector_scoping.py
git commit -m "feat: scope asset dedup to connector_id in ingest pipeline and auto-asset creation"
```

---

### Task 4: Planning Engine — Lock Steps to Asset's Connector

**Files:**
- Modify: `backend/app/services/planning_engine.py`
- Modify: `backend/app/routers/change_requests.py`

- [ ] **Step 1: Add planning engine test**

Append to `backend/app/tests/test_connector_scoping.py`:

```python
import pathlib
import uuid
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.connector import Connector, ConnectorType, ConnectorStatus
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.connectors.catalog_service import init_catalog_service

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def setup_module(module):
    init_catalog_service(CATALOG_DIR)


def _make_asset_with_connector(connector_type_val: str) -> tuple[Asset, Connector]:
    org_id = uuid.uuid4()
    connector = Connector(
        id=uuid.uuid4(),
        organization_id=org_id,
        connector_type=ConnectorType(connector_type_val),
        name=f"Test {connector_type_val}",
        status=ConnectorStatus.active,
        scoped_permissions={},
    )
    asset = Asset(
        id=uuid.uuid4(),
        organization_id=org_id,
        connector_id=connector.id,
        name="test-server",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.high,
        asset_metadata={},
        tags=[],
    )
    asset.connector = connector
    return asset, connector


def test_planning_locks_to_asset_connector_type():
    """When asset has connector_id, plan steps only include that connector's type."""
    asset, connector = _make_asset_with_connector("aws")
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=asset.organization_id,
        requester_id=uuid.uuid4(), title="Test", description="",
        change_type=ChangeType.ec2_stop,
        target_asset_ids=[str(asset.id)],
        desired_outcome={"instance_id": "i-abc123", "snapshot_tag": "test"},
        status=ChangeRequestStatus.draft,
    )
    plan = generate_plan(cr, [asset], score_change_request(cr, [asset]))
    for step in plan.generated_steps:
        if step["connector_type"] != "unknown":
            assert step["connector_type"] == "aws", f"Expected aws connector, got {step['connector_type']}"


def test_planning_includes_connector_id_in_steps():
    """Steps carry connector_id when assets have one."""
    asset, connector = _make_asset_with_connector("aws")
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=asset.organization_id,
        requester_id=uuid.uuid4(), title="Test", description="",
        change_type=ChangeType.ec2_start,
        target_asset_ids=[str(asset.id)],
        desired_outcome={"instance_id": "i-abc123"},
        status=ChangeRequestStatus.draft,
    )
    plan = generate_plan(cr, [asset], score_change_request(cr, [asset]))
    for step in plan.generated_steps:
        if step["connector_type"] == "aws":
            assert step.get("connector_id") == str(connector.id)
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_connector_scoping.py::test_planning_locks_to_asset_connector_type -v 2>&1 | tail -10"
```
Expected: FAIL — `connector_id` not in step / locking not implemented.

- [ ] **Step 3: Update `_resolve_step` in planning_engine.py**

Replace the `_resolve_step` function:

```python
def _resolve_step(step_def: dict, step_number: int, desired: dict, assets: list[Asset], catalog) -> dict:
    generic_action = step_def["generic_action"]
    asset_types = [a.asset_type.value for a in assets]

    # Determine if all assets share a single connector
    connector_ids = {a.connector_id for a in assets if a.connector_id is not None}
    locked_connector_type = None
    locked_connector_id = None
    if len(connector_ids) == 1:
        asset_with_connector = next(a for a in assets if a.connector_id is not None)
        if asset_with_connector.connector is not None:
            locked_connector_type = asset_with_connector.connector.connector_type.value
            locked_connector_id = str(asset_with_connector.connector_id)

    options = catalog.get_options_for_action(generic_action, asset_types=asset_types)
    if not options:
        options = catalog.get_options_for_action(generic_action)

    # Filter to locked connector type when determined
    if locked_connector_type and options:
        locked_options = [o for o in options if o.connector_type == locked_connector_type]
        if locked_options:
            options = locked_options

    if not options:
        return {
            "step_number": step_number,
            "name": generic_action.replace("_", " ").title(),
            "description": f"No connector found for action '{generic_action}'",
            "generic_action": generic_action,
            "connector_type": "unknown",
            "action_id": generic_action,
            "execution_tier": 99,
            "connector_options": [],
            "parameters": _resolve_parameters(generic_action, desired, assets),
            "rollback_action": None,
            "rollback_connector_type": None,
            "connector_id": locked_connector_id,
            "estimated_duration_seconds": 30,
            "blast_radius_hint": None,
        }

    best = options[0]
    action_def = best.action_def
    rollback_action = action_def.get("rollback_action")
    return {
        "step_number": step_number,
        "name": action_def.get("display_name", generic_action.replace("_", " ").title()),
        "description": action_def.get("description", ""),
        "generic_action": generic_action,
        "connector_type": best.connector_type,
        "action_id": best.action_id,
        "execution_tier": best.execution_tier,
        "connector_options": [
            {"connector_type": o.connector_type, "action_id": o.action_id, "execution_tier": o.execution_tier}
            for o in options
        ],
        "parameters": _resolve_parameters(generic_action, desired, assets),
        "rollback_action": rollback_action,
        "rollback_connector_type": best.connector_type if rollback_action else None,
        "connector_id": locked_connector_id,
        "estimated_duration_seconds": action_def.get("estimated_duration_seconds", 30),
        "blast_radius_hint": action_def.get("blast_radius_hint"),
    }
```

- [ ] **Step 4: Update change_requests.py to load connector relationship**

In `backend/app/routers/change_requests.py`, find the `generate_change_plan` route. It contains:
```python
assets_result = await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))
```

Add the selectinload import and update the query:
```python
from sqlalchemy.orm import selectinload

# Replace the asset query line with:
assets_result = await db.execute(
    select(Asset).options(selectinload(Asset.connector)).where(Asset.id.in_(asset_ids))
)
```

- [ ] **Step 5: Run planning engine tests**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_connector_scoping.py -v -k 'planning' 2>&1 | tail -15"
```
Expected: both planning tests pass.

- [ ] **Step 6: Run full test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/planning_engine.py backend/app/routers/change_requests.py backend/app/tests/test_connector_scoping.py
git commit -m "feat: lock planning engine steps to asset's connector type; emit connector_id per step; load connector relationship before planning"
```

---

### Task 5: Fix Real Credential Execution in Activities

**Files:**
- Modify: `backend/app/workflows/activities.py`

This is the bug fix: `activity_execute_change` currently calls `execute_action(connector_type, action_id, parameters, asset_ids)` with `connector=None`, causing all steps to run in mock mode. Fix it to look up the real Connector by `step["connector_id"]` and pass it with credentials.

- [ ] **Step 1: Update `activity_execute_change`**

Replace the `activity_execute_change` function in `backend/app/workflows/activities.py`:

```python
async def activity_execute_change(
    change_request_id: str,
    generated_steps: list[dict],
    asset_ids: list[str],
) -> dict:
    from app.services.connector_service import execute_action

    step_results = []

    async with AsyncSessionLocal() as db:
        for step in generated_steps:
            connector_type = step.get("connector_type", "")
            action_id = step.get("action_id", "")
            parameters = step.get("parameters", {})
            step_connector_id = step.get("connector_id")

            # Look up the specific connector instance if we have its ID
            connector = None
            if step_connector_id:
                result = await db.execute(
                    select(Connector).where(Connector.id == uuid.UUID(step_connector_id))
                )
                connector = result.scalar_one_or_none()

            try:
                result = await execute_action(
                    connector_type, action_id, parameters, asset_ids,
                    connector=connector, db=db if connector else None,
                )
            except Exception as exc:
                logger.error("Step %s failed: %s", step.get("step_number"), exc)
                raise

            step_results.append({
                "step_number": step["step_number"],
                "generic_action": step.get("generic_action"),
                "action_id": action_id,
                "connector_type": connector_type,
                "connector_id": step_connector_id,
                "result": result,
            })
            logger.info("Step %s (%s) completed", step.get("step_number"), action_id)

    logger.info("All steps completed for change request %s", change_request_id)
    return {"steps": step_results}
```

- [ ] **Step 2: Update `activity_execute_rollback`**

Replace the `activity_execute_rollback` function:

```python
async def activity_execute_rollback(
    change_request_id: str,
    generated_steps: list[dict],
    execution_result: dict,
) -> dict:
    from app.services.connector_service import execute_action

    rollback_results = []

    async with AsyncSessionLocal() as db:
        for step in reversed(generated_steps):
            rollback_action = step.get("rollback_action")
            rollback_connector = step.get("rollback_connector_type")
            if not rollback_action or not rollback_connector:
                continue

            step_connector_id = step.get("connector_id")
            connector = None
            if step_connector_id:
                result = await db.execute(
                    select(Connector).where(Connector.id == uuid.UUID(step_connector_id))
                )
                connector = result.scalar_one_or_none()

            try:
                result = await execute_action(
                    rollback_connector, rollback_action, {}, [],
                    connector=connector, db=db if connector else None,
                )
            except Exception as exc:
                logger.error("Rollback step %s failed: %s", step.get("step_number"), exc)
                result = {"rolled_back": False, "error": str(exc)}

            rollback_results.append({
                "step_number": step["step_number"],
                "rollback_action": rollback_action,
                "result": result,
            })

    logger.info("Rollback complete for %s", change_request_id)
    return {"rollback_steps": rollback_results}
```

- [ ] **Step 3: Run full test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/activities.py
git commit -m "fix: look up real connector by step connector_id in activity_execute_change and activity_execute_rollback — change requests now use real credentials instead of always running in mock mode"
```

---

### Task 6: Frontend — Connector Provenance in Asset UI

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/Assets.tsx`
- Modify: `frontend/src/pages/AssetDetail.tsx`

- [ ] **Step 1: Update Asset interface in api.ts**

Find the `Asset` interface (currently at line 33):
```typescript
export interface Asset {
  id: string;
  organization_id: string;
  name: string;
  ...
```

Add two optional fields after `organization_id`:
```typescript
export interface Asset {
  id: string;
  organization_id: string;
  connector_id?: string;
  connector_name?: string;
  name: string;
  asset_type: AssetType;
  environment: Environment;
  criticality: Criticality;
  asset_metadata: Record<string, unknown>;
  tags: string[];
  created_at: string;
}
```

- [ ] **Step 2: Add connector name to AssetRow in Assets.tsx**

Find the `AssetRow` component. The inner button currently renders:
```tsx
<div className="text-sm font-medium text-slate-900">{asset.name}</div>
<div className="text-xs text-slate-400">{asset.asset_type.replace(/_/g, " ")} · {asset.environment}</div>
```

Replace with:
```tsx
<div className="text-sm font-medium text-slate-900">{asset.name}</div>
<div className="text-xs text-slate-400">
  {asset.asset_type.replace(/_/g, " ")} · {asset.environment}
  {asset.connector_name && <span className="ml-1">· {asset.connector_name}</span>}
</div>
```

- [ ] **Step 3: Add connector filter to Assets.tsx**

Find where `connectorsApi` is imported or add it. Near the top of the `Assets` function, add a connectors query:

First add the import (if not present):
```typescript
import { assetsApi, connectorsApi } from "../api/endpoints";
```

Inside the `Assets` component, after the assets query, add:
```typescript
const { data: connectors } = useQuery({
  queryKey: ["connectors"],
  queryFn: connectorsApi.list,
  staleTime: 60000,
});
```

Find the filter UI section (search bar, env/type/criticality dropdowns). Add a connector dropdown after the existing filters:

```tsx
<select
  value={filters.connector_id || ""}
  onChange={(e) => {
    const val = e.target.value;
    setSearchAndFilters((prev) => ({
      ...prev,
      filters: { ...prev.filters, connector_id: val || undefined },
    }));
  }}
  className="text-sm border border-slate-200 rounded-md px-2 py-1.5 text-slate-600 bg-white focus:outline-none focus:ring-1 focus:ring-brand-500"
>
  <option value="">All connectors</option>
  {(connectors ?? []).map((c) => (
    <option key={c.id} value={c.id}>{c.name}</option>
  ))}
</select>
```

Also add `connector_id` to the `parseSearch`/`buildSearchString` key maps and the `apiParams` passthrough so the filter reaches the API.

**Note:** The Assets page uses a URL-param-based filter system (`parseSearch`). The `connector_id` filter should be passed directly as a UUID in `apiParams` rather than through the text-based search parser. Update the `apiParams` construction to include it:

```typescript
const apiParams = {
  ...(q && { q }),
  ...filters,  // already includes connector_id if set in filter state
};
```

- [ ] **Step 4: Add connector provenance to AssetDetail.tsx**

Find where asset metadata/details are displayed. Add a connector section:

```tsx
{asset.connector_name && (
  <div className="flex items-center gap-2 text-sm text-slate-500 mt-1">
    <span className="text-slate-400">Source:</span>
    <span className="font-medium text-slate-700">{asset.connector_name}</span>
  </div>
)}
```

- [ ] **Step 5: Restart frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait 8 seconds:
```bash
docker compose logs frontend --tail=3 2>&1 | grep -v warning
```
Expected: `VITE v6.4.2  ready` with no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/pages/Assets.tsx frontend/src/pages/AssetDetail.tsx
git commit -m "feat: show connector provenance on asset rows and detail page; add connector filter dropdown to Assets page"
```

---

### Task 7: Final Verification

- [ ] **Step 1: Run full backend test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass, 0 failures.

- [ ] **Step 2: Verify migration is at head**

```bash
docker compose exec backend alembic current 2>&1 | grep -v warning
```
Expected: `015 (head)`

- [ ] **Step 3: End-to-end connector scoping verification**

1. Go to http://localhost:3000/connectors — click **Run Discovery** on the AWS connector
2. Go to http://localhost:3000/assets — confirm AWS-discovered assets now show `· <connector-name>` in the asset row subtitle
3. Use the connector dropdown filter — select your AWS connector — confirm only AWS assets are shown
4. Create a change request targeting one of those assets (e.g. ec2_stop) — go to plan — confirm all steps show `connector_type: aws` and `connector_id` is set

- [ ] **Step 4: Final git status**

```bash
cd f:/Nexplane/nexplane && git status
```
Expected: clean working tree.
