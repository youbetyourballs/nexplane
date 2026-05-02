# Delete Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a delete button per connector card with inline two-state confirmation, backed by a new `DELETE /connectors/{id}` backend endpoint.

**Architecture:** Backend adds one new route to `connectors.py` following the existing `record_event` audit pattern. Frontend adds `connectorsApi.delete` to `endpoints.ts`, then wires a `deletingId` state + `deleteMutation` + trash icon button into each connector card in `Connectors.tsx`. The DB cascades credential and schedule deletion automatically.

**Tech Stack:** FastAPI (Python), React 18 + TypeScript, TanStack Query, Tailwind CSS, lucide-react.

**Working directory:** `f:\Nexplane\nexplane`

---

### Task 1: Backend — `DELETE /connectors/{id}` endpoint

**Files:**
- Modify: `backend/app/routers/connectors.py`
- Test: `backend/app/tests/test_connector_delete.py`

- [ ] **Step 1: Write failing test**

Create `backend/app/tests/test_connector_delete.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_delete_connector_returns_204(client: AsyncClient, operator_token: str, seeded_connector_id: str):
    resp = await client.delete(
        f"/connectors/{seeded_connector_id}",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_connector_removes_from_list(client: AsyncClient, operator_token: str, seeded_connector_id: str):
    await client.delete(
        f"/connectors/{seeded_connector_id}",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    list_resp = await client.get(
        "/connectors",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    ids = [c["id"] for c in list_resp.json()]
    assert seeded_connector_id not in ids


@pytest.mark.asyncio
async def test_delete_connector_404_for_wrong_org(client: AsyncClient, operator_token: str):
    import uuid
    resp = await client.delete(
        f"/connectors/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```
docker compose exec backend python -m pytest app/tests/test_connector_delete.py -v
```

Expected: FAIL — `405 Method Not Allowed` or `404` (endpoint doesn't exist yet)

- [ ] **Step 3: Add the DELETE endpoint to `backend/app/routers/connectors.py`**

Read the file first to find the right insertion point (after the existing `POST /{connector_id}/test` block). Add this route:

```python
@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.organization_id == user.organization_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    await db.delete(connector)
    await db.flush()
    await record_event(
        db,
        user.organization_id,
        "connector.deleted",
        {"connector_id": str(connector_id)},
        actor_id=user.id,
    )
    await db.commit()
```

**Important:** Place this route BEFORE the `/{connector_id}/credentials` and `/{connector_id}/schedule` routes, not after — FastAPI matches routes in order and a plain `/{connector_id}` DELETE must appear before sub-path routes to avoid conflicts. Inserting it right after the `POST /` (create) route and before `POST /{connector_id}/test` is the safe position.

- [ ] **Step 4: Run tests to verify they pass**

```
docker compose exec backend python -m pytest app/tests/test_connector_delete.py -v
```

Expected: all 3 PASS

- [ ] **Step 5: Run full backend test suite**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: 116+ passed

- [ ] **Step 6: Commit**

```
git add backend/app/routers/connectors.py backend/app/tests/test_connector_delete.py
git commit -m "feat(connectors): add DELETE /connectors/{id} endpoint with audit event"
```

---

### Task 2: Frontend — `connectorsApi.delete` and UI

**Files:**
- Modify: `frontend/src/api/endpoints.ts`
- Modify: `frontend/src/pages/Connectors.tsx`

- [ ] **Step 1: Add `delete` to `connectorsApi` in `frontend/src/api/endpoints.ts`**

Read the file. The `connectorsApi` object currently looks like:

```typescript
export const connectorsApi = {
  list: () => apiClient.get<Connector[]>("/connectors").then((r) => r.data),
  create: (data: ConnectorCreate) => apiClient.post<Connector>("/connectors", data).then((r) => r.data),
  test: (id: string) =>
    apiClient.post<ConnectorTestResult>(`/connectors/${id}/test`).then((r) => r.data),
  ingest: (id: string, actionId: string) =>
    apiClient.post<IngestResponse>(`/connectors/${id}/ingest/${actionId}`).then((r) => r.data),
};
```

Add `delete` after `create`:

```typescript
export const connectorsApi = {
  list: () => apiClient.get<Connector[]>("/connectors").then((r) => r.data),
  create: (data: ConnectorCreate) => apiClient.post<Connector>("/connectors", data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/connectors/${id}`),
  test: (id: string) =>
    apiClient.post<ConnectorTestResult>(`/connectors/${id}/test`).then((r) => r.data),
  ingest: (id: string, actionId: string) =>
    apiClient.post<IngestResponse>(`/connectors/${id}/ingest/${actionId}`).then((r) => r.data),
};
```

- [ ] **Step 2: Add `Trash2` to lucide-react imports in `Connectors.tsx`**

The current import line is:

```tsx
import { Plug, CheckCircle2, XCircle, Loader2, Download } from "lucide-react";
```

Change it to:

```tsx
import { Plug, CheckCircle2, XCircle, Loader2, Download, Trash2 } from "lucide-react";
```

- [ ] **Step 3: Add `deletingId` state and `deleteMutation` in `Connectors.tsx`**

Inside the `Connectors` function, after the existing `scheduleModal` state and `addModalOpen` state, add:

```tsx
const [deletingId, setDeletingId] = useState<string | null>(null);

const deleteMutation = useMutation({
  mutationFn: (id: string) => connectorsApi.delete(id),
  onSuccess: (_: unknown, id: string) => {
    setDeletingId(null);
    qc.invalidateQueries({ queryKey: ["connectors"] });
  },
});
```

- [ ] **Step 4: Add delete button to each connector card in `Connectors.tsx`**

Find the top-right section of each card. Currently it shows only the status badge:

```tsx
<span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
  connector.status === "active"
    ? "bg-emerald-50 text-emerald-700"
    : connector.status === "error"
    ? "bg-red-50 text-red-700"
    : "bg-slate-100 text-slate-500"
}`}>
  {connector.status}
</span>
```

Replace that `<span>` with a flex container that holds the status badge plus the delete control:

```tsx
<div className="flex items-center gap-2">
  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
    connector.status === "active"
      ? "bg-emerald-50 text-emerald-700"
      : connector.status === "error"
      ? "bg-red-50 text-red-700"
      : "bg-slate-100 text-slate-500"
  }`}>
    {connector.status}
  </span>

  {deletingId === connector.id ? (
    <div className="flex items-center gap-2">
      <button
        onClick={() => deleteMutation.mutate(connector.id)}
        disabled={deleteMutation.isPending}
        className="text-xs text-red-600 font-medium hover:text-red-800 disabled:opacity-50"
      >
        {deleteMutation.isPending && deleteMutation.variables === connector.id
          ? "Deleting…"
          : "Confirm delete"}
      </button>
      <button
        onClick={() => setDeletingId(null)}
        className="text-xs text-slate-400 hover:text-slate-600"
      >
        Cancel
      </button>
    </div>
  ) : (
    <button
      onClick={() => setDeletingId(connector.id)}
      className="text-slate-300 hover:text-red-400 transition-colors"
      title="Delete connector"
    >
      <Trash2 className="w-4 h-4" />
    </button>
  )}
</div>
```

- [ ] **Step 5: Build frontend to verify no TypeScript errors**

```
cd frontend && npm run build 2>&1 | tail -10
```

Expected: Build succeeds. The only errors should be pre-existing ones unrelated to this change. Fix any new errors before proceeding.

- [ ] **Step 6: Commit**

```
git add frontend/src/api/endpoints.ts frontend/src/pages/Connectors.tsx
git commit -m "feat(connectors): add delete button with inline confirmation and connectorsApi.delete"
```

---

### Task 3: Final verification

- [ ] **Step 1: Run all backend tests**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: 119+ passed (3 new tests added in Task 1)

- [ ] **Step 2: Confirm frontend build is clean**

```
cd frontend && npm run build 2>&1 | grep -E "error TS" | grep -v "ChangeRequestDetail\|ProjectDetail\|client\.ts"
```

Expected: no output (no new TypeScript errors)

- [ ] **Step 3: Commit plan to repo**

```
git add docs/superpowers/plans/2026-05-01-delete-connector-plan.md
git commit -m "docs: add delete connector implementation plan" --allow-empty
```
