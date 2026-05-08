# Smoke Test Inventory Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Clean Up Inventory" button to the Smoke Tests page that previews and bulk-deletes Nexplane inventory assets left behind by smoke test runs.

**Architecture:** Two new endpoints in the smoke tests router handle preview (`GET /smoke-tests/cleanup-preview`) and bulk deletion (`DELETE /smoke-tests/cleanup-inventory`), both querying assets by the `nexplane-smoke` name pattern. The frontend adds a `CleanupModal` component and a page-level button to `SmokeTests.tsx`, wired to the two new API functions in `smokeTestsApi.ts`.

**Tech Stack:** Python/FastAPI + SQLAlchemy async (backend), React/TypeScript + @tanstack/react-query useMutation (frontend), existing `DELETE /assets/{id}` pattern for reference

---

## File Structure

```
backend/app/routers/smoke_tests.py     MODIFY — add GET /cleanup-preview and DELETE /cleanup-inventory
backend/tests/test_smoke_cleanup.py    NEW    — unit tests for both endpoints
frontend/src/api/smokeTestsApi.ts      MODIFY — add CleanupAsset, getCleanupPreview(), executeCleanup()
frontend/src/pages/SmokeTests.tsx      MODIFY — add CleanupModal component + button + state
```

---

## Task 1: Backend Endpoints

**Files:**
- Modify: `backend/app/routers/smoke_tests.py`
- Create: `backend/tests/test_smoke_cleanup.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_smoke_cleanup.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.routers.smoke_tests import cleanup_preview, cleanup_inventory


def _make_mock_asset(asset_id: str, name: str) -> MagicMock:
    a = MagicMock()
    a.id = asset_id
    a.name = name
    a.asset_type = "server"
    a.created_at = MagicMock()
    a.created_at.isoformat.return_value = "2026-05-08T10:00:00"
    return a


def _make_mock_db(assets: list) -> AsyncMock:
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = assets
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    return db


def _make_mock_user(org_id: str = "org-1") -> MagicMock:
    user = MagicMock()
    user.organization_id = org_id
    return user


def test_cleanup_preview_returns_matching_assets():
    assets = [
        _make_mock_asset("a1", "nexplane-smoke-test-01"),
        _make_mock_asset("a2", "nexplane-smoke-ec2"),
    ]
    db = _make_mock_db(assets)
    user = _make_mock_user()

    result = asyncio.run(cleanup_preview(user=user, db=db))

    assert result["count"] == 2
    names = [a["name"] for a in result["assets"]]
    assert "nexplane-smoke-test-01" in names
    assert "nexplane-smoke-ec2" in names


def test_cleanup_preview_returns_empty_when_no_matches():
    db = _make_mock_db([])
    user = _make_mock_user()

    result = asyncio.run(cleanup_preview(user=user, db=db))

    assert result["count"] == 0
    assert result["assets"] == []


def test_cleanup_inventory_deletes_all_matching_assets():
    assets = [
        _make_mock_asset("a1", "nexplane-smoke-test-01"),
        _make_mock_asset("a2", "nexplane-smoke-gce-01"),
    ]
    db = _make_mock_db(assets)
    user = _make_mock_user()

    result = asyncio.run(cleanup_inventory(user=user, db=db))

    assert result == {"deleted": 2}
    assert db.delete.call_count == 2
    db.commit.assert_called_once()


def test_cleanup_inventory_returns_zero_when_nothing_to_delete():
    db = _make_mock_db([])
    user = _make_mock_user()

    result = asyncio.run(cleanup_inventory(user=user, db=db))

    assert result == {"deleted": 0}
    db.delete.assert_not_called()
    db.commit.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_smoke_cleanup.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'cleanup_preview'`

- [ ] **Step 3: Add imports and endpoints to `backend/app/routers/smoke_tests.py`**

Add these imports after the existing imports block (after line `from app.models.user import User`):

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
```

Add these two endpoints at the end of the file (after the existing `stop_run` endpoint):

```python
@router.get("/cleanup-preview")
async def cleanup_preview(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all org assets whose name contains 'nexplane-smoke' (no deletions)."""
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.name.ilike("%nexplane-smoke%"),
        )
    )
    assets = result.scalars().all()
    return {
        "count": len(assets),
        "assets": [
            {
                "id": str(a.id),
                "name": a.name,
                "asset_type": a.asset_type.value if hasattr(a.asset_type, "value") else str(a.asset_type),
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in assets
        ],
    }


@router.delete("/cleanup-inventory")
async def cleanup_inventory(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-delete all org assets whose name contains 'nexplane-smoke'."""
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.name.ilike("%nexplane-smoke%"),
        )
    )
    assets = result.scalars().all()
    for asset in assets:
        await db.delete(asset)
    await db.commit()
    return {"deleted": len(assets)}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_smoke_cleanup.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Quick smoke test of the live endpoints**

```bash
docker exec nexplane-backend-1 sh -c "python3 -c \"
import requests
r = requests.post('http://localhost:8000/auth/login', json={'email': 'admin@acme.example', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': 'Bearer ' + t}
p = requests.get('http://localhost:8000/smoke-tests/cleanup-preview', headers=h)
print('Preview status:', p.status_code, 'count:', p.json()['count'])
\""
```

Expected: `Preview status: 200 count: <N>`

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/smoke_tests.py backend/tests/test_smoke_cleanup.py
git commit -m "feat(smoke-tests): add cleanup-preview and cleanup-inventory endpoints"
```

---

## Task 2: Frontend API Client

**Files:**
- Modify: `frontend/src/api/smokeTestsApi.ts`

- [ ] **Step 1: Add types and API functions to `frontend/src/api/smokeTestsApi.ts`**

Add these after the existing `RunConfig` interface and before `export const smokeTestsApi`:

```typescript
export interface CleanupAsset {
  id: string;
  name: string;
  asset_type: string;
  created_at: string | null;
}

export interface CleanupPreview {
  assets: CleanupAsset[];
  count: number;
}
```

Add these two entries inside the `smokeTestsApi` object (after `stopRun`):

```typescript
  getCleanupPreview: (): Promise<CleanupPreview> =>
    apiClient.get<CleanupPreview>("/smoke-tests/cleanup-preview").then((r) => r.data),

  executeCleanup: (): Promise<{ deleted: number }> =>
    apiClient.delete<{ deleted: number }>("/smoke-tests/cleanup-inventory").then((r) => r.data),
```

- [ ] **Step 2: Verify TypeScript compiles cleanly**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep smokeTests | head -10"
```

Expected: no errors referencing `smokeTestsApi.ts`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/smokeTestsApi.ts
git commit -m "feat(smoke-tests): add getCleanupPreview and executeCleanup API functions"
```

---

## Task 3: CleanupModal Component + Button

**Files:**
- Modify: `frontend/src/pages/SmokeTests.tsx`

- [ ] **Step 1: Add the `CleanupModal` component and wire up button**

The page currently imports from `smokeTestsApi`:
```typescript
import { smokeTestsApi, SmokeTestSuite, RunConfig } from "../api/smokeTestsApi";
```

Update that import to also include the new types:
```typescript
import { smokeTestsApi, SmokeTestSuite, RunConfig, CleanupAsset } from "../api/smokeTestsApi";
```

Add the `CleanupModal` component function before the `SuiteCard` function (i.e., before line starting `function SuiteCard`):

```typescript
// ---------------------------------------------------------------------------
// Cleanup Modal
// ---------------------------------------------------------------------------

function CleanupModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();

  const { data: preview, isLoading: isLoadingPreview, isError: isPreviewError } = useQuery({
    queryKey: ["smoke-cleanup-preview"],
    queryFn: smokeTestsApi.getCleanupPreview,
  });

  const deleteMutation = useMutation({
    mutationFn: smokeTestsApi.executeCleanup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["smoke-cleanup-preview"] });
    },
  });

  const isEmpty = !isLoadingPreview && !isPreviewError && preview?.count === 0;
  const isDone = deleteMutation.isSuccess;

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-navy-light border border-navy-border rounded-xl w-full max-w-lg mx-4 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-white font-semibold text-base">Clean Up Inventory</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-xl leading-none">&times;</button>
        </div>

        {/* Loading preview */}
        {isLoadingPreview && (
          <div className="flex items-center gap-2 text-slate-400 text-sm py-4">
            <Loader2 className="w-4 h-4 animate-spin" />
            Scanning inventory…
          </div>
        )}

        {/* Preview error */}
        {isPreviewError && (
          <p className="text-red-400 text-sm">Failed to load preview — please try again.</p>
        )}

        {/* Empty state */}
        {isEmpty && (
          <p className="text-slate-400 text-sm py-2">
            No smoke test assets found — inventory is clean.
          </p>
        )}

        {/* Success state */}
        {isDone && (
          <p className="text-green-400 text-sm py-2">
            Deleted {deleteMutation.data?.deleted ?? 0} asset{deleteMutation.data?.deleted !== 1 ? "s" : ""}.
          </p>
        )}

        {/* Asset list */}
        {!isLoadingPreview && !isPreviewError && !isEmpty && !isDone && preview && (
          <div className="space-y-1 max-h-64 overflow-y-auto">
            <p className="text-slate-400 text-xs mb-2">
              {preview.count} asset{preview.count !== 1 ? "s" : ""} will be removed from the inventory:
            </p>
            {preview.assets.map((a: CleanupAsset) => (
              <div key={a.id} className="flex items-center justify-between px-3 py-1.5 bg-navy rounded-lg text-xs">
                <span className="text-white font-mono">{a.name}</span>
                <span className="text-slate-400">{a.asset_type}</span>
              </div>
            ))}
          </div>
        )}

        {/* Delete mutation error */}
        {deleteMutation.isError && (
          <p className="text-red-400 text-sm">Deletion failed — please try again.</p>
        )}

        {/* Footer buttons */}
        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-300 hover:text-white transition-colors"
          >
            {isDone ? "Close" : "Cancel"}
          </button>
          {!isEmpty && !isDone && (
            <button
              onClick={() => deleteMutation.mutate()}
              disabled={isLoadingPreview || deleteMutation.isPending || !preview}
              className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : null}
              Delete {preview?.count ?? "…"} asset{preview?.count !== 1 ? "s" : ""}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add `showCleanupModal` state and button to the main `SmokeTests` component**

In the `SmokeTests` function body, after the existing `const [activeRunId, setActiveRunId] = useState<string | null>(null);` line, add:

```typescript
  const [showCleanupModal, setShowCleanupModal] = useState(false);
```

In the JSX return, replace this:
```typescript
      <PageHeader
        title="Smoke Tests"
        subtitle="Live end-to-end connector verification against real cloud infrastructure"
      />
```

With:
```typescript
      <div className="flex items-start justify-between">
        <PageHeader
          title="Smoke Tests"
          subtitle="Live end-to-end connector verification against real cloud infrastructure"
        />
        <button
          onClick={() => setShowCleanupModal(true)}
          className="shrink-0 mt-1 inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-600 hover:border-slate-400 text-slate-300 hover:text-white text-xs font-medium rounded-lg transition-colors"
        >
          Clean Up Inventory
        </button>
      </div>
```

At the bottom of the JSX return block, after the existing `{modalSuite && ...}` block and before the closing `</div>`, add:

```typescript
      {showCleanupModal && (
        <CleanupModal onClose={() => setShowCleanupModal(false)} />
      )}
```

- [ ] **Step 3: Verify TypeScript compiles cleanly**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -i 'SmokeTests\|cleanup\|Cleanup' | head -20"
```

Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SmokeTests.tsx
git commit -m "feat(smoke-tests): add Clean Up Inventory button and modal"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `GET /smoke-tests/cleanup-preview` — implemented, returns `{count, assets[]}`
- ✅ `DELETE /smoke-tests/cleanup-inventory` — implemented, returns `{deleted: N}`
- ✅ Pattern `nexplane-smoke` covers all suites (AWS, GCP, Azure, agent)
- ✅ `getCleanupPreview()` and `executeCleanup()` added to `smokeTestsApi.ts`
- ✅ `CleanupAsset` and `CleanupPreview` interfaces defined
- ✅ "Clean Up Inventory" button at page level (not per-suite)
- ✅ Modal shows loading spinner while fetching preview
- ✅ Modal shows empty state: "No smoke test assets found — inventory is clean"
- ✅ Modal shows asset list with name + type
- ✅ Red "Delete N assets" confirm button
- ✅ Cancel button present
- ✅ Success state: "Deleted N assets"
- ✅ Error state: shown inline within modal, modal stays open

**Type consistency:**
- `CleanupAsset` defined in `smokeTestsApi.ts` and imported in `SmokeTests.tsx`
- `getCleanupPreview()` returns `Promise<CleanupPreview>` (assets: `CleanupAsset[]`, count: `number`)
- `executeCleanup()` returns `Promise<{ deleted: number }>`
- Backend returns `{count: int, assets: [...]}` and `{deleted: int}` — matches TypeScript shapes

**Placeholder scan:** None found.
