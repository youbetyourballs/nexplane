# Smoke Test Inventory Cleanup — Design Spec

## Goal

Add a "Clean Up Inventory" button to the Smoke Tests page that lets users preview and bulk-delete Nexplane inventory assets left behind by smoke test runs.

## Background

Smoke tests create assets in the Nexplane inventory with predictable name prefixes (`nexplane-smoke-*`). The test runner already performs cloud-level teardown at the end of each run, but inventory records can be left behind when tests fail mid-run or cleanup is interrupted. This button provides a manual recovery path to restore a clean inventory state without having to delete assets one by one.

---

## Architecture

Two new backend endpoints are added to the existing smoke tests router. The frontend adds a page-level button and a modal component to `SmokeTests.tsx`. No new database tables or models are required.

**Pattern used for matching:** `name ILIKE '%nexplane-smoke%'`

This covers all suites:
- `nexplane-smoke-test-01` — AWS EC2
- `nexplane-smoke-ec2` — AWS agent asset
- `nexplane-smoke-gce-01` — GCP
- `nexplane-smoke-azure-01` — Azure

---

## Backend Changes

### Modified: `backend/app/routers/smoke_tests.py`

#### `GET /smoke-tests/cleanup-preview`

Returns all org assets whose name contains `nexplane-smoke`. No deletions performed.

**Response:**
```json
{
  "assets": [
    { "id": "uuid", "name": "nexplane-smoke-test-01", "asset_type": "server", "created_at": "2026-05-08T10:00:00Z" },
    { "id": "uuid", "name": "nexplane-smoke-ec2",     "asset_type": "server", "created_at": "2026-05-08T10:01:00Z" }
  ],
  "count": 2
}
```

#### `DELETE /smoke-tests/cleanup-inventory`

Deletes all org assets whose name contains `nexplane-smoke`. Returns count of deleted records.

**Response:**
```json
{ "deleted": 2 }
```

**Auth:** Same org-scoped `current_user` dependency as all other smoke test endpoints.

---

## Frontend Changes

### Modified: `frontend/src/api/smokeTestsApi.ts`

Add two functions:

```typescript
getCleanupPreview(): Promise<{ assets: CleanupAsset[]; count: number }>
executeCleanup(): Promise<{ deleted: number }>
```

Where `CleanupAsset` is:
```typescript
interface CleanupAsset {
  id: string;
  name: string;
  asset_type: string;
  created_at: string;
}
```

### Modified: `frontend/src/pages/SmokeTests.tsx`

**Button placement:** Page-level, above the suite cards. Styled as a secondary/outline button so it does not compete visually with the suite "Run" buttons.

**`CleanupModal` component flow:**

1. User clicks "Clean Up Inventory"
2. `getCleanupPreview()` fires; modal opens showing a spinner
3. On success:
   - **Empty list:** "No smoke test assets found — inventory is clean." Cancel button only.
   - **Non-empty list:** Asset list (name + type per row), then a red "Delete N assets" confirm button and a Cancel button.
4. User clicks "Delete N assets" → `executeCleanup()` fires
5. **Success:** Modal closes; inline success message shown: "Deleted N assets."
6. **Error (either call):** Modal stays open; error message shown inline within the modal.

---

## Files Affected

| File | Change |
|------|--------|
| `backend/app/routers/smoke_tests.py` | Add `GET /smoke-tests/cleanup-preview` and `DELETE /smoke-tests/cleanup-inventory` |
| `frontend/src/api/smokeTestsApi.ts` | Add `getCleanupPreview()`, `executeCleanup()`, `CleanupAsset` interface |
| `frontend/src/pages/SmokeTests.tsx` | Add "Clean Up Inventory" button and `CleanupModal` component |
