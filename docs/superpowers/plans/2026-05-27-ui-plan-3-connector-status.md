# UI Plan 3: Connector Status & Error Recovery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit connector status states (credential_expired, never_synced, disabled), inline error recovery banners on connector cards, and graceful rendering for all status states in the Connectors page.

**Architecture:** All frontend changes. The backend `connectors` table already has a `status` field. We extend the frontend to handle new status values it may already receive (`credential_expired`, `never_synced`, `disabled`) and add inline UI affordances to recover from error states without navigating away. No backend schema changes — additive frontend handling only.

**Tech Stack:** React, TanStack Query, Tailwind CSS, lucide-react

**Important constraint:** Do NOT delete or overwrite any existing connector records. This plan only changes how the frontend renders connector status — it does not mutate DB records.

---

## File Map

- Modify: `frontend/src/types/api.ts` — extend `ConnectorRead.status` type to include new states
- Modify: `frontend/src/pages/Connectors.tsx` — add status-aware rendering, inline error recovery banner
- Modify: `frontend/src/pages/Dashboard.tsx` — connector status widget already updated in Plan 2 (ConnectorHealthBanner); the detailed tile view here gets status-aware styling

---

### Task 1: Extend ConnectorRead status type

**Files:**
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Find the ConnectorRead type**

Open `frontend/src/types/api.ts`. Find the `ConnectorRead` interface (it will be somewhere after line 80). It currently has `status: string` or a limited union type.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/types/__tests__/connectorStatus.test.ts`:

```ts
import type { ConnectorStatus } from "../api";

// TypeScript compile-time test — if ConnectorStatus doesn't include these values, tsc will fail
const statuses: ConnectorStatus[] = [
  "active",
  "error",
  "syncing",
  "credential_expired",
  "never_synced",
  "disabled",
];

test("ConnectorStatus type includes all 6 states", () => {
  expect(statuses).toHaveLength(6);
});
```

- [ ] **Step 3: Run test to verify it fails**

```
cd frontend && npx jest src/types/__tests__/connectorStatus.test.ts --no-coverage
```

Expected: FAIL — `ConnectorStatus` is not exported from `../api`.

- [ ] **Step 4: Add ConnectorStatus type to api.ts**

In `frontend/src/types/api.ts`, find the `ConnectorRead` interface. Add the exported type and update the interface's `status` field:

```ts
export type ConnectorStatus =
  | "active"
  | "error"
  | "syncing"
  | "credential_expired"
  | "never_synced"
  | "disabled";
```

Then update `ConnectorRead` to use it:

```ts
export interface ConnectorRead {
  // ... existing fields ...
  status: ConnectorStatus;
  // ... rest of fields ...
}
```

- [ ] **Step 5: Run test to verify it passes**

```
cd frontend && npx jest src/types/__tests__/connectorStatus.test.ts --no-coverage
```

Expected: PASS.

- [ ] **Step 6: Run TypeScript check**

```
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: No new errors. Fix any type errors that appear (usually just adding the new type to existing status checks).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/types/__tests__/connectorStatus.test.ts
git commit -m "feat: add ConnectorStatus type with all 6 states"
```

---

### Task 2: Status-aware rendering in Connectors page

**Files:**
- Modify: `frontend/src/pages/Connectors.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Connectors.status.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockConnectors = [
  { id: "c1", connector_type: "aws", name: "AWS Prod", status: "active", scoped_permissions: {} },
  { id: "c2", connector_type: "okta", name: "Okta", status: "credential_expired", scoped_permissions: {} },
  { id: "c3", connector_type: "azure", name: "Azure", status: "never_synced", scoped_permissions: {} },
  { id: "c4", connector_type: "gcp", name: "GCP", status: "error", scoped_permissions: {} },
  { id: "c5", connector_type: "ldap", name: "LDAP", status: "disabled", scoped_permissions: {} },
];

jest.mock("../../api/endpoints", () => ({
  connectorsApi: {
    list: jest.fn().mockResolvedValue(mockConnectors),
    test: jest.fn(),
    delete: jest.fn(),
    ingest: jest.fn(),
  },
}));
jest.mock("../../api/client", () => ({
  apiClient: { get: jest.fn().mockResolvedValue({ data: null }) },
}));

import { Connectors } from "../Connectors";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows credential expired banner for okta connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/credentials expired/i)).toBeInTheDocument();
});

test("shows error banner for GCP connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/last sync failed/i)).toBeInTheDocument();
});

test("shows never synced label for Azure connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/never synced/i)).toBeInTheDocument();
});

test("shows disabled label for LDAP connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/disabled/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Connectors.status.test.tsx --no-coverage
```

Expected: FAIL — status-specific text not found.

- [ ] **Step 3: Add status config and inline error recovery banner to Connectors.tsx**

In `frontend/src/pages/Connectors.tsx`, add the following after the existing `INTERVAL_LABELS` constant (before the `ConnectorScheduleBadge` component):

```tsx
import type { ConnectorStatus } from "../types/api";

const STATUS_CONFIG: Record<ConnectorStatus, { label: string; badgeClass: string }> = {
  active:             { label: "Active",            badgeClass: "bg-emerald-50 text-emerald-700" },
  syncing:            { label: "Syncing…",          badgeClass: "bg-blue-50 text-blue-700" },
  error:              { label: "Error",             badgeClass: "bg-red-50 text-red-700" },
  credential_expired: { label: "Auth Failed",       badgeClass: "bg-red-50 text-red-700" },
  never_synced:       { label: "Never Synced",      badgeClass: "bg-slate-100 text-slate-500" },
  disabled:           { label: "Disabled",          badgeClass: "bg-slate-100 text-slate-400" },
};

function ConnectorErrorBanner({
  connector,
  onUpdateCredentials,
  onRetrySync,
}: {
  connector: ConnectorRead;
  onUpdateCredentials: () => void;
  onRetrySync: () => void;
}) {
  if (connector.status === "credential_expired") {
    return (
      <div className="mt-3 flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-md">
        <AlertTriangle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-red-800">Credentials expired</p>
          <p className="text-xs text-red-600 mt-0.5">
            Authentication failed — the stored credentials are no longer valid.
          </p>
        </div>
        <button
          onClick={onUpdateCredentials}
          className="shrink-0 text-xs font-medium text-red-700 hover:text-red-900 underline"
        >
          Update Credentials
        </button>
      </div>
    );
  }

  if (connector.status === "error") {
    return (
      <div className="mt-3 flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-md">
        <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-amber-800">Last sync failed</p>
          <p className="text-xs text-amber-600 mt-0.5">
            The previous discovery run encountered an error. Check credentials or retry.
          </p>
        </div>
        <button
          onClick={onRetrySync}
          className="shrink-0 text-xs font-medium text-amber-700 hover:text-amber-900 underline"
        >
          Retry Sync
        </button>
      </div>
    );
  }

  if (connector.status === "never_synced") {
    return (
      <div className="mt-3 p-3 bg-slate-50 border border-slate-200 rounded-md">
        <p className="text-xs text-slate-500">Never synced — run discovery to populate assets.</p>
      </div>
    );
  }

  return null;
}
```

Add `AlertTriangle` to the lucide-react import at the top of `Connectors.tsx`:

```tsx
import { Plug, CheckCircle2, XCircle, Loader2, Download, Trash2, AlertTriangle } from "lucide-react";
```

In the connector card JSX (inside the `.map((connector) => ...)` block), find the status badge span and replace it with a status-config-driven version:

```tsx
// Replace this:
<span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
  connector.status === "active"
    ? "bg-emerald-50 text-emerald-700"
    : connector.status === "error"
    ? "bg-red-50 text-red-700"
    : "bg-slate-100 text-slate-500"
}`}>
  {connector.status}
</span>

// With this:
<span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
  (STATUS_CONFIG[connector.status as ConnectorStatus] ?? STATUS_CONFIG.never_synced).badgeClass
}`}>
  {(STATUS_CONFIG[connector.status as ConnectorStatus] ?? STATUS_CONFIG.never_synced).label}
</span>
```

Then add the error banner just before the existing `<div className="mb-3">` scoped permissions section in each card:

```tsx
<ConnectorErrorBanner
  connector={connector}
  onUpdateCredentials={() => setCredModalConnector(connector)}
  onRetrySync={() => ingestActionId
    ? ingestMutation.mutate({ id: connector.id, actionId: ingestActionId })
    : testConnector(connector.id)
  }
/>
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/Connectors.status.test.tsx --no-coverage
```

Expected: PASS (4 tests).

- [ ] **Step 5: Run TypeScript check**

```
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Connectors.tsx frontend/src/pages/__tests__/Connectors.status.test.tsx
git commit -m "feat: add connector status states and inline error recovery banners"
```

---

### Task 3: Browser verification

- [ ] **Step 1: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 2: Verify connector page**

Open http://100.101.186.39/connectors. Verify:
1. All existing connectors show correct status badges (active connectors show "Active" in green)
2. No existing connector records were affected
3. Status badge labels are human-readable (not raw enum values like "active", "error")
4. If any connector is in error state, the inline error recovery banner appears

- [ ] **Step 3: Verify dashboard warning**

Navigate to the Dashboard. Verify the ConnectorHealthBanner (from Plan 2) correctly identifies connectors with error/credential_expired status.

- [ ] **Step 4: Commit any tweaks**

```bash
git add -p && git commit -m "fix: connector status display tweaks from browser verification"
```
