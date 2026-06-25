# Impact Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Impact Simulation page functional (blast-radius analysis for any asset) and reorganize the sidebar nav into lifecycle sections.

**Architecture:** New `GET /impact-simulation` endpoint combines asset graph traversal, recent CRs, and findings count into a blast-radius response. Frontend page adds asset search + results display. Sidebar gets lifecycle section labels.

**Tech Stack:** FastAPI, SQLAlchemy async, React/TypeScript

---

## Task 1 — Backend: Impact Simulation Router

### 1a — Write the test first

- [ ] Create `backend/tests/test_impact_simulation.py`:

```python
import uuid
import pytest
from httpx import AsyncClient
from app.main import app
from app.database import get_db
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.asset_relationship import AssetRelationship
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.organization import Organization
from app.models.user import User, UserRole
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db_session():
    """Yield a test DB session — assumes conftest.py provides async_engine."""
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def test_org(db_session: AsyncSession):
    org = Organization(name="Test Org Impact", slug=f"test-impact-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


@pytest.fixture
async def test_user(db_session: AsyncSession, test_org):
    user = User(
        organization_id=test_org.id,
        email=f"user-{uuid.uuid4().hex[:6]}@example.com",
        name="Test User",
        role=UserRole.admin,
        hashed_password="x",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def assets(db_session: AsyncSession, test_org):
    """Create a small graph: db <- app <- lb"""
    db_asset = Asset(
        organization_id=test_org.id,
        name="prod-db",
        asset_type=AssetType.database,
        environment=Environment.production,
        criticality=Criticality.critical,
    )
    app_asset = Asset(
        organization_id=test_org.id,
        name="prod-app",
        asset_type=AssetType.server,
        environment=Environment.production,
        criticality=Criticality.high,
    )
    lb_asset = Asset(
        organization_id=test_org.id,
        name="prod-lb",
        asset_type=AssetType.firewall,
        environment=Environment.production,
        criticality=Criticality.medium,
    )
    db_session.add_all([db_asset, app_asset, lb_asset])
    await db_session.flush()

    # app depends_on db (app -> db)
    rel1 = AssetRelationship(
        organization_id=test_org.id,
        source_asset_id=app_asset.id,
        target_asset_id=db_asset.id,
        relationship_type="depends_on",
    )
    # lb depends_on app (lb -> app)
    rel2 = AssetRelationship(
        organization_id=test_org.id,
        source_asset_id=lb_asset.id,
        target_asset_id=app_asset.id,
        relationship_type="depends_on",
    )
    db_session.add_all([rel1, rel2])
    await db_session.commit()
    await db_session.refresh(db_asset)
    await db_session.refresh(app_asset)
    await db_session.refresh(lb_asset)
    return {"db": db_asset, "app": app_asset, "lb": lb_asset}


async def _auth_headers(client: AsyncClient, user: User, password: str = "test") -> dict:
    resp = await client.post("/auth/login", json={"email": user.email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_impact_simulation_returns_blast_radius(assets, test_user):
    async with AsyncClient(app=app, base_url="http://test") as client:
        headers = await _auth_headers(client, test_user)
        db_id = str(assets["db"].id)
        resp = await client.get(f"/impact-simulation?asset_id={db_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["asset"]["id"] == db_id
        assert data["asset"]["name"] == "prod-db"
        # app and lb are downstream of db
        downstream_ids = {d["id"] for d in data["downstream"]}
        assert str(assets["app"].id) in downstream_ids
        assert str(assets["lb"].id) in downstream_ids
        # db has no upstream
        assert data["upstream"] == []
        # risk counts
        assert data["downstream_risk"]["total"] == 2
        assert data["downstream_risk"]["high"] >= 1
        # findings placeholder
        assert data["open_findings_count"] == 0


async def test_impact_simulation_upstream(assets, test_user):
    async with AsyncClient(app=app, base_url="http://test") as client:
        headers = await _auth_headers(client, test_user)
        app_id = str(assets["app"].id)
        resp = await client.get(f"/impact-simulation?asset_id={app_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        upstream_ids = {u["id"] for u in data["upstream"]}
        assert str(assets["db"].id) in upstream_ids
        downstream_ids = {d["id"] for d in data["downstream"]}
        assert str(assets["lb"].id) in downstream_ids


async def test_impact_simulation_404_unknown_asset(test_user):
    async with AsyncClient(app=app, base_url="http://test") as client:
        headers = await _auth_headers(client, test_user)
        resp = await client.get(f"/impact-simulation?asset_id={uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404


async def test_impact_simulation_422_missing_asset_id(test_user):
    async with AsyncClient(app=app, base_url="http://test") as client:
        headers = await _auth_headers(client, test_user)
        resp = await client.get("/impact-simulation", headers=headers)
        assert resp.status_code == 422


async def test_impact_simulation_recent_crs(assets, test_user, db_session):
    # Create 6 CRs touching db_asset — expect only 5 returned
    for i in range(6):
        cr = ChangeRequest(
            organization_id=test_user.organization_id,
            requester_id=test_user.id,
            title=f"CR {i}",
            description="test",
            change_type=ChangeType.firewall_rule_change,
            target_asset_ids=[str(assets["db"].id)],
            desired_outcome={},
            risk_level=RiskLevel.low,
            status=ChangeRequestStatus.draft,
        )
        db_session.add(cr)
    await db_session.commit()

    async with AsyncClient(app=app, base_url="http://test") as client:
        headers = await _auth_headers(client, test_user)
        resp = await client.get(f"/impact-simulation?asset_id={assets['db'].id}", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["recent_crs"]) <= 5
```

### 1b — Implement the router

- [ ] Create `backend/app/routers/impact_simulation.py`:

```python
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, cast, Text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Criticality
from app.models.change_request import ChangeRequest
from app.models.user import User
from app.routers import current_user
from app.services.asset_graph import get_upstream, get_downstream

router = APIRouter(prefix="/impact-simulation", tags=["Impact Simulation"])


@router.get("")
async def get_impact_simulation(
    asset_id: uuid.UUID = Query(..., description="ID of the asset to analyse"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # 1. Fetch target asset
    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.organization_id == user.organization_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # 2. Graph traversal
    upstream = await get_upstream(db, asset_id, user.organization_id, max_depth=3)
    downstream = await get_downstream(db, asset_id, user.organization_id, max_depth=3)

    # 3. Enrich downstream with criticality via batch query
    downstream_ids = [uuid.UUID(d["id"]) for d in downstream]
    criticality_map: dict[str, str] = {}
    if downstream_ids:
        crit_result = await db.execute(
            select(Asset.id, Asset.criticality).where(Asset.id.in_(downstream_ids))
        )
        for row in crit_result:
            val = row.criticality
            criticality_map[str(row.id)] = val.value if hasattr(val, "value") else str(val)

    enriched_downstream = []
    for d in downstream:
        enriched_downstream.append({**d, "criticality": criticality_map.get(d["id"], "unknown")})

    # 4. Downstream risk summary
    risk_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    for d in enriched_downstream:
        crit = d.get("criticality", "unknown")
        risk_counts[crit] = risk_counts.get(crit, 0) + 1

    downstream_risk = {
        "critical": risk_counts.get("critical", 0),
        "high": risk_counts.get("high", 0),
        "total": len(enriched_downstream),
    }

    # 5. Recent CRs touching this asset (substring match on JSON array of UUID strings)
    cr_result = await db.execute(
        select(ChangeRequest)
        .where(
            ChangeRequest.organization_id == user.organization_id,
            cast(ChangeRequest.target_asset_ids, Text).contains(str(asset_id)),
        )
        .order_by(ChangeRequest.created_at.desc())
        .limit(5)
    )
    recent_crs_raw = cr_result.scalars().all()
    recent_crs = [
        {
            "id": str(cr.id),
            "title": cr.title,
            "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
        }
        for cr in recent_crs_raw
    ]

    return {
        "asset": {
            "id": str(asset.id),
            "name": asset.name,
            "asset_type": asset.asset_type.value if hasattr(asset.asset_type, "value") else str(asset.asset_type),
            "environment": asset.environment.value if hasattr(asset.environment, "value") else str(asset.environment),
            "criticality": asset.criticality.value if hasattr(asset.criticality, "value") else str(asset.criticality),
            "owner": getattr(asset, "owner", None),
            "why_exists": getattr(asset, "why_exists", None),
        },
        "upstream": upstream,
        "downstream": enriched_downstream,
        "downstream_risk": downstream_risk,
        "recent_crs": recent_crs,
        "open_findings_count": 0,
    }
```

### 1c — Register router in main.py

- [ ] In `backend/app/main.py`, add after the `infrastructure_memory_router` import line:

```python
from app.routers.impact_simulation import router as impact_simulation_router
```

- [ ] Add after the `app.include_router(infrastructure_memory_router)` line:

```python
app.include_router(impact_simulation_router)
```

### 1d — Run tests

- [ ] From EC2 (ssh `ec2-user@100.101.186.39`), run inside the backend container:
```bash
docker exec nexplane-backend-1 python -m pytest backend/tests/test_impact_simulation.py -v
```
All 5 tests must pass before proceeding.

- [ ] Commit:
```bash
git add backend/app/routers/impact_simulation.py backend/app/main.py backend/tests/test_impact_simulation.py
git commit -m "feat: add GET /impact-simulation blast-radius endpoint"
```

---

## Task 2 — Frontend: Impact Simulation Page

- [ ] Replace `frontend/src/pages/ImpactSimulationPage.tsx` with the following:

```tsx
import { useState, useEffect, useRef } from "react";
import { Zap, Search, AlertTriangle, ArrowUpRight, ArrowDownLeft, GitCommit } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import clsx from "clsx";

// ── types ────────────────────────────────────────────────────────────────────

interface AssetSummary {
  id: string;
  name: string;
  asset_type: string;
  environment: string;
  criticality: string;
}

interface BlastNode {
  id: string;
  name: string;
  asset_type: string;
  relationship_type: string;
  depth: number;
  criticality?: string;
}

interface RecentCR {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

interface BlastRadius {
  asset: AssetSummary & { owner: string | null; why_exists: string | null };
  upstream: BlastNode[];
  downstream: BlastNode[];
  downstream_risk: { critical: number; high: number; total: number };
  recent_crs: RecentCR[];
  open_findings_count: number;
}

// ── helpers ──────────────────────────────────────────────────────────────────

const CRIT_COLORS: Record<string, string> = {
  critical: "text-red-400 bg-red-500/10 border-red-500/30",
  high: "text-orange-400 bg-orange-500/10 border-orange-500/30",
  medium: "text-yellow-400 bg-yellow-500/10 border-yellow-500/30",
  low: "text-slate-400 bg-slate-500/10 border-slate-500/30",
};

function CritBadge({ value }: { value: string }) {
  return (
    <span
      className={clsx(
        "text-[10px] font-semibold uppercase tracking-wide border rounded px-1.5 py-0.5 leading-none",
        CRIT_COLORS[value] ?? "text-slate-400 bg-slate-500/10 border-slate-500/30"
      )}
    >
      {value}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "text-green-400",
    approved: "text-blue-400",
    draft: "text-slate-400",
    failed: "text-red-400",
    executing: "text-amber-400",
  };
  return (
    <span className={clsx("text-xs", colors[status] ?? "text-slate-400")}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ── sub-components ────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="p-4 bg-brand-500/10 rounded-full mb-4">
        <Zap className="w-8 h-8 text-brand-400" />
      </div>
      <h2 className="text-lg font-semibold text-white mb-2">Select an asset to see its blast radius</h2>
      <p className="text-slate-400 text-sm max-w-sm">
        Search for any asset above. The platform will traverse the asset graph to show downstream
        dependents, upstream dependencies, and recent changes.
      </p>
    </div>
  );
}

function NodeList({ nodes, label, icon: Icon }: { nodes: BlastNode[]; label: string; icon: React.ElementType }) {
  if (nodes.length === 0) {
    return (
      <div className="bg-navy-light border border-navy-border rounded-lg p-5">
        <div className="flex items-center gap-2 mb-3">
          <Icon className="w-4 h-4 text-slate-500" />
          <h3 className="text-sm font-semibold text-slate-300">{label}</h3>
        </div>
        <p className="text-xs text-slate-500">None found</p>
      </div>
    );
  }

  // Group downstream by criticality for better scanning
  return (
    <div className="bg-navy-light border border-navy-border rounded-lg p-5">
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-slate-400" />
        <h3 className="text-sm font-semibold text-slate-300">{label}</h3>
        <span className="ml-auto text-xs text-slate-500">{nodes.length} asset{nodes.length !== 1 ? "s" : ""}</span>
      </div>
      <div className="space-y-2">
        {nodes.map((node) => (
          <div key={node.id} className="flex items-center gap-2 text-sm">
            <span className="text-slate-500 text-xs w-4 text-center">{node.depth}</span>
            <span className="text-white flex-1 truncate">{node.name}</span>
            <span className="text-slate-500 text-xs">{node.asset_type}</span>
            {node.criticality && <CritBadge value={node.criticality} />}
          </div>
        ))}
      </div>
    </div>
  );
}

function RecentCRs({ crs }: { crs: RecentCR[] }) {
  return (
    <div className="bg-navy-light border border-navy-border rounded-lg p-5">
      <div className="flex items-center gap-2 mb-3">
        <GitCommit className="w-4 h-4 text-slate-400" />
        <h3 className="text-sm font-semibold text-slate-300">Recent Changes</h3>
      </div>
      {crs.length === 0 ? (
        <p className="text-xs text-slate-500">No recent change requests</p>
      ) : (
        <div className="space-y-2">
          {crs.map((cr) => (
            <div key={cr.id} className="flex items-start gap-2 text-sm">
              <div className="flex-1 min-w-0">
                <p className="text-white truncate">{cr.title}</p>
                <p className="text-slate-500 text-xs">{new Date(cr.created_at).toLocaleDateString()}</p>
              </div>
              <StatusBadge status={cr.status} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RiskBanner({ risk }: { risk: BlastRadius["downstream_risk"] }) {
  if (risk.total === 0) return null;
  const parts: string[] = [];
  if (risk.critical > 0) parts.push(`${risk.critical} critical`);
  if (risk.high > 0) parts.push(`${risk.high} high`);

  const severity = risk.critical > 0 ? "red" : risk.high > 0 ? "orange" : "slate";
  const colors = {
    red: "bg-red-500/10 border-red-500/30 text-red-300",
    orange: "bg-orange-500/10 border-orange-500/30 text-orange-300",
    slate: "bg-slate-500/10 border-slate-500/30 text-slate-300",
  };

  return (
    <div className={clsx("flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm", colors[severity])}>
      <AlertTriangle className="w-4 h-4 shrink-0" />
      <span>
        {parts.length > 0
          ? `${parts.join(", ")} asset${risk.total !== 1 ? "s" : ""} downstream`
          : `${risk.total} assets downstream`}
        {" — changes to this asset may cascade"}
      </span>
    </div>
  );
}

// ── asset search combobox ─────────────────────────────────────────────────────

interface AssetOption {
  id: string;
  name: string;
  asset_type: string;
  environment: string;
  criticality: string;
}

function AssetSearchBox({ onSelect }: { onSelect: (asset: AssetOption) => void }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [debouncedQ, setDebouncedQ] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(query), 300);
    return () => clearTimeout(t);
  }, [query]);

  const { data: results = [] } = useQuery<AssetOption[]>({
    queryKey: ["asset-search", debouncedQ],
    queryFn: () =>
      debouncedQ.length >= 1
        ? apiClient.get(`/assets?q=${encodeURIComponent(debouncedQ)}`).then((r) => r.data.slice(0, 8))
        : Promise.resolve([]),
    enabled: debouncedQ.length >= 1,
  });

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div ref={containerRef} className="relative w-full max-w-xl">
      <div className="flex items-center gap-2 bg-navy-light border border-navy-border rounded-lg px-3 py-2.5 focus-within:border-brand-500 transition-colors">
        <Search className="w-4 h-4 text-slate-500 shrink-0" />
        <input
          className="flex-1 bg-transparent text-white placeholder-slate-500 text-sm outline-none"
          placeholder="Search assets by name…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
        />
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-50 top-full mt-1 w-full bg-navy-light border border-navy-border rounded-lg shadow-xl overflow-hidden">
          {results.map((asset) => (
            <button
              key={asset.id}
              className="w-full flex items-center gap-3 px-3 py-2.5 text-sm hover:bg-navy-border/40 transition-colors text-left"
              onMouseDown={(e) => {
                e.preventDefault();
                onSelect(asset);
                setQuery(asset.name);
                setOpen(false);
              }}
            >
              <span className="text-white flex-1 truncate">{asset.name}</span>
              <span className="text-slate-500 text-xs">{asset.asset_type}</span>
              <CritBadge value={asset.criticality} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── page ─────────────────────────────────────────────────────────────────────

export function ImpactSimulationPage() {
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  const { data: blastRadius, isLoading } = useQuery<BlastRadius>({
    queryKey: ["impact-simulation", selectedAssetId],
    queryFn: () =>
      apiClient.get(`/impact-simulation?asset_id=${selectedAssetId}`).then((r) => r.data),
    enabled: !!selectedAssetId,
  });

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">Impact Simulation</h1>
        <p className="text-slate-400 text-sm">What will happen if I change it?</p>
      </div>

      {/* Search */}
      <div className="mb-8">
        <AssetSearchBox onSelect={(a) => setSelectedAssetId(a.id)} />
      </div>

      {/* Results */}
      {!selectedAssetId && <EmptyState />}

      {selectedAssetId && isLoading && (
        <div className="flex items-center justify-center py-24">
          <div className="text-slate-400 text-sm">Analysing blast radius…</div>
        </div>
      )}

      {blastRadius && (
        <div className="space-y-6">
          {/* Asset summary */}
          <div className="bg-navy-light border border-navy-border rounded-lg p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-white">{blastRadius.asset.name}</h2>
                <div className="flex items-center gap-2 mt-1 text-sm text-slate-400">
                  <span>{blastRadius.asset.asset_type}</span>
                  <span>·</span>
                  <span>{blastRadius.asset.environment}</span>
                  {blastRadius.asset.owner && (
                    <>
                      <span>·</span>
                      <span>Owner: {blastRadius.asset.owner}</span>
                    </>
                  )}
                </div>
                {blastRadius.asset.why_exists && (
                  <p className="text-sm text-slate-400 mt-2 italic">"{blastRadius.asset.why_exists}"</p>
                )}
              </div>
              <CritBadge value={blastRadius.asset.criticality} />
            </div>
          </div>

          {/* Risk banner */}
          <RiskBanner risk={blastRadius.downstream_risk} />

          {/* Three-column cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <NodeList
              nodes={blastRadius.downstream}
              label="Downstream Impact"
              icon={ArrowDownLeft}
            />
            <NodeList
              nodes={blastRadius.upstream}
              label="Upstream Dependencies"
              icon={ArrowUpRight}
            />
            <RecentCRs crs={blastRadius.recent_crs} />
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] Restart the frontend container (required after any frontend/src change):
```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] Commit:
```bash
git add frontend/src/pages/ImpactSimulationPage.tsx
git commit -m "feat: replace ImpactSimulationPage stub with functional blast-radius UI"
```

---

## Task 3 — Sidebar: Lifecycle Nav Reorganization

- [ ] Replace `frontend/src/components/Sidebar.tsx` with the following full file. Key changes from the current file:
  - New nav item groupings replacing `topNavItems`, `operationsNavItems`, `complianceNavItems`, `previewNavItems`
  - New icons imported: `Search`, `GitBranch`, `Play`, `Eye`, `RotateCcw`
  - Seven collapsible sections replacing three
  - Impact Simulation moves to Observe (no Preview badge)
  - Infrastructure Memory moves to Discover (no Preview badge)
  - Recommendations stays in Preview section (keeps Preview badge)

```tsx
import { useState } from "react";
import { NavLink } from "react-router-dom";
import clsx from "clsx";
import {
  LayoutDashboard,
  FileStack,
  Plug,
  Server,
  FolderOpen,
  Settings as SettingsIcon,
  ShieldCheck,
  BookOpen,
  ClipboardList,
  HardDrive,
  Clock,
  CalendarClock,
  Bell,
  ChevronDown,
  ChevronRight,
  ShieldAlert,
  Zap,
  Lightbulb,
  Brain,
  Lock,
  Search,
  GitBranch,
  Play,
  Eye,
  RotateCcw,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { DemoOrgSwitcher } from './DemoOrgSwitcher';

// ── nav item definitions ─────────────────────────────────────────────────────

const discoverNavItems = [
  { to: "/assets", label: "Assets", icon: Server },
  { to: "/infrastructure-memory", label: "Infrastructure Memory", icon: Brain },
];

const planNavItems = [
  { to: "/change-requests", label: "Change Requests", icon: FileStack },
  { to: "/projects", label: "Projects", icon: FolderOpen },
];

const executeNavItems = [
  { to: "/connectors", label: "Connectors", icon: Plug },
  { to: "/runbooks", label: "Runbooks", icon: BookOpen },
  { to: "/scheduled-operations", label: "Scheduled Ops", icon: CalendarClock },
];

const observeNavItems = [
  { to: "/maintenance-windows", label: "Maintenance Windows", icon: Clock },
  { to: "/impact-simulation", label: "Impact Simulation", icon: Zap },
];

const recoverNavItems = [
  { to: "/backup-recovery", label: "Backup & Recovery", icon: HardDrive },
];

const complianceNavItems = [
  { to: "/remediation", label: "Findings & Remediation", icon: ShieldAlert },
  { to: "/access-reviews", label: "Access Reviews", icon: ShieldCheck },
  { to: "/compliance", label: "Compliance", icon: ClipboardList },
];

const previewNavItems = [
  { to: "/recommendations", label: "Recommendations", icon: Lightbulb },
];

const bottomNavItems = [
  { to: "/notifications", label: "Notifications", icon: Bell },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

// ── components ───────────────────────────────────────────────────────────────

function NavItem({
  to,
  label,
  icon: Icon,
  exact,
  badge,
  preview,
}: {
  to: string;
  label: string;
  icon: React.ElementType;
  exact?: boolean;
  badge?: number;
  preview?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={exact}
      className={({ isActive }) =>
        clsx(
          "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
          isActive
            ? "bg-brand-600 text-white"
            : "text-slate-400 hover:text-white hover:bg-navy-light"
        )
      }
    >
      <div className="relative flex-shrink-0">
        <Icon className="w-4 h-4" />
        {badge != null && badge > 0 && (
          <span className="absolute -top-1.5 -right-1.5 bg-amber-500 text-white text-[10px] font-bold rounded-full min-w-[14px] h-[14px] flex items-center justify-center px-0.5 leading-none">
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </div>
      <span className="flex-1">{label}</span>
      {preview && (
        <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-1 py-0.5 leading-none">
          Preview
        </span>
      )}
    </NavLink>
  );
}

function SectionHeader({
  icon: Icon,
  label,
  open,
  onToggle,
}: {
  icon: React.ElementType;
  label: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      aria-expanded={open}
      onClick={onToggle}
      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wide hover:text-slate-300 transition-colors"
    >
      <Icon className="w-3 h-3" />
      <span>{label}</span>
      {open ? (
        <ChevronDown className="w-3 h-3 ml-auto" />
      ) : (
        <ChevronRight className="w-3 h-3 ml-auto" />
      )}
    </button>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export function Sidebar() {
  const { user, logout } = useAuth();
  const [discoverOpen, setDiscoverOpen] = useState(true);
  const [planOpen, setPlanOpen] = useState(true);
  const [executeOpen, setExecuteOpen] = useState(true);
  const [observeOpen, setObserveOpen] = useState(true);
  const [recoverOpen, setRecoverOpen] = useState(true);
  const [complianceOpen, setComplianceOpen] = useState(true);
  const [previewOpen, setPreviewOpen] = useState(true);

  const { data: unreadNotifications = [] } = useQuery<{ id: string; read: boolean }[]>({
    queryKey: ["notifications", "unread"],
    queryFn: () => apiClient.get("/notifications?unread_only=true&limit=50").then(r => r.data),
    refetchInterval: 30_000,
    enabled: !!user,
  });

  const { data: pendingApprovals = [] } = useQuery<{ id: string }[]>({
    queryKey: ["change-requests", "pending-approvals"],
    queryFn: () =>
      apiClient.get("/change-requests?status=awaiting_approval&limit=100").then(r =>
        Array.isArray(r.data) ? r.data : (r.data?.items ?? [])
      ),
    refetchInterval: 60_000,
    enabled: !!user,
  });

  const unreadCount = unreadNotifications.length;
  const pendingCount = pendingApprovals.length;

  const { data: checklist } = useQuery<{
    steps: { id: string; label: string; complete: boolean; detail: string }[];
    connector_count: number;
    asset_count: number;
  }>({
    queryKey: ["onboarding-checklist"],
    queryFn: () => apiClient.get("/onboarding/checklist").then((r) => r.data),
    staleTime: 60_000,
    enabled: !!user,
  });

  const showOnboarding = checklist != null && checklist.connector_count === 0;
  const incompleteSteps = checklist?.steps?.filter((s) => !s.complete) ?? [];

  return (
    <aside className="fixed inset-y-0 left-0 w-60 bg-navy flex flex-col z-10">
      {/* Logo */}
      <div className="flex items-center px-5 py-4 border-b border-navy-border">
        <img src="/title_white.png" alt="Nexplane" className="h-8 w-auto" />
      </div>

      {/* Onboarding checklist */}
      {showOnboarding && incompleteSteps.length > 0 && (
        <div className="px-3 py-3 border-b border-navy-border bg-indigo-900/30">
          <p className="text-xs font-semibold text-indigo-300 mb-2 uppercase tracking-wide">Get started</p>
          <div className="space-y-1.5">
            {incompleteSteps.slice(0, 3).map((step) => (
              <div key={step.id} className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-1.5 shrink-0" />
                <span className="text-xs text-indigo-200 leading-tight">{step.label}</span>
              </div>
            ))}
          </div>
          <NavLink
            to="/connectors"
            className="mt-2 block text-xs font-medium text-indigo-300 hover:text-white transition-colors"
          >
            Configure connectors →
          </NavLink>
        </div>
      )}

      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {/* Dashboard — always visible, no section */}
        <NavItem to="/" label="Dashboard" icon={LayoutDashboard} exact />

        {/* Discover */}
        <div className="pt-2">
          <SectionHeader
            icon={Search}
            label="Discover"
            open={discoverOpen}
            onToggle={() => setDiscoverOpen((v) => !v)}
          />
          {discoverOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {discoverNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Plan & Approve */}
        <div className="pt-2">
          <SectionHeader
            icon={GitBranch}
            label="Plan & Approve"
            open={planOpen}
            onToggle={() => setPlanOpen((v) => !v)}
          />
          {planOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {planNavItems.map(({ to, label, icon }) => (
                <NavItem
                  key={to}
                  to={to}
                  label={label}
                  icon={icon}
                  badge={to === "/change-requests" && pendingCount > 0 ? pendingCount : undefined}
                />
              ))}
            </div>
          )}
        </div>

        {/* Execute */}
        <div className="pt-2">
          <SectionHeader
            icon={Play}
            label="Execute"
            open={executeOpen}
            onToggle={() => setExecuteOpen((v) => !v)}
          />
          {executeOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {executeNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Observe */}
        <div className="pt-2">
          <SectionHeader
            icon={Eye}
            label="Observe"
            open={observeOpen}
            onToggle={() => setObserveOpen((v) => !v)}
          />
          {observeOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {observeNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Recover */}
        <div className="pt-2">
          <SectionHeader
            icon={RotateCcw}
            label="Recover"
            open={recoverOpen}
            onToggle={() => setRecoverOpen((v) => !v)}
          />
          {recoverOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {recoverNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Compliance & Identity */}
        <div className="pt-2">
          <SectionHeader
            icon={Lock}
            label="Compliance & Identity"
            open={complianceOpen}
            onToggle={() => setComplianceOpen((v) => !v)}
          />
          {complianceOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {complianceNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Preview — upcoming capabilities */}
        <div className="pt-2">
          <SectionHeader
            icon={Zap}
            label="Preview"
            open={previewOpen}
            onToggle={() => setPreviewOpen((v) => !v)}
          />
          {previewOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {previewNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} preview />
              ))}
            </div>
          )}
        </div>

        <DemoOrgSwitcher />

        {/* Bottom strip */}
        <div className="pt-2 border-t border-navy-border mt-2 space-y-0.5">
          {bottomNavItems.map(({ to, label, icon }) => (
            <NavItem
              key={to}
              to={to}
              label={label}
              icon={icon}
              badge={to === "/notifications" && unreadCount > 0 ? unreadCount : undefined}
            />
          ))}
        </div>
      </nav>

      {/* User footer */}
      <div className="px-4 py-4 border-t border-navy-border">
        {user && (
          <div className="mb-3">
            <div className="text-slate-300 text-sm font-medium truncate">{user.name}</div>
            <div className="text-slate-500 text-xs truncate">{user.email}</div>
            <div className="text-slate-600 text-xs mt-0.5 capitalize">{user.role.replace("_", " ")}</div>
          </div>
        )}
        <button
          onClick={logout}
          className="text-slate-500 hover:text-slate-300 text-xs transition-colors"
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}
```

- [ ] Restart frontend container:
```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] Commit:
```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat: reorganize sidebar nav into lifecycle sections (Discover/Plan/Execute/Observe/Recover)"
```

---

## Task 4 — Verification

- [ ] SSH to EC2 (`ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`) and confirm backend tests still pass:
```bash
docker exec nexplane-backend-1 python -m pytest backend/tests/test_impact_simulation.py -v
```

- [ ] Open the app via Tailscale and verify:
  1. Sidebar shows 7 collapsible sections (Discover, Plan & Approve, Execute, Observe, Recover, Compliance & Identity, Preview)
  2. "Impact Simulation" appears under Observe with no Preview badge
  3. "Infrastructure Memory" appears under Discover with no Preview badge
  4. "Recommendations" appears under Preview with the amber Preview badge
  5. Navigate to `/impact-simulation` — empty state with Zap icon and search box
  6. Type an asset name — dropdown appears with results
  7. Select an asset — blast-radius cards render (downstream, upstream, recent CRs)
  8. Select an asset with no relationships — cards show "None found" gracefully

---

## Commit Summary

| Commit | Files |
|--------|-------|
| `feat: add GET /impact-simulation blast-radius endpoint` | `backend/app/routers/impact_simulation.py`, `backend/app/main.py`, `backend/tests/test_impact_simulation.py` |
| `feat: replace ImpactSimulationPage stub with functional blast-radius UI` | `frontend/src/pages/ImpactSimulationPage.tsx` |
| `feat: reorganize sidebar nav into lifecycle sections` | `frontend/src/components/Sidebar.tsx` |
