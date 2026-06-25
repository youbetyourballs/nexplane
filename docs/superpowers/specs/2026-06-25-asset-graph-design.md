# Spec: Asset Graph — Infrastructure Relationship Model

**Date:** 2026-06-25
**Sub-project:** 3 of 6 (Repositioning series)
**Status:** Approved for implementation

---

## Summary

Add a directed asset relationship graph to Nexplane: a persistent `asset_relationships` table that models how assets depend on, connect to, host, and monitor each other. Seed existing `depends_on` hints from asset_metadata into real edges. Expose relationships via API, surface them on the asset detail page, and add two MCP tools for graph traversal.

---

## Architecture

### Data Model

New table `asset_relationships`:

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| organization_id | UUID FK | Scopes all queries |
| source_asset_id | UUID FK → assets | The "from" node |
| target_asset_id | UUID FK → assets | The "to" node |
| relationship_type | VARCHAR | See types below |
| rel_metadata | JSON | Optional annotations |
| created_by | UUID FK → users (nullable) | Null = seeded/system |
| created_at | TIMESTAMP | |

**Unique constraint:** `(organization_id, source_asset_id, target_asset_id, relationship_type)` — no duplicate edges of the same type.

**Relationship types:**
- `depends_on` — source requires target to function (seeded from metadata hints)
- `hosted_on` — source runs on target (e.g., service hosted_on server)
- `connects_to` — source has a network connection to target
- `monitors` — source observes/monitors target
- `backs_up` — source backs up target
- `manages` — source manages/controls target (e.g., SCCM manages workstation)

### New SQLAlchemy Model

`backend/app/models/asset_relationship.py`

### Graph Service

`backend/app/services/asset_graph.py`

Functions:
- `get_neighbors(db, asset_id, org_id, direction="both")` → `list[dict]` — immediate neighbors with relationship type and direction
- `get_upstream(db, asset_id, org_id, max_depth=3)` → `list[dict]` — all upstream dependencies (what this asset depends on)
- `get_downstream(db, asset_id, org_id, max_depth=3)` → `list[dict]` — all downstream dependents (what depends on this asset)

### API Router

`backend/app/routers/asset_graph.py`

Endpoints:
- `GET /assets/{asset_id}/relationships` — list all edges touching this asset (both directions)
- `POST /assets/{asset_id}/relationships` — create a new edge from this asset
- `DELETE /assets/relationships/{rel_id}` — delete an edge by ID
- `GET /assets/{asset_id}/graph` — neighborhood response: asset + its direct neighbors + edge list

All endpoints scoped to authenticated org. Standard `CurrentUser` dependency.

### Seed Migration

`backend/app/seed/seed_asset_graph.py`

Parses `asset_metadata["depends_on"]` list from every asset in all 4 demo orgs. Looks up target asset by name within the same org. Creates `AssetRelationship` records with type `depends_on`. Idempotent: skips if any relationship already exists for the org.

Called from `backend/seed.py` `main()` after scenario seeds.

### UI

**Asset detail page** (`frontend/src/pages/AssetDetailPage.tsx` or equivalent) — add a "Relationships" section below the existing detail panels. Shows:
- **Upstream** (what this asset depends on): list of asset names + relationship type + link
- **Downstream** (what depends on this asset): same format

If no relationships exist, show a soft empty state: "No relationships recorded."

Do **not** build a force-directed graph visualization — that's complex and deferred. A clean list is sufficient and forward-compatible.

### MCP Tools

Add to `backend/app/mcp_tools/assets.py`:

**`get_asset_neighbors(token, asset_id)`**
- Returns immediate neighbors (depth 1) in both directions
- Response: `{upstream: [{id, name, type, relationship_type}], downstream: [...]}`
- Docstring: "Get assets directly connected to this asset — what it depends on (upstream) and what depends on it (downstream). Use to understand blast radius before making a change."

**`get_asset_upstream(token, asset_id, max_depth=3)`**
- Returns full upstream dependency chain
- Docstring: "Get the full upstream dependency chain for an asset. Use to understand what infrastructure a change to this asset could cascade through."

---

## Alembic Migration

New migration file: `backend/alembic/versions/xxxx_add_asset_relationships.py`

Creates `asset_relationships` table with all columns, indexes on `(organization_id, source_asset_id)` and `(organization_id, target_asset_id)`, and unique constraint.

---

## Out of Scope

- Force-directed graph visualization (canvas/D3) — deferred
- Graph-based impact simulation — sub-project 5
- Automated relationship discovery from connectors — future
- Relationship editing UI beyond the API — future

---

## Acceptance Criteria

- [ ] `asset_relationships` table created via Alembic migration
- [ ] `AssetRelationship` SQLAlchemy model exists
- [ ] Graph service implements get_neighbors, get_upstream, get_downstream
- [ ] GET /assets/{id}/relationships returns edges for demo org assets
- [ ] POST /assets/{id}/relationships creates a new edge
- [ ] DELETE /assets/relationships/{id} removes an edge
- [ ] GET /assets/{id}/graph returns neighborhood
- [ ] Seed migration populates depends_on edges from asset_metadata hints
- [ ] Asset detail page shows Upstream and Downstream relationship lists
- [ ] `get_asset_neighbors` and `get_asset_upstream` MCP tools added and registered
- [ ] All 4 demo orgs have relationship data after fresh seed
