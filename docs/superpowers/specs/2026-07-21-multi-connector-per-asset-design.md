# Multi-Connector Per Asset — Design Spec

## Problem

Assets currently have a single `connector_id` FK. An EC2 instance managed by the nexplane agent also has an AWS identity — but there is no way to express that. The planner infers one connector type for all steps from the asset's single connector, so operations like EBS snapshot (requires `aws` connector) fail to plan on agent-managed assets. There is also no API surface to associate additional connectors with an existing asset after discovery.

## Goal

Allow an asset to be associated with N connectors of different types simultaneously. The planner picks the right connector per step based on the action's required connector type. Operators can attach/detach connectors via API. Rollback always uses the connector recorded at execution time.

---

## Section 1: Data Model

### New table: `asset_connectors`

```sql
CREATE TABLE asset_connectors (
    asset_id     UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    connector_id UUID NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (asset_id, connector_id)
);
CREATE INDEX ix_asset_connectors_asset_id     ON asset_connectors(asset_id);
CREATE INDEX ix_asset_connectors_connector_id ON asset_connectors(connector_id);
```

### `assets.connector_id` stays

The existing `connector_id` FK is kept as the "primary/discovery connector" — set at ingest time, used by legacy code paths. It is not removed.

### Alembic migration backfill

The migration inserts every existing `(asset_id, connector_id)` pair from the `assets` table into `asset_connectors` before the new code runs. Idempotent via `INSERT ... ON CONFLICT DO NOTHING`.

### Asset model

```python
# models/asset.py — add alongside existing `connector` relationship
from sqlalchemy import Table, Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

asset_connectors_table = Table(
    "asset_connectors",
    Base.metadata,
    Column("asset_id",     PG_UUID(as_uuid=True), ForeignKey("assets.id",     ondelete="CASCADE"), primary_key=True),
    Column("connector_id", PG_UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at",   ..., server_default=...),
)

# On Asset:
connectors: Mapped[list["Connector"]] = relationship(
    "Connector",
    secondary="asset_connectors",
    lazy="select",
)
```

---

## Section 2: API

### New endpoints

| Method   | Path                                          | Description                              |
|----------|-----------------------------------------------|------------------------------------------|
| `GET`    | `/assets/{asset_id}/connectors`               | List all connectors associated with asset |
| `POST`   | `/assets/{asset_id}/connectors`               | Attach a connector to an asset           |
| `DELETE` | `/assets/{asset_id}/connectors/{connector_id}`| Detach a connector from an asset         |

### POST body

```json
{ "connector_id": "<uuid>" }
```

Returns `409` if already attached. Returns `404` if the connector is not in the same org as the asset.

### GET response

```json
[
  { "id": "<uuid>", "name": "aws-prod", "connector_type": "aws" },
  { "id": "<uuid>", "name": "nexplane-agent", "connector_type": "nexplane_agent" }
]
```

### `AssetRead` update

Add `connectors: list[ConnectorSummary]` field. `ConnectorSummary` contains `id`, `name`, `connector_type` — no credentials. Populated by eager-loading the `connectors` relationship on asset reads.

### New schema

```python
class ConnectorSummary(BaseModel):
    id: uuid.UUID
    name: str
    connector_type: str

class AssetConnectorAdd(BaseModel):
    connector_id: uuid.UUID
```

### Authorization

All three endpoints require the caller to be in the same org as the asset. The connector being attached must also belong to the same org. Role: any user with write access (same as PATCH /assets/{id}).

---

## Section 3: Planning Engine

### Per-step connector resolution (replaces single-asset-lock)

Today the planner derives one `locked_connector_id` from `asset.connector_id` and applies it to all steps. This is replaced with per-step resolution:

For each generated step:
1. Read the step action's required `connector_type` from the catalog.
2. Query `asset_connectors` for connectors of that type on the target asset.
3. If multiple match, pick the one with the lowest `execution_tier` from the catalog entry (cloud API before SSH — safer connector wins).
4. If no match in `asset_connectors`, fall back to org-wide connector lookup by type (existing behavior, unchanged).
5. Explicit override via `_locked_connector_id` in `desired_outcome` takes unconditional precedence (existing mechanism, unchanged).

The planner loads the asset's connectors via `selectinload(Asset.connectors)` — one join per plan, not per step.

### Execution activity (`activities.py`)

The existing two-tier fallback (plan-locked connector → org-wide by type) gains a middle tier:

1. Plan's `connector_id` (locked at plan time) — use it.
2. Asset's connector of matching type from `asset_connectors` — use it.
3. Org-wide connector of matching type — use it.

This covers cases where a plan was generated without a locked connector but execution still wants asset-scoped resolution.

### Rollback connector pinning

During rollback, the connector used is the one **recorded in `execution_runs.result`** at execution time — not re-resolved from the asset's current connectors. The rollback executor reads `step["connector_id"]` from the stored result and loads that specific connector by ID. If the connector no longer exists, rollback fails loudly rather than silently substituting a different connector. This preserves the rollback guarantee: the same connector that made the change undoes it.

The `rollback_executor.py` must be updated to enforce this — load by recorded ID, do not fall back.

---

## Section 4: Ingest & Auto-Asset Creation

Two places that set `connector_id` on assets must also write to `asset_connectors`:

### `ingest_service.py`

After upserting each discovered asset, insert `(asset_id, connector_id)` into `asset_connectors`:

```python
await db.execute(
    insert(asset_connectors_table)
    .values(asset_id=asset.id, connector_id=connector_id)
    .on_conflict_do_nothing()
)
```

Runs within the same transaction as the upsert.

### `connector_service.py` (auto-asset creation)

When an executor creates an asset mid-execution (e.g., `ec2_launch` creating an EC2 asset), same insert into `asset_connectors` immediately after the asset row is created.

Both inserts are idempotent — `ON CONFLICT DO NOTHING` means repeated ingestion cycles do not error.

---

## Section 5: MCP Tools & Smoke Tests

### `mcp_tools/assets.py`

`get_asset` and `list_assets` gain a `connectors` field — the full list of associated connectors (id, name, connector_type). This gives the AI and operators visibility into what operations are available for each asset.

### `test_smoke_mcp_cr_workflows.py`

`setup_module` currently calls `PUT /assets/{id}/connector` (returns 404). Replace with:

```python
client.post(f"/assets/{agent_asset_id}/connectors", json={"connector_id": aws_connector["id"]})
```

This attaches the AWS connector to the ephemeral EC2 agent asset. The planner then resolves the `aws` connector for `server_snapshot` steps, unblocking the 6 currently-skipped SNAPSHOT and EBS BACKUP tests.

---

## Connector Type Safety Order

When multiple connectors of the same type are available for a step and no explicit override is set, prefer the one with the lowest `execution_tier` in the catalog (safer first):

The minimum `execution_tier` across all catalog actions for a given connector type determines its position. Lower tier = safer = preferred. This is derived from the existing `execution_tier` field in catalog action entries — no new storage needed. The tiebreak is applied only when multiple connectors of the **same** connector type are associated with the asset (rare in practice).

---

## Out of Scope

- UI for managing asset connectors (operators use API/MCP)
- Connector priority/ordering beyond safety-tier tiebreaking
- Removing `assets.connector_id` — kept for backward compat
