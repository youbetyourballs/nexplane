# UI Plan 2: Dashboard Enhancements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four missing panels to the Dashboard: (1) rollback status on in-flight CRs, (2) exposure summary by asset risk tier, (3) connector health warning banner when any connector is in error/credential_expired state, (4) top unmitigated risks panel showing the 5 most critical assets.

**Architecture:** All changes are in `frontend/src/pages/Dashboard.tsx`. Each new panel is a self-contained component in the same file. Data comes from existing `/change-requests`, `/connectors`, and `/assets` API endpoints — no backend changes needed.

**Tech Stack:** React, TanStack Query, Tailwind CSS, lucide-react, date-fns

---

## File Map

- Modify: `frontend/src/pages/Dashboard.tsx` — add InFlightPanel, ExposureSummary, ConnectorHealthBanner, TopRisksPanel

---

### Task 1: In-Flight CRs panel with rollback status

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Dashboard.InFlight.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: {
    list: jest.fn().mockResolvedValue([
      {
        id: "cr1",
        title: "Patch web servers",
        change_type: "patch_packages",
        status: "executing",
        risk_level: "high",
        requester: { name: "Alice" },
        created_at: new Date().toISOString(),
        rollback_available: true,
      },
    ]),
  },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({ data: [] });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 1, asset_count: 1 } });
      if (url.includes("assets")) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

test("shows In-Flight section header", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("In-Flight Changes")).toBeInTheDocument();
});

test("shows rollback available indicator for executing CRs", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText(/rollback available/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.InFlight.test.tsx --no-coverage
```

Expected: FAIL — "In-Flight Changes" not found.

- [ ] **Step 3: Add InFlightPanel to Dashboard.tsx**

In `frontend/src/pages/Dashboard.tsx`, add a new component after the existing `ChangeRequestRow` component at the bottom of the file:

```tsx
function InFlightPanel({ crs }: { crs: ChangeRequestSummary[] }) {
  if (crs.length === 0) return null;
  return (
    <div className="bg-white rounded-lg border border-slate-200 mb-6">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Play className="w-4 h-4 text-blue-500" />
          <h2 className="text-sm font-semibold text-slate-900">In-Flight Changes</h2>
          <span className="text-xs text-slate-400">({crs.length})</span>
        </div>
      </div>
      <div className="divide-y divide-slate-50">
        {crs.map((cr) => (
          <Link
            key={cr.id}
            to={`/change-requests/${cr.id}`}
            className="flex items-center gap-4 px-5 py-3 hover:bg-slate-50 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-slate-900 truncate">{cr.title}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                {cr.change_type.replace(/_/g, " ")} · {cr.requester.name}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <StatusBadge status={cr.status} size="sm" />
              {(cr as any).rollback_available ? (
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
                  <CheckCircle2 className="w-3 h-3" />
                  rollback available
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-xs text-slate-400 bg-slate-50 px-2 py-0.5 rounded-full border border-slate-200">
                  no rollback
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

Then in the `Dashboard` function, find the `executing` array (already computed as `crs.filter(...)`) and add `<InFlightPanel crs={executing} />` after the stat cards grid and before the connector status widget:

```tsx
{/* In-flight panel — replaces the "Executing" stat card detail */}
<InFlightPanel crs={executing} />
```

Note: The `ChangeRequestSummary` type in `types/api.ts` may not have `rollback_available`. The panel uses `(cr as any).rollback_available` to handle this gracefully until the type is extended. If the backend doesn't return this field yet, the "no rollback" state is shown, which is safe.

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.InFlight.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx frontend/src/pages/__tests__/Dashboard.InFlight.test.tsx
git commit -m "feat: add in-flight CRs panel with rollback status to dashboard"
```

---

### Task 2: Exposure summary by asset risk tier

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Dashboard.Exposure.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({ data: [] });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 1, asset_count: 5 } });
      if (url.includes("assets")) return Promise.resolve({
        data: [
          { id: "1", criticality: "critical", environment: "prod" },
          { id: "2", criticality: "high", environment: "prod" },
          { id: "3", criticality: "medium", environment: "staging" },
          { id: "4", criticality: "low", environment: "dev" },
          { id: "5", criticality: "critical", environment: "prod" },
        ],
      });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows Exposure Summary heading", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("Exposure Summary")).toBeInTheDocument();
});

test("shows critical count", async () => {
  wrap(<Dashboard />);
  // 2 critical assets
  expect(await screen.findByText("2")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.Exposure.test.tsx --no-coverage
```

Expected: FAIL — "Exposure Summary" not found.

- [ ] **Step 3: Add ExposureSummary component and assets query to Dashboard.tsx**

Add an assets query inside the `Dashboard` function (after the existing queries):

```tsx
const { data: allAssets } = useQuery<{ id: string; criticality: string; environment: string }[]>({
  queryKey: ["assets", {}],
  queryFn: () => apiClient.get("/assets").then((r) => r.data),
  staleTime: 120_000,
});
```

Add this component above `ChangeRequestRow`:

```tsx
function ExposureSummary({
  assets,
}: {
  assets: { id: string; criticality: string; environment: string }[];
}) {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const a of assets) {
    if (a.criticality in counts) counts[a.criticality as keyof typeof counts]++;
  }

  const tiers: { key: keyof typeof counts; label: string; color: string; bg: string }[] = [
    { key: "critical", label: "Critical", color: "text-red-700", bg: "bg-red-50 border-red-200" },
    { key: "high", label: "High", color: "text-orange-700", bg: "bg-orange-50 border-orange-200" },
    { key: "medium", label: "Medium", color: "text-amber-700", bg: "bg-amber-50 border-amber-200" },
    { key: "low", label: "Low", color: "text-slate-600", bg: "bg-slate-50 border-slate-200" },
  ];

  return (
    <div className="bg-white rounded-lg border border-slate-200 mb-6">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">Exposure Summary</h2>
        </div>
        <Link to="/assets?preset=unmitigated_criticals" className="text-xs text-brand-600 hover:text-brand-700 font-medium">
          View critical assets →
        </Link>
      </div>
      <div className="px-5 py-4 grid grid-cols-4 gap-3">
        {tiers.map(({ key, label, color, bg }) => (
          <Link
            key={key}
            to={`/assets?search=criticality:${key}`}
            className={`flex flex-col items-center p-3 rounded-lg border ${bg} hover:opacity-80 transition-opacity`}
          >
            <span className={`text-2xl font-bold ${color}`}>{counts[key]}</span>
            <span className={`text-xs font-medium ${color} mt-0.5`}>{label}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

In the `Dashboard` return, add `<ExposureSummary assets={allAssets ?? []} />` after the stat cards grid.

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.Exposure.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx frontend/src/pages/__tests__/Dashboard.Exposure.test.tsx
git commit -m "feat: add exposure summary panel by asset risk tier to dashboard"
```

---

### Task 3: Connector health warning banner

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Dashboard.ConnectorWarning.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({
        data: [
          { id: "c1", connector_type: "aws", name: "AWS Prod", status: "error" },
          { id: "c2", connector_type: "okta", name: "Okta", status: "credential_expired" },
        ],
      });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 2, asset_count: 5 } });
      if (url.includes("assets")) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows connector warning banner when connectors have errors", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText(/connector.*issue/i)).toBeInTheDocument();
});

test("mentions the number of failing connectors", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText(/2/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.ConnectorWarning.test.tsx --no-coverage
```

Expected: FAIL.

- [ ] **Step 3: Add ConnectorHealthBanner component and render in Dashboard**

Add this component to `frontend/src/pages/Dashboard.tsx`:

```tsx
function ConnectorHealthBanner({ connectors }: { connectors: ConnectorRead[] }) {
  const failing = connectors.filter(
    (c) => c.status === "error" || c.status === "credential_expired"
  );
  if (failing.length === 0) return null;

  return (
    <div className="mb-6 flex items-start gap-3 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg">
      <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-amber-900">
          {failing.length} connector {failing.length === 1 ? "issue" : "issues"} detected
        </p>
        <p className="text-xs text-amber-700 mt-0.5">
          {failing.map((c) => c.name).join(", ")} —{" "}
          {failing.some((c) => c.status === "credential_expired")
            ? "credentials expired or auth failure"
            : "last sync failed"}
        </p>
      </div>
      <Link
        to="/connectors"
        className="text-xs font-medium text-amber-800 hover:text-amber-900 shrink-0 underline"
      >
        Fix now →
      </Link>
    </div>
  );
}
```

In the `Dashboard` return JSX, add `<ConnectorHealthBanner connectors={connectors ?? []} />` at the very top of the content area (after the page heading `<div className="mb-8">` block).

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.ConnectorWarning.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx frontend/src/pages/__tests__/Dashboard.ConnectorWarning.test.tsx
git commit -m "feat: add connector health warning banner to dashboard"
```

---

### Task 4: Top unmitigated risks panel

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Dashboard.TopRisks.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({ data: [] });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 1, asset_count: 3 } });
      if (url.includes("assets")) return Promise.resolve({
        data: [
          { id: "1", name: "prod-web-01", criticality: "critical", environment: "prod", asset_type: "server", tags: [] },
          { id: "2", name: "prod-db-01", criticality: "critical", environment: "prod", asset_type: "database", tags: [] },
          { id: "3", name: "staging-app", criticality: "high", environment: "staging", asset_type: "application", tags: [] },
        ],
      });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows Top Risks heading", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("Top At-Risk Assets")).toBeInTheDocument();
});

test("shows the critical asset names", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("prod-web-01")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.TopRisks.test.tsx --no-coverage
```

Expected: FAIL.

- [ ] **Step 3: Add TopRisksPanel component**

Add this component to `frontend/src/pages/Dashboard.tsx`:

```tsx
const CRITICALITY_ORDER: Record<string, number> = {
  critical: 0, high: 1, medium: 2, low: 3,
};

function TopRisksPanel({
  assets,
}: {
  assets: { id: string; name: string; criticality: string; environment: string; asset_type: string }[];
}) {
  const top5 = [...assets]
    .sort(
      (a, b) =>
        (CRITICALITY_ORDER[a.criticality] ?? 9) - (CRITICALITY_ORDER[b.criticality] ?? 9)
    )
    .slice(0, 5)
    .filter((a) => ["critical", "high"].includes(a.criticality));

  if (top5.length === 0) return null;

  return (
    <div className="bg-white rounded-lg border border-slate-200 mb-6">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <h2 className="text-sm font-semibold text-slate-900">Top At-Risk Assets</h2>
        </div>
        <Link to="/assets?preset=unmitigated_criticals" className="text-xs text-brand-600 hover:text-brand-700 font-medium">
          View all →
        </Link>
      </div>
      <div className="divide-y divide-slate-50">
        {top5.map((asset) => (
          <Link
            key={asset.id}
            to={`/assets/${asset.id}`}
            className="flex items-center gap-3 px-5 py-3 hover:bg-slate-50 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-slate-900 truncate">{asset.name}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                {asset.asset_type.replace(/_/g, " ")} · {asset.environment}
              </div>
            </div>
            <RiskBadge level={asset.criticality as any} size="sm" />
          </Link>
        ))}
      </div>
    </div>
  );
}
```

In the `Dashboard` return, add `<TopRisksPanel assets={allAssets ?? []} />` after the ExposureSummary panel.

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend && npx jest src/pages/__tests__/Dashboard.TopRisks.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx frontend/src/pages/__tests__/Dashboard.TopRisks.test.tsx
git commit -m "feat: add top at-risk assets panel to dashboard"
```

---

### Task 5: Browser verification

- [ ] **Step 1: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 2: Verify dashboard panels**

Open http://100.101.186.39 in browser. Verify:
1. Connector health warning appears if any connector is in error state
2. Exposure Summary shows 4 criticality tiers with asset counts as links to filtered asset view
3. In-Flight Changes panel shows executing CRs with rollback status indicators
4. Top At-Risk Assets shows up to 5 critical/high assets
5. Existing panels (onboarding checklist, stat cards, connector status, recent CRs) still work

- [ ] **Step 3: Commit final tweaks if any**

```bash
git add -p && git commit -m "fix: dashboard panel tweaks from browser verification"
```
