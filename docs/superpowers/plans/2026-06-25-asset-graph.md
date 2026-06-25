# Asset Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a directed asset relationship graph with persistent edges, graph traversal service, REST API, seed migration from metadata hints, UI relationship lists, and MCP tools.

**Architecture:** New `asset_relationships` table stores typed directed edges between assets. A graph service provides BFS traversal for upstream/downstream queries. The API exposes CRUD for edges plus a neighborhood endpoint. Seed migration converts `asset_metadata["depends_on"]` string hints into real edges.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL, Alembic, React/TypeScript, pytest

---

## Task 1 — Alembic migration: `asset_relationships` table

**Files:**
- `backend/alembic/versions/077_add_asset_relationships.py` (new)

### Steps

- [ ] Create the migration file `backend/alembic/versions/077_add_asset_relationships.py`:

```python
"""Add asset_relationships table

Revision ID: 077
Revises: 076
Create Date: 2026-06-25
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_relationships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(), nullable=False),
        sa.Column(
            "rel_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_asset_relationships_org_src_tgt_type",
        "asset_relationships",
        ["organization_id", "source_asset_id", "target_asset_id", "relationship_type"],
    )
    op.create_index(
        "ix_asset_relationships_org_source",
        "asset_relationships",
        ["organization_id", "source_asset_id"],
    )
    op.create_index(
        "ix_asset_relationships_org_target",
        "asset_relationships",
        ["organization_id", "target_asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_relationships_org_target", table_name="asset_relationships")
    op.drop_index("ix_asset_relationships_org_source", table_name="asset_relationships")
    op.drop_constraint(
        "uq_asset_relationships_org_src_tgt_type",
        "asset_relationships",
        type_="unique",
    )
    op.drop_table("asset_relationships")
```

- [ ] Run the migration on the EC2 platform instance:
  ```bash
  # ssh to EC2 then in container:
  docker exec nexplane-backend-1 bash -c "cd /app && alembic upgrade head"
  ```
  Expected output contains: `Running upgrade 076 -> 077, Add asset_relationships table`

- [ ] Commit:
  ```bash
  git add backend/alembic/versions/077_add_asset_relationships.py
  git commit -m "feat: add asset_relationships migration (077)"
  ```

---

## Task 2 — SQLAlchemy model: `AssetRelationship`

**Files:**
- `backend/app/models/asset_relationship.py` (new)
- `backend/app/models/__init__.py` (if it exists, add import; otherwise models are imported directly)

### Steps

- [ ] Create `backend/app/models/asset_relationship.py`:

```python
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class AssetRelationship(Base):
    __tablename__ = "asset_relationships"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_asset_id",
            "target_asset_id",
            "relationship_type",
            name="uq_asset_relationships_org_src_tgt_type",
        ),
        Index("ix_asset_relationships_org_source", "organization_id", "source_asset_id"),
        Index("ix_asset_relationships_org_target", "organization_id", "target_asset_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(String(), nullable=False)
    rel_metadata: Mapped[dict] = mapped_column(JSONB(), nullable=False, default=dict)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] Verify the model imports cleanly:
  ```bash
  docker exec nexplane-backend-1 python -c "from app.models.asset_relationship import AssetRelationship; print('OK', AssetRelationship.__tablename__)"
  ```
  Expected output: `OK asset_relationships`

- [ ] Commit:
  ```bash
  git add backend/app/models/asset_relationship.py
  git commit -m "feat: add AssetRelationship SQLAlchemy model"
  ```

---

## Task 3 — Graph service: BFS traversal

**Files:**
- `backend/app/services/asset_graph.py` (new)
- `backend/tests/test_asset_graph_service.py` (new)

### Steps

- [ ] Write failing tests first at `backend/tests/test_asset_graph_service.py`:

```python
"""Tests for asset_graph service — run against live DB via pytest-asyncio."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.asset_relationship import AssetRelationship
from app.models.organization import Organization
from app.services.asset_graph import get_neighbors, get_upstream, get_downstream


ORG_ID = uuid.UUID("00000000-ffff-0000-0000-000000000001")


@pytest.fixture
async def graph_org(db: AsyncSession):
    """Create a minimal org + 3 assets + 2 edges: A -> B -> C (depends_on)."""
    org = Organization(id=ORG_ID, name="Graph Test Org")
    db.add(org)

    asset_a = Asset(
        id=uuid.UUID("00000000-ffff-0001-0000-000000000001"),
        organization_id=ORG_ID,
        name="asset-a",
        asset_type=AssetType.server,
        environment=Environment.dev,
        criticality=Criticality.low,
        asset_metadata={},
    )
    asset_b = Asset(
        id=uuid.UUID("00000000-ffff-0001-0000-000000000002"),
        organization_id=ORG_ID,
        name="asset-b",
        asset_type=AssetType.database,
        environment=Environment.dev,
        criticality=Criticality.low,
        asset_metadata={},
    )
    asset_c = Asset(
        id=uuid.UUID("00000000-ffff-0001-0000-000000000003"),
        organization_id=ORG_ID,
        name="asset-c",
        asset_type=AssetType.storage_bucket,
        environment=Environment.dev,
        criticality=Criticality.low,
        asset_metadata={},
    )
    db.add_all([asset_a, asset_b, asset_c])

    # A depends_on B, B depends_on C
    edge_ab = AssetRelationship(
        organization_id=ORG_ID,
        source_asset_id=asset_a.id,
        target_asset_id=asset_b.id,
        relationship_type="depends_on",
    )
    edge_bc = AssetRelationship(
        organization_id=ORG_ID,
        source_asset_id=asset_b.id,
        target_asset_id=asset_c.id,
        relationship_type="depends_on",
    )
    db.add_all([edge_ab, edge_bc])
    await db.commit()

    return {"a": asset_a, "b": asset_b, "c": asset_c}


@pytest.mark.asyncio
async def test_get_neighbors_both(db: AsyncSession, graph_org):
    assets = graph_org
    neighbors = await get_neighbors(db, assets["b"].id, ORG_ID, direction="both")
    names = {n["name"] for n in neighbors}
    assert names == {"asset-a", "asset-c"}


@pytest.mark.asyncio
async def test_get_neighbors_downstream(db: AsyncSession, graph_org):
    assets = graph_org
    neighbors = await get_neighbors(db, assets["b"].id, ORG_ID, direction="downstream")
    assert len(neighbors) == 1
    assert neighbors[0]["name"] == "asset-c"
    assert neighbors[0]["direction"] == "downstream"


@pytest.mark.asyncio
async def test_get_neighbors_upstream(db: AsyncSession, graph_org):
    assets = graph_org
    neighbors = await get_neighbors(db, assets["b"].id, ORG_ID, direction="upstream")
    assert len(neighbors) == 1
    assert neighbors[0]["name"] == "asset-a"
    assert neighbors[0]["direction"] == "upstream"


@pytest.mark.asyncio
async def test_get_upstream_bfs(db: AsyncSession, graph_org):
    assets = graph_org
    # From A's perspective, upstream = what A depends on (B, C)
    result = await get_upstream(db, assets["a"].id, ORG_ID, max_depth=3)
    names = {r["name"] for r in result}
    assert names == {"asset-b", "asset-c"}


@pytest.mark.asyncio
async def test_get_downstream_bfs(db: AsyncSession, graph_org):
    assets = graph_org
    # From C's perspective, downstream = what depends on C (B, A)
    result = await get_downstream(db, assets["c"].id, ORG_ID, max_depth=3)
    names = {r["name"] for r in result}
    assert names == {"asset-a", "asset-b"}


@pytest.mark.asyncio
async def test_get_upstream_max_depth(db: AsyncSession, graph_org):
    assets = graph_org
    # max_depth=1 from A should only return B (not C)
    result = await get_upstream(db, assets["a"].id, ORG_ID, max_depth=1)
    names = {r["name"] for r in result}
    assert names == {"asset-b"}
```

- [ ] Run failing tests (expect ImportError or no-module since service doesn't exist yet):
  ```bash
  docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest tests/test_asset_graph_service.py -x 2>&1 | head -30"
  ```
  Expected: `ModuleNotFoundError: No module named 'app.services.asset_graph'`

- [ ] Create `backend/app/services/asset_graph.py`:

```python
"""
Graph traversal service for directed asset relationships.

Edge direction convention:
  source_asset_id --[relationship_type]--> target_asset_id
  Meaning: "source depends_on target" (source flows downstream to target)

get_upstream(asset_id):  what asset_id depends on  → follow source→target edges outward
get_downstream(asset_id): what depends on asset_id → follow target←source edges inward

get_neighbors direction="upstream":  assets where THIS asset is the source (it depends on them)
get_neighbors direction="downstream": assets where THIS asset is the target (they depend on it)
"""
import uuid
from collections import deque
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_relationship import AssetRelationship


async def get_neighbors(
    db: AsyncSession,
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    direction: Literal["upstream", "downstream", "both"] = "both",
) -> list[dict]:
    """
    Return immediate neighbors of asset_id within org_id.

    direction="upstream":   assets that asset_id depends on
                            (edges where source_asset_id == asset_id)
    direction="downstream": assets that depend on asset_id
                            (edges where target_asset_id == asset_id)
    direction="both":       union of upstream and downstream

    Each dict has keys: id, name, asset_type, relationship_type, direction
    """
    results: list[dict] = []

    if direction in ("upstream", "both"):
        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.target_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.source_asset_id == asset_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            results.append(
                {
                    "id": str(asset.id),
                    "name": asset.name,
                    "asset_type": str(asset.asset_type),
                    "relationship_type": rel.relationship_type,
                    "direction": "upstream",
                    "rel_id": str(rel.id),
                }
            )

    if direction in ("downstream", "both"):
        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.source_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.target_asset_id == asset_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            results.append(
                {
                    "id": str(asset.id),
                    "name": asset.name,
                    "asset_type": str(asset.asset_type),
                    "relationship_type": rel.relationship_type,
                    "direction": "downstream",
                    "rel_id": str(rel.id),
                }
            )

    return results


async def get_upstream(
    db: AsyncSession,
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    max_depth: int = 3,
) -> list[dict]:
    """
    BFS: return all assets that asset_id transitively depends on (upstream).
    Follows source→target edges outward. Stops at max_depth hops.
    Returns list of dicts with: id, name, asset_type, relationship_type, direction, depth
    """
    visited: set[uuid.UUID] = {asset_id}
    queue: deque[tuple[uuid.UUID, int]] = deque([(asset_id, 0)])
    results: list[dict] = []

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.target_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.source_asset_id == current_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            if asset.id not in visited:
                visited.add(asset.id)
                results.append(
                    {
                        "id": str(asset.id),
                        "name": asset.name,
                        "asset_type": str(asset.asset_type),
                        "relationship_type": rel.relationship_type,
                        "direction": "upstream",
                        "depth": depth + 1,
                    }
                )
                queue.append((asset.id, depth + 1))

    return results


async def get_downstream(
    db: AsyncSession,
    asset_id: uuid.UUID,
    org_id: uuid.UUID,
    max_depth: int = 3,
) -> list[dict]:
    """
    BFS: return all assets that transitively depend on asset_id (downstream).
    Follows target←source edges inward. Stops at max_depth hops.
    Returns list of dicts with: id, name, asset_type, relationship_type, direction, depth
    """
    visited: set[uuid.UUID] = {asset_id}
    queue: deque[tuple[uuid.UUID, int]] = deque([(asset_id, 0)])
    results: list[dict] = []

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        stmt = (
            select(AssetRelationship, Asset)
            .join(Asset, Asset.id == AssetRelationship.source_asset_id)
            .where(
                AssetRelationship.organization_id == org_id,
                AssetRelationship.target_asset_id == current_id,
            )
        )
        rows = await db.execute(stmt)
        for rel, asset in rows:
            if asset.id not in visited:
                visited.add(asset.id)
                results.append(
                    {
                        "id": str(asset.id),
                        "name": asset.name,
                        "asset_type": str(asset.asset_type),
                        "relationship_type": rel.relationship_type,
                        "direction": "downstream",
                        "depth": depth + 1,
                    }
                )
                queue.append((asset.id, depth + 1))

    return results
```

- [ ] Run tests again; all 6 should pass:
  ```bash
  docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest tests/test_asset_graph_service.py -v 2>&1"
  ```
  Expected: `6 passed`

- [ ] Commit:
  ```bash
  git add backend/app/services/asset_graph.py backend/tests/test_asset_graph_service.py
  git commit -m "feat: add asset_graph BFS traversal service with tests"
  ```

---

## Task 4 — API router: asset graph endpoints

**Files:**
- `backend/app/routers/asset_graph.py` (new)
- `backend/app/main.py` (add router registration)
- `backend/tests/test_asset_graph_router.py` (new)

### Steps

- [ ] Write failing router tests at `backend/tests/test_asset_graph_router.py`:

```python
"""Router tests for asset graph endpoints. Requires seeded org 1 or test fixtures."""
import uuid
import pytest
from httpx import AsyncClient

from app.models.asset_relationship import AssetRelationship


ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_list_relationships_empty(authed_client: AsyncClient, db):
    """GET /assets/{id}/relationships returns 200 empty list for asset with no edges."""
    from backend.seed import ASSET_IDS
    asset_id = str(ASSET_IDS["finance_app"])
    resp = await authed_client.get(f"/assets/{asset_id}/relationships")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_and_list_relationship(authed_client: AsyncClient, db):
    from backend.seed import ASSET_IDS
    source_id = str(ASSET_IDS["finance_app"])
    target_id = str(ASSET_IDS["linux_servers"])

    # Create edge
    resp = await authed_client.post(
        f"/assets/{source_id}/relationships",
        json={
            "target_asset_id": target_id,
            "relationship_type": "depends_on",
            "rel_metadata": {"note": "test"},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_asset_id"] == source_id
    assert data["target_asset_id"] == target_id
    assert data["relationship_type"] == "depends_on"
    rel_id = data["id"]

    # List edges
    resp2 = await authed_client.get(f"/assets/{source_id}/relationships")
    assert resp2.status_code == 200
    ids = [r["id"] for r in resp2.json()]
    assert rel_id in ids

    # Delete edge
    resp3 = await authed_client.delete(f"/assets/relationships/{rel_id}")
    assert resp3.status_code == 204

    # Verify gone
    resp4 = await authed_client.get(f"/assets/{source_id}/relationships")
    ids_after = [r["id"] for r in resp4.json()]
    assert rel_id not in ids_after


@pytest.mark.asyncio
async def test_get_graph(authed_client: AsyncClient, db):
    from backend.seed import ASSET_IDS
    asset_id = str(ASSET_IDS["finance_app"])
    resp = await authed_client.get(f"/assets/{asset_id}/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "asset" in body
    assert "neighbors" in body
    assert "edges" in body


@pytest.mark.asyncio
async def test_create_relationship_wrong_org(authed_client: AsyncClient, db):
    """Cannot create edge with target asset from a different org."""
    # Use a random UUID that doesn't exist in the test org
    fake_id = str(uuid.uuid4())
    from backend.seed import ASSET_IDS
    source_id = str(ASSET_IDS["finance_app"])
    resp = await authed_client.post(
        f"/assets/{source_id}/relationships",
        json={"target_asset_id": fake_id, "relationship_type": "depends_on"},
    )
    assert resp.status_code == 404
```

- [ ] Run failing tests:
  ```bash
  docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest tests/test_asset_graph_router.py -x 2>&1 | head -20"
  ```
  Expected: `404` or `ImportError` since router not registered yet.

- [ ] Create `backend/app/routers/asset_graph.py`:

```python
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.models.asset_relationship import AssetRelationship
from app.models.user import User
from app.routers import current_user
from app.services.asset_graph import get_neighbors

router = APIRouter(prefix="/assets", tags=["Asset Graph"])


class RelationshipCreate(BaseModel):
    target_asset_id: uuid.UUID
    relationship_type: str
    rel_metadata: dict = {}


class RelationshipRead(BaseModel):
    id: str
    organization_id: str
    source_asset_id: str
    target_asset_id: str
    relationship_type: str
    rel_metadata: dict
    created_by: Optional[str]
    created_at: str


def _rel_to_dict(rel: AssetRelationship) -> dict:
    return {
        "id": str(rel.id),
        "organization_id": str(rel.organization_id),
        "source_asset_id": str(rel.source_asset_id),
        "target_asset_id": str(rel.target_asset_id),
        "relationship_type": rel.relationship_type,
        "rel_metadata": rel.rel_metadata or {},
        "created_by": str(rel.created_by) if rel.created_by else None,
        "created_at": rel.created_at.isoformat() if rel.created_at else None,
    }


@router.get("/{asset_id}/relationships", response_model=list[RelationshipRead])
async def list_relationships(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all edges where this asset is source OR target."""
    # Verify asset belongs to org
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    stmt = select(AssetRelationship).where(
        AssetRelationship.organization_id == user.organization_id,
        (AssetRelationship.source_asset_id == asset_id)
        | (AssetRelationship.target_asset_id == asset_id),
    )
    result = await db.execute(stmt)
    rels = result.scalars().all()
    return [_rel_to_dict(r) for r in rels]


@router.post("/{asset_id}/relationships", response_model=RelationshipRead, status_code=201)
async def create_relationship(
    asset_id: uuid.UUID,
    body: RelationshipCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a directed edge: asset_id --[relationship_type]--> target_asset_id."""
    source = await db.get(Asset, asset_id)
    if not source or source.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Source asset not found")

    target = await db.get(Asset, body.target_asset_id)
    if not target or target.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Target asset not found")

    # Check for duplicate
    existing = await db.execute(
        select(AssetRelationship).where(
            AssetRelationship.organization_id == user.organization_id,
            AssetRelationship.source_asset_id == asset_id,
            AssetRelationship.target_asset_id == body.target_asset_id,
            AssetRelationship.relationship_type == body.relationship_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Relationship already exists")

    rel = AssetRelationship(
        organization_id=user.organization_id,
        source_asset_id=asset_id,
        target_asset_id=body.target_asset_id,
        relationship_type=body.relationship_type,
        rel_metadata=body.rel_metadata,
        created_by=user.id,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return _rel_to_dict(rel)


@router.delete("/relationships/{rel_id}", status_code=204)
async def delete_relationship(
    rel_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an edge by its ID."""
    rel = await db.get(AssetRelationship, rel_id)
    if not rel or rel.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Relationship not found")
    await db.delete(rel)
    await db.commit()


@router.get("/{asset_id}/graph")
async def get_graph(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return neighborhood graph for asset_id.
    Response: {asset: {...}, neighbors: [...], edges: [...]}
    neighbors: immediate neighbors with direction tag
    edges: raw AssetRelationship records for all neighbor edges
    """
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    neighbors = await get_neighbors(db, asset_id, user.organization_id, direction="both")

    # Collect all edge records for the neighborhood
    neighbor_ids = [uuid.UUID(n["id"]) for n in neighbors]
    all_edges: list[dict] = []
    if neighbor_ids:
        stmt = select(AssetRelationship).where(
            AssetRelationship.organization_id == user.organization_id,
            (
                (AssetRelationship.source_asset_id == asset_id)
                | (AssetRelationship.target_asset_id == asset_id)
            ),
        )
        result = await db.execute(stmt)
        all_edges = [_rel_to_dict(r) for r in result.scalars().all()]

    return {
        "asset": {
            "id": str(asset.id),
            "name": asset.name,
            "asset_type": str(asset.asset_type),
            "environment": str(asset.environment),
            "criticality": str(asset.criticality),
        },
        "neighbors": neighbors,
        "edges": all_edges,
    }
```

- [ ] Register the router in `backend/app/main.py`. Find the existing import block (around line 10-36) and add:

  In the import section, after the existing asset_timeline_router import, add:
  ```python
  from app.routers.asset_graph import router as asset_graph_router
  ```

  Then find where routers are included (look for `app.include_router`) and add:
  ```python
  app.include_router(asset_graph_router)
  ```
  Place it immediately after `app.include_router(assets.router)`.

- [ ] Restart the backend container to pick up the new router:
  ```bash
  docker restart nexplane-backend-1
  sleep 5
  docker exec nexplane-backend-1 curl -s http://localhost:8000/openapi.json | python -c "import json,sys; paths=json.load(sys.stdin)['paths']; print([p for p in paths if 'relationships' in p or 'graph' in p])"
  ```
  Expected: a list containing paths like `/assets/{asset_id}/relationships` and `/assets/{asset_id}/graph`.

- [ ] Run router tests:
  ```bash
  docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest tests/test_asset_graph_router.py -v 2>&1"
  ```
  Expected: `4 passed`

- [ ] Commit:
  ```bash
  git add backend/app/routers/asset_graph.py backend/app/main.py backend/tests/test_asset_graph_router.py
  git commit -m "feat: add asset graph API router (list/create/delete/graph endpoints)"
  ```

---

## Task 5 — Seed: convert `depends_on` metadata hints to real edges

**Files:**
- `backend/app/seed/seed_asset_graph.py` (new)
- `backend/seed.py` (add call in `main()`)

### Steps

- [ ] Create `backend/app/seed/seed_asset_graph.py`:

```python
"""
Seed asset relationships from asset_metadata["depends_on"] lists.

For each of the 4 demo org UUIDs, scans all assets for a "depends_on" list
in their metadata. Each entry is matched by name within the same org. If a
matching asset is found, an AssetRelationship with type="depends_on" is created.

Idempotent: skips entire org if ANY relationship already exists for that org.
"""
from __future__ import annotations
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_relationship import AssetRelationship


DEMO_ORG_IDS = [
    uuid.UUID("00000000-0000-0000-0000-000000000001"),
    uuid.UUID("00000000-0000-0000-0000-000000000002"),
    uuid.UUID("00000000-0000-0000-0000-000000000003"),
    uuid.UUID("00000000-0000-0000-0000-000000000004"),
]


async def seed_asset_graph(db: AsyncSession) -> None:
    for org_id in DEMO_ORG_IDS:
        # Idempotency check: skip if any relationship already exists for this org
        existing = await db.execute(
            select(AssetRelationship)
            .where(AssetRelationship.organization_id == org_id)
            .limit(1)
        )
        if existing.scalar_one_or_none():
            print(f"Asset graph seed: org {org_id} already has relationships, skipping.")
            continue

        # Load all assets for this org
        assets_result = await db.execute(
            select(Asset).where(Asset.organization_id == org_id)
        )
        assets = assets_result.scalars().all()

        # Build name→id lookup (lowercase for case-insensitive match)
        name_to_id: dict[str, uuid.UUID] = {
            a.name.lower(): a.id for a in assets
        }

        created = 0
        for asset in assets:
            depends_on_list = (asset.asset_metadata or {}).get("depends_on", [])
            if not isinstance(depends_on_list, list):
                continue

            for dep_name in depends_on_list:
                if not isinstance(dep_name, str) or not dep_name.strip():
                    continue

                target_id = name_to_id.get(dep_name.strip().lower())
                if target_id is None:
                    # No matching asset found in this org — skip silently
                    continue
                if target_id == asset.id:
                    # Self-loop — skip
                    continue

                rel = AssetRelationship(
                    organization_id=org_id,
                    source_asset_id=asset.id,
                    target_asset_id=target_id,
                    relationship_type="depends_on",
                    rel_metadata={},
                    created_by=None,
                )
                db.add(rel)
                created += 1

        await db.flush()
        print(f"Asset graph seed: org {org_id} — created {created} relationships.")

    await db.commit()
    print("Asset graph seed complete.")
```

- [ ] Add the call to `backend/seed.py` `main()`. Find the existing `main()` function (around line 445) and add after the scenario seeds:

  Current end of `main()`:
  ```python
  async with AsyncSessionLocal() as db:
      await seed_saas(db)
      await seed_finserv(db)
      await seed_defense(db)
  ```

  Replace with:
  ```python
  async with AsyncSessionLocal() as db:
      await seed_saas(db)
      await seed_finserv(db)
      await seed_defense(db)
  from app.seed.seed_asset_graph import seed_asset_graph
  async with AsyncSessionLocal() as db:
      await seed_asset_graph(db)
  ```

- [ ] Run the seed on EC2:
  ```bash
  docker exec nexplane-backend-1 bash -c "cd /app && python seed.py 2>&1"
  ```
  Expected output contains lines like:
  `Asset graph seed: org 00000000-0000-0000-0000-000000000002 — created N relationships.`
  and `Asset graph seed complete.`

- [ ] Verify rows were inserted:
  ```bash
  docker exec nexplane-backend-1 python -c "
  import asyncio
  from app.database import AsyncSessionLocal
  from sqlalchemy import select, func
  from app.models.asset_relationship import AssetRelationship

  async def check():
      async with AsyncSessionLocal() as db:
          result = await db.execute(select(func.count()).select_from(AssetRelationship))
          print('Total asset_relationships rows:', result.scalar())

  asyncio.run(check())
  "
  ```
  Expected: `Total asset_relationships rows: <N>` where N > 0.

- [ ] Commit:
  ```bash
  git add backend/app/seed/seed_asset_graph.py backend/seed.py
  git commit -m "feat: seed asset relationships from metadata depends_on hints"
  ```

---

## Task 6 — Frontend: Relationships section in AssetDetail

**Files:**
- `frontend/src/pages/AssetDetail.tsx` (edit)

### Steps

- [ ] Add the `useQuery` for relationships and the Relationships section to `frontend/src/pages/AssetDetail.tsx`.

  **Step 6a — Add the relationships query** after the existing `timelineEvents` query (around line 685, after the `timelineEvents` useQuery block):

  ```tsx
  const { data: relationships } = useQuery({
    queryKey: ["asset-relationships", id],
    queryFn: () =>
      apiClient
        .get(`/assets/${id}/relationships`)
        .then(
          (r) =>
            r.data as Array<{
              id: string;
              source_asset_id: string;
              target_asset_id: string;
              relationship_type: string;
              rel_metadata: Record<string, unknown>;
              created_at: string;
            }>
        ),
    enabled: !!id,
  });
  ```

  **Step 6b — Add the Relationships section** in the overview tab's right column, immediately after the `Change Requests` card (around line 1264, after the closing `</div>` of the Change Requests card). Insert this JSX block before the Software Inventory section:

  ```tsx
  {/* Relationships */}
  <div className="bg-white border border-slate-200 rounded-lg p-5">
    <h2 className="text-sm font-semibold text-slate-900 mb-3">Relationships</h2>
    {!relationships || relationships.length === 0 ? (
      <p className="text-xs text-slate-400">No relationships recorded.</p>
    ) : (() => {
      const upstream = relationships.filter(
        (r) => r.source_asset_id === asset.id
      );
      const downstream = relationships.filter(
        (r) => r.target_asset_id === asset.id
      );
      return (
        <div className="space-y-4">
          {upstream.length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">
                Depends on
              </p>
              <div className="space-y-1">
                {upstream.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => navigate(`/assets/${r.target_asset_id}`)}
                    className="w-full text-left flex items-center justify-between px-2.5 py-1.5 rounded-md border border-slate-100 hover:border-brand-200 hover:bg-brand-50 text-xs"
                  >
                    <span className="text-slate-800 font-medium truncate">
                      {r.target_asset_id}
                    </span>
                    <span className="ml-2 shrink-0 px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">
                      {r.relationship_type}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {downstream.length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">
                Depended on by
              </p>
              <div className="space-y-1">
                {downstream.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => navigate(`/assets/${r.source_asset_id}`)}
                    className="w-full text-left flex items-center justify-between px-2.5 py-1.5 rounded-md border border-slate-100 hover:border-brand-200 hover:bg-brand-50 text-xs"
                  >
                    <span className="text-slate-800 font-medium truncate">
                      {r.source_asset_id}
                    </span>
                    <span className="ml-2 shrink-0 px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">
                      {r.relationship_type}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      );
    })()}
  </div>
  ```

  **Note on asset names:** The relationship records returned by `GET /assets/{id}/relationships` contain only UUIDs, not names. The API's `GET /assets/{id}/graph` endpoint returns neighbor names. For a richer display, either:
  - Use the `/graph` endpoint instead (preferred), or
  - Accept that the initial implementation shows IDs and a follow-up can enrich them.
  
  Use the `/graph` endpoint for the query instead:

  Replace the relationships query with:
  ```tsx
  const { data: graphData } = useQuery({
    queryKey: ["asset-graph", id],
    queryFn: () =>
      apiClient
        .get(`/assets/${id}/graph`)
        .then(
          (r) =>
            r.data as {
              asset: { id: string; name: string; asset_type: string };
              neighbors: Array<{
                id: string;
                name: string;
                asset_type: string;
                relationship_type: string;
                direction: "upstream" | "downstream";
                rel_id: string;
              }>;
              edges: Array<{
                id: string;
                source_asset_id: string;
                target_asset_id: string;
                relationship_type: string;
              }>;
            }
        ),
    enabled: !!id,
  });
  ```

  Then update the Relationships JSX to use `graphData?.neighbors`:

  ```tsx
  {/* Relationships */}
  <div className="bg-white border border-slate-200 rounded-lg p-5">
    <h2 className="text-sm font-semibold text-slate-900 mb-3">Relationships</h2>
    {!graphData?.neighbors || graphData.neighbors.length === 0 ? (
      <p className="text-xs text-slate-400">No relationships recorded.</p>
    ) : (() => {
      const upstream = graphData.neighbors.filter((n) => n.direction === "upstream");
      const downstream = graphData.neighbors.filter((n) => n.direction === "downstream");
      return (
        <div className="space-y-4">
          {upstream.length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">
                Depends on
              </p>
              <div className="space-y-1">
                {upstream.map((n) => (
                  <button
                    key={n.rel_id}
                    onClick={() => navigate(`/assets/${n.id}`)}
                    className="w-full text-left flex items-center justify-between px-2.5 py-1.5 rounded-md border border-slate-100 hover:border-brand-200 hover:bg-brand-50 text-xs"
                  >
                    <span className="text-slate-800 font-medium truncate">{n.name}</span>
                    <span className="ml-2 shrink-0 px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">
                      {n.relationship_type}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {downstream.length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wide">
                Depended on by
              </p>
              <div className="space-y-1">
                {downstream.map((n) => (
                  <button
                    key={n.rel_id}
                    onClick={() => navigate(`/assets/${n.id}`)}
                    className="w-full text-left flex items-center justify-between px-2.5 py-1.5 rounded-md border border-slate-100 hover:border-brand-200 hover:bg-brand-50 text-xs"
                  >
                    <span className="text-slate-800 font-medium truncate">{n.name}</span>
                    <span className="ml-2 shrink-0 px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">
                      {n.relationship_type}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      );
    })()}
  </div>
  ```

- [ ] After editing `frontend/src/pages/AssetDetail.tsx`, restart the frontend container (required per project rule):
  ```bash
  docker compose stop frontend && docker compose up frontend -d
  sleep 8
  docker logs nexplane-frontend-1 --tail=5
  ```
  Expected: no compilation errors in logs.

- [ ] SCP the edited file to EC2 so the live container picks it up:
  ```bash
  # From EC2:
  # (edits happen in /home/ec2-user/nexplane/frontend/src/pages/AssetDetail.tsx)
  # After editing on EC2 directly, restart frontend:
  docker compose stop frontend && docker compose up frontend -d
  ```

- [ ] Verify in browser: navigate to an asset detail page for an asset known to have `depends_on` in its metadata (e.g., org 2 / saas scenario assets). The Relationships section should show "Depends on" with clickable asset names.

- [ ] Commit:
  ```bash
  git add frontend/src/pages/AssetDetail.tsx
  git commit -m "feat: add Relationships section to AssetDetail page"
  ```

---

## Task 7 — MCP tools: `get_asset_neighbors` and `get_asset_upstream`

**Files:**
- `backend/app/mcp_tools/assets.py` (edit — append two new tools)

### Steps

- [ ] Append two new `@mcp.tool()` functions to `backend/app/mcp_tools/assets.py` (after the existing `search_assets` tool, at the end of the file):

```python
@mcp.tool()
async def get_asset_neighbors(
    token: str,
    asset_id: str,
) -> dict[str, Any]:
    """
    Get the immediate relationship neighborhood of an asset.
    Returns upstream (what this asset depends on) and downstream (what depends on this asset).
    Use before planning changes to understand blast radius and dependency chain.
    """
    from app.services.asset_graph import get_neighbors
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        asset_uuid = _uuid.UUID(asset_id)
        asset_result = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(Asset).where(
                Asset.id == asset_uuid,
                Asset.organization_id == user.organization_id,
            )
        )
        if asset_result.scalar_one_or_none() is None:
            return {"error": "Asset not found"}

        neighbors = await get_neighbors(db, asset_uuid, user.organization_id, direction="both")
        upstream = [n for n in neighbors if n["direction"] == "upstream"]
        downstream = [n for n in neighbors if n["direction"] == "downstream"]
        return {
            "asset_id": asset_id,
            "upstream": upstream,
            "downstream": downstream,
        }
    finally:
        await db_cm.__aexit__(None, None, None)


@mcp.tool()
async def get_asset_upstream(
    token: str,
    asset_id: str,
    max_depth: int = 3,
) -> list[dict[str, Any]]:
    """
    BFS traversal of all assets this asset transitively depends on (upstream chain).
    max_depth controls how many hops to follow (default 3).
    Returns list of assets with: id, name, asset_type, relationship_type, direction, depth.
    Use to answer: what would be affected if I change asset X?
    """
    from app.services.asset_graph import get_upstream
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        asset_uuid = _uuid.UUID(asset_id)
        asset_result = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(Asset).where(
                Asset.id == asset_uuid,
                Asset.organization_id == user.organization_id,
            )
        )
        if asset_result.scalar_one_or_none() is None:
            return [{"error": "Asset not found"}]

        return await get_upstream(db, asset_uuid, user.organization_id, max_depth=max_depth)
    finally:
        await db_cm.__aexit__(None, None, None)
```

  **Note:** The `__import__("sqlalchemy", fromlist=["select"]).select(...)` pattern avoids a module-level import that would be redundant with existing imports. Replace it with a local import for clarity:

  Use this cleaner pattern instead (consistent with existing tools that do local imports):
  ```python
  @mcp.tool()
  async def get_asset_neighbors(
      token: str,
      asset_id: str,
  ) -> dict[str, Any]:
      """
      Get the immediate relationship neighborhood of an asset.
      Returns upstream (what this asset depends on) and downstream (what depends on this asset).
      Use before planning changes to understand blast radius and dependency chain.
      """
      from sqlalchemy import select
      from app.services.asset_graph import get_neighbors
      from app.models.asset import Asset

      user, db, db_cm = await _auth(token)
      try:
          asset_uuid = _uuid.UUID(asset_id)
          result = await db.execute(
              select(Asset).where(
                  Asset.id == asset_uuid,
                  Asset.organization_id == user.organization_id,
              )
          )
          if result.scalar_one_or_none() is None:
              return {"error": "Asset not found"}

          neighbors = await get_neighbors(db, asset_uuid, user.organization_id, direction="both")
          upstream = [n for n in neighbors if n["direction"] == "upstream"]
          downstream = [n for n in neighbors if n["direction"] == "downstream"]
          return {
              "asset_id": asset_id,
              "upstream": upstream,
              "downstream": downstream,
          }
      finally:
          await db_cm.__aexit__(None, None, None)


  @mcp.tool()
  async def get_asset_upstream(
      token: str,
      asset_id: str,
      max_depth: int = 3,
  ) -> list[dict[str, Any]]:
      """
      BFS traversal of all assets this asset transitively depends on (upstream chain).
      max_depth controls how many hops to follow (default 3).
      Returns list of assets with: id, name, asset_type, relationship_type, direction, depth.
      Use to answer: what would be affected if I change asset X?
      """
      from sqlalchemy import select
      from app.services.asset_graph import get_upstream
      from app.models.asset import Asset

      user, db, db_cm = await _auth(token)
      try:
          asset_uuid = _uuid.UUID(asset_id)
          result = await db.execute(
              select(Asset).where(
                  Asset.id == asset_uuid,
                  Asset.organization_id == user.organization_id,
              )
          )
          if result.scalar_one_or_none() is None:
              return [{"error": "Asset not found"}]

          return await get_upstream(db, asset_uuid, user.organization_id, max_depth=max_depth)
      finally:
          await db_cm.__aexit__(None, None, None)
  ```

- [ ] Verify the MCP server loads the new tools without error:
  ```bash
  docker exec nexplane-backend-1 python -c "
  import asyncio
  from app.mcp_tools.assets import get_asset_neighbors, get_asset_upstream
  print('get_asset_neighbors:', get_asset_neighbors.__name__)
  print('get_asset_upstream:', get_asset_upstream.__name__)
  "
  ```
  Expected:
  ```
  get_asset_neighbors: get_asset_neighbors
  get_asset_upstream: get_asset_upstream
  ```

- [ ] Restart the backend container and confirm no startup errors:
  ```bash
  docker restart nexplane-backend-1
  sleep 5
  docker logs nexplane-backend-1 --tail=10
  ```
  Expected: no `ImportError` or `AttributeError` lines.

- [ ] Commit:
  ```bash
  git add backend/app/mcp_tools/assets.py
  git commit -m "feat: add get_asset_neighbors and get_asset_upstream MCP tools"
  ```

---

## Self-review checklist

- [x] Spec coverage: all 7 spec items addressed (migration, model, graph service, API router, seed, UI, MCP tools)
- [x] No placeholders: all code is fully written out, no "add error handling" stubs
- [x] Type consistency: UUID columns use `postgresql.UUID(as_uuid=True)` matching existing models; JSONB matches identity_graph migration pattern
- [x] Migration chaining: `down_revision = "076"` chains correctly from the current head
- [x] TDD: failing test step precedes implementation step in Task 3 and Task 4
- [x] Idempotent seed: org-level skip guard in `seed_asset_graph`
- [x] FILO rollback: `AssetRelationship` rows cascade-delete when either asset or org is deleted (ON DELETE CASCADE)
- [x] Frontend restart: Task 6 includes required container restart
- [x] Each commit is scoped to its task; git commands are exact
- [x] EC2 is canonical: migration and seed run steps use `docker exec nexplane-backend-1`
- [x] `depends_on` edge direction: source=dependent asset, target=dependency (consistent with "source depends_on target" semantics throughout service and UI)
