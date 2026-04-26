# Asset Tags & Search — Design Spec

**Date:** 2026-04-26  
**Status:** Approved  
**Scope:** Free-form asset tags, server-side search/filter on the asset list, bulk tag management, and an asset detail/edit page.

---

## Problem Statement

The asset list is currently static — no search, no filtering, no way to group assets by custom characteristics. As asset inventories grow (hundreds to thousands of assets from future connector ingestion), operators need to find assets quickly and group them for project planning. Tags serve as a flexible stop-gap for characteristics not yet captured by structured fields or connector ingest.

---

## Decisions

- **Tag storage:** PostgreSQL `ARRAY(String)` column on the `assets` table. Enables native array operators (`&&` overlap, `@>` contains) for efficient tag filtering. No separate tags table — free-form labels don't need normalization at this scale.
- **Search:** Server-side. Asset inventories are expected to be large; client-side filtering is not viable.
- **Bulk operations:** Single `PATCH /assets/bulk-tag` endpoint, one DB transaction per bulk call. Not N individual PATCHes.
- **Asset editing:** Type and Environment are read-only after creation (changing them would invalidate existing change requests). Name, Criticality, Metadata, and Tags are editable.

---

## Section 1: Backend

### 1.1 Asset Model Change

Add a `tags` column to the `assets` table:

```python
# backend/app/models/asset.py
from sqlalchemy import ARRAY, String

tags = Column(ARRAY(String), nullable=False, server_default="{}")
```

Add the field to Pydantic schemas:

```python
# backend/app/schemas/asset.py
class AssetBase(BaseModel):
    tags: list[str] = []

class AssetUpdate(BaseModel):
    name: str | None = None
    criticality: Criticality | None = None
    asset_metadata: dict | None = None
    tags: list[str] | None = None  # None = no change; [] = clear all tags
```

A new Alembic migration adds the column with a default of `'{}'::text[]`.

### 1.2 New and Updated Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET /assets` | **updated** | Accepts `q`, `env`, `asset_type`, `criticality`, `tag` query params |
| `PATCH /assets/{id}` | **new** | Partial update — name, criticality, asset_metadata, tags |
| `GET /assets/tags` | **new** | All unique tags in the org, sorted alphabetically |
| `PATCH /assets/bulk-tag` | **new** | Bulk add/remove/set tags across multiple assets |

### 1.3 `GET /assets` — Filter Query Params

| Param | Type | Behavior |
|-------|------|----------|
| `q` | string | `Asset.name.ilike(f"%{q}%")` — substring match |
| `env` | enum | Exact match on `environment` |
| `asset_type` | enum | Exact match on `asset_type` |
| `criticality` | enum | Exact match on `criticality` |
| `tag` | string (repeatable) | `Asset.tags.overlap([tag])` — asset must have at least one of the specified tags |

All filters are AND-combined. Any combination is valid. No filter returns all assets (existing behaviour).

### 1.4 `PATCH /assets/{id}`

Partial update. Only provided fields are changed. Returns the updated `Asset`.

```python
class AssetUpdate(BaseModel):
    name: str | None = None
    criticality: Criticality | None = None
    asset_metadata: dict | None = None
    tags: list[str] | None = None
```

Writes an `asset.updated` audit event on success.

### 1.5 `GET /assets/tags`

```sql
SELECT DISTINCT UNNEST(tags) AS tag
FROM assets
WHERE organization_id = :org_id
  AND tags != '{}'
ORDER BY tag ASC
```

Returns: `{ "tags": ["microseg-phase-1", "payments", "pci-scope", ...] }`

Route must be registered **before** `GET /assets/{asset_id}` to avoid path collision.

### 1.6 `PATCH /assets/bulk-tag`

```python
class BulkTagOperation(BaseModel):
    asset_ids: list[UUID]           # must all belong to caller's org
    operation: Literal["add", "remove", "set"]
    tags: list[str]                 # tags to apply; must be non-empty

# Response: { "updated": N }
```

Behaviour by operation:
- `add` — append tags not already present: `tags = ARRAY(DISTINCT tags || :new_tags)`
- `remove` — remove specified tags: `tags = ARRAY(tags) - :remove_tags`
- `set` — replace entirely: `tags = :new_tags`

Executes in a single DB transaction. Verifies all `asset_ids` belong to the caller's org before updating; returns 403 if any don't. Writes a single `asset.bulk_tagged` audit event with the operation and count.

---

## Section 2: Asset List Page — Search & Bulk Tagging

### 2.1 Search Bar

A single text input replaces the current static page description. Supports:

- **Plain text** → asset name substring match (sent as `q` param)
- **Filter tokens** → parsed client-side before API call:
  - `env:prod` → `env=prod`
  - `type:server` → `asset_type=server`
  - `criticality:high` → `criticality=high`
  - `tag:pci-scope` → `tag=pci-scope`
- **Mixed** → `payments env:prod tag:microseg-phase-1` (all combined)

Token parsing strips recognised `key:value` pairs from the string; remaining text becomes `q`. Invalid/unknown tokens are ignored without error.

### 2.2 Filter Dropdown Shortcuts

Three small select controls beside the search bar: **Environment**, **Type**, **Criticality**. Selecting a value appends the corresponding token to the search input and triggers re-fetch. Clearing a dropdown removes its token. These are additive with hand-typed tokens.

A fourth **Tags** dropdown loads from `GET /assets/tags`. It contains a type-ahead input and shows all org tags as clickable chips. Selecting a tag appends `tag:<name>` to the search bar.

All four dropdowns use the same search query state — the search bar is the single source of truth.

### 2.3 URL-Driven Search State

Search state lives in the URL as query params (e.g. `/assets?env=prod&tag=pci-scope&q=api`). The Assets page reads from `useSearchParams()` on mount and writes back whenever filters change. This means searches are shareable, bookmarkable, and browser back/forward navigation works correctly. The TanStack Query key is `["assets", queryParams]` where `queryParams` is the parsed filter object derived from the URL. Re-fetch is triggered whenever query params change, debounced 300ms.

### 2.4 Grouping Behaviour

- **No active filters:** assets grouped by environment (existing behaviour)
- **Any active filter:** flat list sorted by name, groups removed

### 2.5 Bulk Tagging

Each asset row has a checkbox (leftmost column). A "select all" checkbox in the header selects all assets in the current filtered result.

When ≥1 asset is selected, a sticky bulk-action toolbar appears above the list:

```
● 12 assets selected   [Add tags ▾]  [Remove tags ▾]  [Clear selection]
```

**Add tags:** Opens a popover with a tag input (autocomplete from `GET /assets/tags`). Multiple tags can be added at once. Confirming calls `PATCH /assets/bulk-tag` with `operation: "add"`. On success: list re-fetches, selection clears, brief success toast.

**Remove tags:** Same popover but pre-populated with the union of tags across all selected assets. Only tags that exist on at least one selected asset are shown. Confirming calls `operation: "remove"`.

On API error, the toolbar shows an inline error message. Selection is preserved so the operator can retry.

---

## Section 3: Asset Detail / Edit Page

### 3.1 Route

`/assets/:id` → `AssetDetail.tsx`

Accessible by clicking any asset row in the list. The Back button returns to `/assets`. Search state lives in the URL as query params (e.g. `/assets?env=prod&tag=pci-scope`), so browser back navigation naturally restores the previous search — no special state management needed.

### 3.2 Layout

```
PageHeader: "{asset name}"                    [Edit]  [← Assets]

┌──────────────────────────┬───────────────────────────────┐
│  Properties              │  Tags                          │
│  Name        api-srv-01  │  [pci-scope ×] [payments ×]   │
│  Type        server      │  [+ Add tag...]               │
│  Environment prod        │                               │
│  Criticality high        ├───────────────────────────────┤
│                          │  Change Requests               │
│  Metadata                │  → DNS update for api.acme    │
│  {JSON viewer/editor}    │  → Snapshot prod-server-01    │
│                          │  (click to navigate)           │
└──────────────────────────┴───────────────────────────────┘
```

### 3.3 Edit Mode

Clicking **Edit** converts editable fields to inputs inline (no separate URL). Read-only fields (Type, Environment) remain as static text with a tooltip: *"Cannot be changed after creation."*

Editable fields in edit mode:
- **Name** → text input
- **Criticality** → select dropdown
- **Tags** → chip input with autocomplete (add via Enter/comma, remove via ×)
- **Metadata** → textarea with JSON validation

**Save** calls `PATCH /assets/{id}` with only changed fields. **Cancel** reverts all changes. Save button is disabled if JSON metadata is invalid. No unsaved-changes navigation guard — navigating away silently discards edits.

### 3.4 Tags in Detail View

- Displayed as removable chips in both read and edit modes
- In read mode: chips are display-only (no ×)
- In edit mode: chips have × to remove; `[+ Add tag]` input with autocomplete appears
- Tag changes are local state until Save is clicked

### 3.5 Linked Change Requests Panel

Calls `GET /change-requests?asset_id={id}` (new optional filter on the existing endpoint). Lists the 10 most recent change requests targeting this asset. Each row shows title, status badge, and created date. Clicking navigates to the change request detail page. Panel shows "No change requests" if empty.

This requires adding `asset_id` as an optional filter param to `GET /change-requests` in the backend.

---

## Summary of Backend Changes

| File | Change |
|------|--------|
| `backend/app/models/asset.py` | Add `tags = Column(ARRAY(String), ...)` |
| `backend/app/schemas/asset.py` | Add `tags` to `Asset`, add `AssetUpdate`, `BulkTagOperation` schemas |
| `backend/app/routers/assets.py` | Add filter params to `GET /assets`; add `PATCH /{id}`, `GET /tags`, `PATCH /bulk-tag` endpoints |
| `backend/app/routers/change_requests.py` | Add optional `asset_id` filter to `GET /change-requests` |
| `backend/alembic/versions/` | New migration: add `tags` column |

## Summary of Frontend Changes

| File | Change |
|------|--------|
| `frontend/src/types/api.ts` | Add `tags` to `Asset`; add `AssetUpdate`, `BulkTagOperation` types |
| `frontend/src/api/endpoints.ts` | Add `update`, `bulkTag`, `tags` methods to `assetsApi`; add `asset_id` param to `changeRequestsApi.list` |
| `frontend/src/pages/Assets.tsx` | Rewrite: add search bar, filter dropdowns, checkboxes, bulk toolbar, tag rendering |
| `frontend/src/pages/AssetDetail.tsx` | New page: detail view + edit mode + linked CRs panel |
| `frontend/src/routes/index.tsx` | Add `/assets/:id` route |
