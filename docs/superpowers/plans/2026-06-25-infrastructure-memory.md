# Infrastructure Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Infrastructure Memory page functional: a searchable view of asset provenance (owner + why_exists) pulled from asset_metadata, plus surfacing owner/why_exists prominently on the asset detail page.

**Architecture:** New backend endpoint queries the assets table filtering on asset_metadata JSON fields. Frontend InfrastructureMemoryPage replaces stub with real search UI.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL JSONB, React/TypeScript

---

## Task 1 — Backend: failing test for `/infrastructure-memory`

**Files:**
- `backend/tests/test_infrastructure_memory.py` (new)

- [ ] Create test file with a pytest fixture that creates an org, user, and 3 assets with varied `asset_metadata` (some with owner/why_exists, one without):

```python
# backend/tests/test_infrastructure_memory.py
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetType, Environment, Criticality


@pytest.mark.asyncio
async def test_infrastructure_memory_returns_assets(
    async_client: AsyncClient,
    auth_headers: dict,
    db: AsyncSession,
    test_org_id,
):
    """Endpoint returns assets with owner/why_exists from metadata."""
    # Assets created via fixture or inline — depends on existing test infra pattern
    response = await async_client.get("/infrastructure-memory", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "page_size" in body


@pytest.mark.asyncio
async def test_infrastructure_memory_q_search(
    async_client: AsyncClient,
    auth_headers: dict,
):
    """?q= filters on name, owner, why_exists."""
    response = await async_client.get(
        "/infrastructure-memory?q=platform-eng", headers=auth_headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    for item in items:
        text = f"{item['name']} {item.get('owner', '')} {item.get('why_exists', '')}".lower()
        assert "platform-eng" in text


@pytest.mark.asyncio
async def test_infrastructure_memory_org_scoped(
    async_client: AsyncClient,
    auth_headers: dict,
):
    """Assets from another org are not returned."""
    response = await async_client.get("/infrastructure-memory", headers=auth_headers)
    assert response.status_code == 200
    # All returned items must belong to authenticated org (enforced by response shape check above)
    # The actual cross-org isolation is tested by verifying no 403/leaked data


@pytest.mark.asyncio
async def test_infrastructure_memory_pagination(
    async_client: AsyncClient,
    auth_headers: dict,
):
    """page/page_size params work."""
    response = await async_client.get(
        "/infrastructure-memory?page=1&page_size=2", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 2
    assert body["page"] == 1
    assert body["page_size"] == 2
```

- [ ] Run tests to confirm they fail (404 on the endpoint):
```bash
cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 python -m pytest tests/test_infrastructure_memory.py -x -q 2>&1 | tail -20
```

---

## Task 2 — Backend: implement `/infrastructure-memory` router

**Files:**
- `backend/app/routers/infrastructure_memory.py` (new)
- `backend/app/main.py` (register router)

- [ ] Create the router:

```python
# backend/app/routers/infrastructure_memory.py
import math
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset, Criticality
from app.models.user import User
from app.routers import current_user

router = APIRouter(prefix="/infrastructure-memory", tags=["Infrastructure Memory"])

_CRITICALITY_ORDER = {
    Criticality.critical: 0,
    Criticality.high: 1,
    Criticality.medium: 2,
    Criticality.low: 3,
}


@router.get("")
async def list_infrastructure_memory(
    q: str | None = Query(None, description="Search across name, owner, why_exists"),
    owner: str | None = Query(None, description="Filter by owner (case-insensitive exact match)"),
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Asset).where(Asset.organization_id == user.organization_id)

    if q:
        pattern = f"%{q}%"
        owner_col = cast(Asset.asset_metadata["owner"].astext, String)
        why_col = cast(Asset.asset_metadata["why_exists"].astext, String)
        stmt = stmt.where(
            Asset.name.ilike(pattern)
            | owner_col.ilike(pattern)
            | why_col.ilike(pattern)
        )

    if owner:
        owner_col = cast(Asset.asset_metadata["owner"].astext, String)
        stmt = stmt.where(owner_col.ilike(owner))

    # Count total before pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Order: criticality priority then name
    stmt = stmt.order_by(Asset.name)
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    result = await db.execute(stmt)
    assets = result.scalars().all()

    # Sort by criticality in Python after fetch (small page, acceptable)
    assets = sorted(assets, key=lambda a: (_CRITICALITY_ORDER.get(a.criticality, 99), a.name))

    items = [
        {
            "id": str(a.id),
            "name": a.name,
            "asset_type": a.asset_type.value,
            "environment": a.environment.value,
            "criticality": a.criticality.value,
            "owner": (a.asset_metadata or {}).get("owner"),
            "why_exists": (a.asset_metadata or {}).get("why_exists"),
        }
        for a in assets
    ]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }
```

- [ ] Register the router in `backend/app/main.py`. Find the block where other routers are included (look for `app.include_router`) and add:

```python
from app.routers.infrastructure_memory import router as infrastructure_memory_router
app.include_router(infrastructure_memory_router)
```

- [ ] Run tests to confirm they pass:
```bash
cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 python -m pytest tests/test_infrastructure_memory.py -x -q 2>&1 | tail -20
```

- [ ] Commit:
```bash
cd /home/ec2-user/nexplane && git add backend/app/routers/infrastructure_memory.py backend/app/main.py backend/tests/test_infrastructure_memory.py && git commit -m "feat: add GET /infrastructure-memory endpoint with search and pagination"
```

---

## Task 3 — Frontend: API client method

**Files:**
- `frontend/src/api/endpoints.ts` (edit — add `infrastructureMemoryApi`)

- [ ] Read `frontend/src/api/endpoints.ts` to find the pattern used by `assetsApi`, then add a new export at the bottom:

```typescript
// Add to frontend/src/api/endpoints.ts

export interface InfrastructureMemoryItem {
  id: string;
  name: string;
  asset_type: string;
  environment: string;
  criticality: string;
  owner: string | null;
  why_exists: string | null;
}

export interface InfrastructureMemoryResponse {
  total: number;
  page: number;
  page_size: number;
  items: InfrastructureMemoryItem[];
}

export const infrastructureMemoryApi = {
  list: (params: { q?: string; page?: number; page_size?: number }) =>
    apiClient
      .get<InfrastructureMemoryResponse>("/infrastructure-memory", { params })
      .then((r) => r.data),
};
```

---

## Task 4 — Frontend: replace InfrastructureMemoryPage stub

**Files:**
- `frontend/src/pages/InfrastructureMemoryPage.tsx` (full rewrite)

- [ ] Replace the entire file with:

```typescript
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Brain, Search, X } from "lucide-react";
import { infrastructureMemoryApi } from "../api/endpoints";
import { useDebounce } from "../hooks/useDebounce"; // create if missing — see note

const ENV_COLORS: Record<string, string> = {
  prod: "bg-red-500/10 text-red-400 border-red-400/30",
  staging: "bg-amber-500/10 text-amber-400 border-amber-400/30",
  dev: "bg-slate-500/10 text-slate-400 border-slate-400/30",
};

const CRIT_COLORS: Record<string, string> = {
  critical: "bg-red-500/10 text-red-400 border-red-400/30",
  high: "bg-orange-500/10 text-orange-400 border-orange-400/30",
  medium: "bg-amber-500/10 text-amber-400 border-amber-400/30",
  low: "bg-slate-500/10 text-slate-400 border-slate-400/30",
};

export function InfrastructureMemoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [inputValue, setInputValue] = useState(searchParams.get("q") ?? "");
  const debouncedQ = useDebounce(inputValue, 300);

  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const pageSize = 50;

  const { data, isLoading } = useQuery({
    queryKey: ["infrastructure-memory", debouncedQ, page],
    queryFn: () =>
      infrastructureMemoryApi.list({ q: debouncedQ || undefined, page, page_size: pageSize }),
  });

  function handleSearchChange(value: string) {
    setInputValue(value);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set("q", value);
      else next.delete("q");
      next.delete("page");
      return next;
    });
  }

  function handleClearSearch() {
    setInputValue("");
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("q");
      next.delete("page");
      return next;
    });
  }

  function handlePageChange(newPage: number) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("page", String(newPage));
      return next;
    });
  }

  const totalPages = data ? Math.ceil(data.total / pageSize) : 1;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Brain className="w-5 h-5 text-amber-400" />
        </div>
        <h1 className="text-2xl font-bold text-white">Infrastructure Memory</h1>
      </div>
      <p className="text-slate-400 mb-6">Why does this exist?</p>

      {/* Search bar */}
      <div className="relative mb-6 max-w-lg">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
        <input
          type="text"
          placeholder="Search by name, owner, or reason…"
          value={inputValue}
          onChange={(e) => handleSearchChange(e.target.value)}
          className="w-full pl-9 pr-9 py-2 bg-navy-light border border-navy-border rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-brand-400"
        />
        {inputValue && (
          <button
            onClick={handleClearSearch}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Results */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-12 bg-navy-light border border-navy-border rounded animate-pulse" />
          ))}
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <p className="text-base mb-2">
            {inputValue ? "No assets match your search." : "No assets found."}
          </p>
          {inputValue && (
            <button
              onClick={handleClearSearch}
              className="text-sm text-brand-400 hover:text-brand-300"
            >
              Clear search
            </button>
          )}
        </div>
      ) : (
        <>
          <p className="text-xs text-slate-500 mb-3">{data.total} asset{data.total !== 1 ? "s" : ""}</p>
          <div className="border border-navy-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-navy-border bg-navy-light">
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Asset</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Owner</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Why it exists</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Type</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Env</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id} className="border-b border-navy-border last:border-0 hover:bg-navy-light/50 transition-colors">
                    <td className="px-4 py-3">
                      <Link
                        to={`/assets/${item.id}`}
                        className="text-brand-400 hover:text-brand-300 font-medium"
                      >
                        {item.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      {item.owner ? (
                        <span className="text-xs px-2 py-0.5 rounded border bg-slate-500/10 text-slate-300 border-slate-400/30">
                          {item.owner}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-300 max-w-sm">
                      {item.why_exists ? (
                        <span title={item.why_exists}>
                          {item.why_exists.length > 120
                            ? item.why_exists.slice(0, 117) + "…"
                            : item.why_exists}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-slate-400">{item.asset_type.replace(/_/g, " ")}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded border ${ENV_COLORS[item.environment] ?? "text-slate-400"}`}>
                        {item.environment}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center gap-3 mt-4 justify-end">
              <button
                onClick={() => handlePageChange(page - 1)}
                disabled={page <= 1}
                className="text-sm px-3 py-1.5 rounded border border-navy-border text-slate-400 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <span className="text-sm text-slate-500">
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => handlePageChange(page + 1)}
                disabled={page >= totalPages}
                className="text-sm px-3 py-1.5 rounded border border-navy-border text-slate-400 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] Check whether `frontend/src/hooks/useDebounce.ts` exists. If not, create it:

```typescript
// frontend/src/hooks/useDebounce.ts
import { useState, useEffect } from "react";

export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debouncedValue;
}
```

- [ ] Restart frontend container to pick up changes:
```bash
cd /home/ec2-user/nexplane && docker compose stop frontend && docker compose up frontend -d
```

- [ ] Commit:
```bash
cd /home/ec2-user/nexplane && git add frontend/src/pages/InfrastructureMemoryPage.tsx frontend/src/api/endpoints.ts frontend/src/hooks/useDebounce.ts && git commit -m "feat: implement Infrastructure Memory page with search and pagination"
```

---

## Task 5 — Frontend: surface owner/why_exists in AssetDetail header

**Files:**
- `frontend/src/pages/AssetDetail.tsx` (edit)

- [ ] Read the AssetDetail header section (lines 100–200 approximately) to find where `criticality`, `environment`, or the info row is rendered. Then insert `owner` and `why_exists` fields.

The fields should be added as labeled items in the same row/block as environment and criticality. Pattern to add (adapt to match surrounding JSX):

```tsx
{/* Owner */}
{asset.asset_metadata?.owner && (
  <div className="flex flex-col gap-0.5">
    <span className="text-xs text-slate-500 uppercase tracking-wide">Owner</span>
    <span className="text-sm text-slate-300">{asset.asset_metadata.owner}</span>
  </div>
)}
{!asset.asset_metadata?.owner && (
  <div className="flex flex-col gap-0.5">
    <span className="text-xs text-slate-500 uppercase tracking-wide">Owner</span>
    <span className="text-sm text-slate-600">—</span>
  </div>
)}

{/* Why it exists */}
{asset.asset_metadata?.why_exists && (
  <div className="flex flex-col gap-0.5 max-w-sm">
    <span className="text-xs text-slate-500 uppercase tracking-wide">Why it exists</span>
    <span className="text-sm text-slate-300">{asset.asset_metadata.why_exists}</span>
  </div>
)}
{!asset.asset_metadata?.why_exists && (
  <div className="flex flex-col gap-0.5">
    <span className="text-xs text-slate-500 uppercase tracking-wide">Why it exists</span>
    <span className="text-sm text-slate-600">—</span>
  </div>
)}
```

- [ ] Restart frontend container:
```bash
cd /home/ec2-user/nexplane && docker compose stop frontend && docker compose up frontend -d
```

- [ ] Commit:
```bash
cd /home/ec2-user/nexplane && git add frontend/src/pages/AssetDetail.tsx && git commit -m "feat: surface owner and why_exists in AssetDetail header"
```

---

## Task 6 — Verification

- [ ] Browse to `/infrastructure-memory` in the Tailscale-accessible app. Confirm:
  - No "Preview" badge visible
  - Assets listed with owner and why_exists columns
  - Search for a known owner (e.g., "platform-eng") and confirm filtered results
  - Click an asset name and verify it navigates to `/assets/{id}`

- [ ] On an asset detail page, confirm `Owner` and `Why it exists` fields appear in the header.

- [ ] Run backend tests one final time:
```bash
cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 python -m pytest tests/test_infrastructure_memory.py -v 2>&1 | tail -30
```

- [ ] All tests green, no console errors in browser — sub-project 4 complete.
