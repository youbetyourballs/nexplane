# Multi-Account Connector Scoping — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Scope assets to the connector that discovered or created them, fix real-credential execution in change requests, and expose connector provenance in the asset inventory UI.

---

## Background

Today all assets are deduplicated by `(organization_id, name)`. Two AWS connectors pointing to different accounts can discover assets with the same name and corrupt each other's records. Change request execution passes `connector=None` to all executors, so all steps run in mock mode regardless of configured credentials. This spec fixes both problems.

---

## Design Decisions

- **`connector_id` is nullable on Asset** — manually-created assets and legacy records are unaffected. When NULL, existing dedup `(org_id, name)` behaviour applies; when set, dedup uses `(org_id, connector_id, name)`.
- **No DB-level unique constraint change** — NULL handling in unique constraints is database-specific and risky to add to existing data. Application-level dedup is sufficient and already the pattern.
- **Planning engine locks by connector type, not instance** — when assets have a `connector_id`, the step only offers actions from that connector's type. The specific instance is resolved at execution time via the step's `connector_id` field.
- **Execution fix is part of this spec** — discovering that all CR executions use mock mode makes the credential lookup fix non-optional. It ships together with scoping.
- **Connector name displayed in UI** — `AssetRead` includes `connector_name` (from a join) so the frontend can show provenance without a second API call.

---

## Section 1: Database Migration

**File:** `backend/alembic/versions/015_add_connector_id_to_assets.py`

```python
"""add connector_id to assets for multi-account scoping

Revision ID: 015
Revises: 014
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
        'connector_id', UUID(as_uuid=True),
        sa.ForeignKey('connectors.id', ondelete='SET NULL'),
        nullable=True,
    ))
    op.create_index('ix_assets_connector_id', 'assets', ['connector_id'])

def downgrade():
    op.drop_index('ix_assets_connector_id', table_name='assets')
    op.drop_column('assets', 'connector_id')
```

---

## Section 2: Asset Model + Schemas

### `backend/app/models/asset.py`

Add to the `Asset` model:
```python
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey

connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(
    UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True, index=True
)
connector: Mapped[Optional["Connector"]] = relationship("Connector", foreign_keys=[connector_id], lazy="select")
```

### `backend/app/schemas/asset.py` (or wherever AssetRead is defined)

`AssetRead` gains two new optional fields:
```python
connector_id: Optional[uuid.UUID] = None
connector_name: Optional[str] = None
```

The router populates `connector_name` by reading `asset.connector.name` when the relationship is loaded.

### `backend/app/routers/assets.py`

Asset list query gains an optional `connector_id: Optional[uuid.UUID] = Query(None)` filter applied as `Asset.connector_id == connector_id` when provided.

The `AssetRead` construction in list and get endpoints must include:
```python
connector_id=asset.connector_id,
connector_name=asset.connector.name if asset.connector else None,
```

---

## Section 3: Ingest Pipeline

### `backend/app/services/ingest_service.py`

`IngestService.run()` already receives the full `connector` object. Add `connector.id` to the dedup query:

```python
async def run(self, action_id, connector, organization_id, db):
    ...
    for payload in payloads:
        name = payload["name"]
        connector_id = connector.id if hasattr(connector, 'id') else None

        # Dedup: prefer (org, connector, name); fall back to (org, name) if no connector
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
            # update metadata/tags as before
            ...
        else:
            asset = Asset(
                organization_id=organization_id,
                connector_id=connector_id,   # NEW — stamp connector provenance
                name=name,
                ...
            )
```

### `backend/app/services/connector_service.py`

`_upsert_auto_asset` gains the same treatment:

```python
async def _upsert_auto_asset(payload, organization_id, db, connector_id=None):
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

`execute_action` extracts `connector.id` when available and passes it to `_upsert_auto_asset`:

```python
result = await executor.execute(parameters, asset_ids, connector)
if "_auto_asset" in result and connector is not None and db is not None:
    connector_id = getattr(connector, 'id', None)
    await _upsert_auto_asset(result.pop("_auto_asset"), connector.organization_id, db, connector_id=connector_id)
return result
```

---

## Section 4: Planning Engine

### `backend/app/services/planning_engine.py`

`_resolve_step` checks the asset set for a common `connector_id`. If all assets share one, it filters catalog options to only that connector's type:

```python
def _resolve_step(step_def, step_number, desired, assets, catalog):
    generic_action = step_def["generic_action"]
    asset_types = [a.asset_type.value for a in assets]

    # Determine locked connector type from assets
    connector_ids = {a.connector_id for a in assets if a.connector_id}
    locked_connector_type = None
    if len(connector_ids) == 1:
        # All assets from same connector — look up its type
        asset_with_connector = next(a for a in assets if a.connector_id)
        if asset_with_connector.connector:
            locked_connector_type = asset_with_connector.connector.connector_type.value

    options = catalog.get_options_for_action(generic_action, asset_types=asset_types)
    if not options:
        options = catalog.get_options_for_action(generic_action)

    # Filter to locked connector type if determined
    if locked_connector_type and options:
        locked_options = [o for o in options if o.connector_type == locked_connector_type]
        if locked_options:
            options = locked_options

    best = options[0] if options else None
    if not best:
        return { ...no connector found response... }

    # Determine step connector_id: use the asset's connector_id if locked
    step_connector_id = str(connector_ids.pop()) if len(connector_ids) == 1 else None

    return {
        ...existing fields...,
        "connector_id": step_connector_id,   # NEW — specific connector instance
    }
```

`generate_plan` must eagerly load the `connector` relationship on assets before passing them to `_resolve_step`. In `backend/app/routers/change_requests.py` line 128, the asset query must use `selectinload`:
```python
from sqlalchemy.orm import selectinload
assets_result = await db.execute(
    select(Asset).options(selectinload(Asset.connector)).where(Asset.id.in_(asset_ids))
)
```

---

## Section 5: Execution — Real Credential Lookup

### `backend/app/workflows/activities.py`

`activity_execute_change` currently passes `connector=None`. Fix it to look up and attach the real connector when `connector_id` is on the step:

```python
async def activity_execute_change(change_request_id, generated_steps, asset_ids):
    from app.services.connector_service import execute_action
    from app.models.connector import Connector
    from app.database import AsyncSessionLocal

    step_results = []
    async with AsyncSessionLocal() as db:
        for step in generated_steps:
            connector_type = step.get("connector_type", "")
            action_id = step.get("action_id", "")
            parameters = step.get("parameters", {})
            step_connector_id = step.get("connector_id")

            connector = None
            if step_connector_id:
                result = await db.execute(
                    select(Connector).where(Connector.id == uuid.UUID(step_connector_id))
                )
                connector = result.scalar_one_or_none()

            try:
                result = await execute_action(
                    connector_type, action_id, parameters, asset_ids,
                    connector=connector, db=db if connector else None
                )
            except Exception as exc:
                logger.error("Step %s failed: %s", step.get("step_number"), exc)
                raise

            step_results.append({...})

    return {"steps": step_results}
```

The same fix applies to `activity_execute_rollback` — look up the connector via `rollback_connector_type` and `connector_id` from the step.

---

## Section 6: API

### `backend/app/routers/assets.py`

Add `connector_id: Optional[uuid.UUID] = Query(None)` to the list endpoint. When set, add `Asset.connector_id == connector_id` to the WHERE clause.

Load the connector relationship when building `AssetRead` responses:
- For list: use `selectinload(Asset.connector)` on the query
- For get: use `joinedload(Asset.connector)`

`AssetRead` response includes:
```python
connector_id: Optional[uuid.UUID]
connector_name: Optional[str]
```

---

## Section 7: Frontend

### `frontend/src/types/api.ts`

Add to `Asset` interface:
```typescript
connector_id?: string;
connector_name?: string;
```

### `frontend/src/pages/Assets.tsx`

**Asset row:** Add a small connector badge between the asset name and type columns:
```tsx
{asset.connector_name && (
  <span className="text-xs text-slate-400 font-normal ml-1">· {asset.connector_name}</span>
)}
```

**Filter:** Add a connector filter dropdown populated from `GET /connectors`. When selected, passes `?connector_id=<uuid>` to the asset list query.

### `frontend/src/pages/AssetDetail.tsx`

Show connector provenance in the asset metadata section:
```tsx
{asset.connector_name && (
  <div className="text-sm text-slate-500">
    Source: <span className="font-medium">{asset.connector_name}</span>
  </div>
)}
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/alembic/versions/015_add_connector_id_to_assets.py` | New migration |
| `backend/app/models/asset.py` | Add `connector_id` + `connector` relationship |
| `backend/app/schemas/asset.py` | Add `connector_id`, `connector_name` to `AssetRead` |
| `backend/app/routers/assets.py` | Add connector_id filter, load relationship, populate schema fields |
| `backend/app/services/ingest_service.py` | Dedup by `(org, connector_id, name)`; stamp `connector_id` on new assets |
| `backend/app/services/connector_service.py` | Pass `connector_id` to `_upsert_auto_asset`; stamp on auto-assets |
| `backend/app/services/planning_engine.py` | Lock step options to asset's connector type; output `connector_id` per step |
| `backend/app/routers/projects.py` | Load `connector` relationship on assets before planning |
| `backend/app/workflows/activities.py` | Look up real connector by step's `connector_id`; pass to `execute_action` with db |
| `frontend/src/types/api.ts` | Add `connector_id`, `connector_name` to `Asset` |
| `frontend/src/pages/Assets.tsx` | Show connector name; add connector filter dropdown |
| `frontend/src/pages/AssetDetail.tsx` | Show connector provenance |
