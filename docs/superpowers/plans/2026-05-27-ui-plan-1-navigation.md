# UI Plan 1: Navigation & Information Architecture

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the sidebar navigation to match the spec: add an Operations section (Runbooks + Scheduled Ops + Maintenance Windows), remove Vuln Remediation as a standalone nav item, fold Approvals Queue into Change Requests with a count badge, redirect /remediation to /assets with a preset filter, and pin an onboarding checklist above nav items in the sidebar until the first connector is live.

**Architecture:** All changes are frontend-only. The sidebar gets a grouped structure with an Operations collapsible section. VulnerabilityRemediation.tsx becomes a redirect component. Assets.tsx gains URL preset handling. No backend changes.

**Tech Stack:** React, React Router v6, TanStack Query, Tailwind CSS, lucide-react

---

## File Map

- Modify: `frontend/src/components/Sidebar.tsx` — restructure nav items, add Operations group, remove remediation and approvals entries, add pending-approval badge on Change Requests, add onboarding checklist strip above nav when no connectors are live
- Modify: `frontend/src/pages/VulnerabilityRemediation.tsx` — replace with redirect to `/assets?preset=unmitigated_criticals`
- Modify: `frontend/src/pages/Assets.tsx` — read `preset` URL param on mount and apply filter preset

---

### Task 1: Add Operations section and pending approval badge to Sidebar

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/__tests__/Sidebar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "../Sidebar";

// Mock useAuth
jest.mock("../../hooks/useAuth", () => ({
  useAuth: () => ({ user: { name: "Test", email: "t@t.com", role: "admin" }, logout: jest.fn() }),
}));

// Mock apiClient
jest.mock("../../api/client", () => ({
  apiClient: { get: jest.fn().mockResolvedValue({ data: [] }) },
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

test("shows Operations section with Runbooks, Scheduled Ops, Maintenance Windows", () => {
  wrap(<Sidebar />);
  expect(screen.getByText("Operations")).toBeInTheDocument();
  expect(screen.getByText("Runbooks")).toBeInTheDocument();
  expect(screen.getByText("Scheduled Ops")).toBeInTheDocument();
  expect(screen.getByText("Maintenance Windows")).toBeInTheDocument();
});

test("does not show Vuln Remediation as a top-level nav item", () => {
  wrap(<Sidebar />);
  expect(screen.queryByText("Vuln Remediation")).not.toBeInTheDocument();
});

test("does not show Approvals Queue as a top-level nav item", () => {
  wrap(<Sidebar />);
  expect(screen.queryByText("Approvals Queue")).not.toBeInTheDocument();
});

test("shows Change Requests nav item", () => {
  wrap(<Sidebar />);
  expect(screen.getByText("Change Requests")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/components/__tests__/Sidebar.test.tsx --no-coverage
```

Expected: FAIL — "Operations" not found, "Vuln Remediation" found, "Approvals Queue" found.

- [ ] **Step 3: Rewrite Sidebar.tsx**

Replace the full contents of `frontend/src/components/Sidebar.tsx`:

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
  FlaskConical,
  Bell,
  ChevronDown,
  ChevronRight,
  Wrench,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

const mainNavItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/projects", label: "Projects", icon: FolderOpen },
  { to: "/assets", label: "Asset Inventory", icon: Server },
  { to: "/change-requests", label: "Change Requests", icon: FileStack },
  { to: "/connectors", label: "Connectors", icon: Plug },
];

const operationsNavItems = [
  { to: "/runbooks", label: "Runbooks", icon: BookOpen },
  { to: "/scheduled-operations", label: "Scheduled Ops", icon: CalendarClock },
  { to: "/maintenance-windows", label: "Maintenance Windows", icon: Clock },
];

const bottomNavItems = [
  { to: "/access-reviews", label: "Access Reviews", icon: ShieldCheck },
  { to: "/compliance", label: "Compliance", icon: ClipboardList },
  { to: "/backup-recovery", label: "Backup & Recovery", icon: HardDrive },
  { to: "/smoke-tests", label: "Smoke Tests", icon: FlaskConical },
  { to: "/notifications", label: "Notifications", icon: Bell },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

function NavItem({
  to,
  label,
  icon: Icon,
  exact,
  badge,
}: {
  to: string;
  label: string;
  icon: React.ElementType;
  exact?: boolean;
  badge?: number;
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
      {label}
    </NavLink>
  );
}

export function Sidebar() {
  const { user, logout } = useAuth();
  const [operationsOpen, setOperationsOpen] = useState(true);

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

  return (
    <aside className="fixed inset-y-0 left-0 w-60 bg-navy flex flex-col z-10">
      <div className="flex items-center px-5 py-4 border-b border-navy-border">
        <img src="/title_white.png" alt="Nexplane" className="h-8 w-auto" />
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {mainNavItems.map(({ to, label, icon, exact }) => (
          <NavItem
            key={to}
            to={to}
            label={label}
            icon={icon}
            exact={exact}
            badge={label === "Change Requests" ? pendingCount : undefined}
          />
        ))}

        {/* Operations section */}
        <div className="pt-2">
          <button
            onClick={() => setOperationsOpen((v) => !v)}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wide hover:text-slate-300 transition-colors"
          >
            <Wrench className="w-3 h-3" />
            <span>Operations</span>
            {operationsOpen ? (
              <ChevronDown className="w-3 h-3 ml-auto" />
            ) : (
              <ChevronRight className="w-3 h-3 ml-auto" />
            )}
          </button>
          {operationsOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {operationsNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        <div className="pt-2 border-t border-navy-border mt-2 space-y-0.5">
          {bottomNavItems.map(({ to, label, icon }) => (
            <NavItem
              key={to}
              to={to}
              label={label}
              icon={icon}
              badge={
                to === "/notifications" && unreadCount > 0 ? unreadCount : undefined
              }
            />
          ))}
        </div>
      </nav>

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

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/components/__tests__/Sidebar.test.tsx --no-coverage
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/__tests__/Sidebar.test.tsx
git commit -m "feat: restructure sidebar nav — Operations group, fold approvals into CR badge"
```

---

### Task 2: Redirect /remediation to /assets with preset

**Files:**
- Modify: `frontend/src/pages/VulnerabilityRemediation.tsx`
- Modify: `frontend/src/pages/Assets.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/VulnerabilityRemediationRedirect.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

// We'll test that navigating to /remediation lands on /assets with preset param
let capturedSearch = "";

jest.mock("react-router-dom", () => {
  const actual = jest.requireActual("react-router-dom");
  return {
    ...actual,
    Navigate: ({ to }: { to: string }) => {
      capturedSearch = to;
      return null;
    },
  };
});

import VulnerabilityRemediation from "../VulnerabilityRemediation";

test("redirects /remediation to /assets?preset=unmitigated_criticals", () => {
  render(
    <MemoryRouter initialEntries={["/remediation"]}>
      <Routes>
        <Route path="/remediation" element={<VulnerabilityRemediation />} />
      </Routes>
    </MemoryRouter>
  );
  expect(capturedSearch).toBe("/assets?preset=unmitigated_criticals");
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/VulnerabilityRemediationRedirect.test.tsx --no-coverage
```

Expected: FAIL — VulnerabilityRemediation does not redirect.

- [ ] **Step 3: Replace VulnerabilityRemediation.tsx**

Replace the full contents of `frontend/src/pages/VulnerabilityRemediation.tsx`:

```tsx
import { Navigate } from "react-router-dom";

export default function VulnerabilityRemediation() {
  return <Navigate to="/assets?preset=unmitigated_criticals" replace />;
}
```

- [ ] **Step 4: Run test to verify it passes**

```
cd frontend && npx jest src/pages/__tests__/VulnerabilityRemediationRedirect.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Add preset handling to Assets.tsx**

In `frontend/src/pages/Assets.tsx`, find the existing `useSearchParams` and `useEffect` block at the top of the `Assets` function. Add a preset initialization effect immediately after the existing state declarations:

```tsx
// Add this import at the top with the other imports:
// import { useNavigate, useSearchParams } from "react-router-dom"; // already imported

// Add PRESET_FILTERS constant before the Assets function:
const PRESET_FILTERS: Record<string, string> = {
  unmitigated_criticals: "criticality:critical",
  public_facing_high: "env:prod criticality:high",
};

// Inside the Assets function, after the existing useEffect blocks, add:
// Consume ?preset= param once on mount — apply filter and remove param from URL
useEffect(() => {
  const preset = searchParams.get("preset");
  if (preset && PRESET_FILTERS[preset]) {
    setInputValue(PRESET_FILTERS[preset]);
    setSearchParams({ search: PRESET_FILTERS[preset] }, { replace: true });
  }
}, []); // eslint-disable-line react-hooks/exhaustive-deps
```

- [ ] **Step 6: Run existing asset tests**

```
cd frontend && npx jest src/pages/__tests__/Assets.test.tsx --no-coverage 2>/dev/null || echo "no test file yet — manual verify in browser"
```

Expected: PASS or "no test file" (no regression).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/VulnerabilityRemediation.tsx frontend/src/pages/Assets.tsx
git commit -m "feat: redirect /remediation to /assets with unmitigated_criticals preset"
```

---

### Task 3: Pin onboarding checklist in sidebar until first connector is live

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

The backend already has a `/onboarding/checklist` endpoint (used by the Dashboard). The sidebar should query it and show a compact strip above the nav items when `connector_count === 0`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/__tests__/Sidebar.test.tsx`:

```tsx
test("shows onboarding strip when no connectors are configured", async () => {
  // Override the apiClient mock for this test to return no connectors
  const { apiClient } = require("../../api/client");
  apiClient.get.mockImplementation((url: string) => {
    if (url.includes("onboarding")) return Promise.resolve({
      data: { steps: [{ id: "connect", label: "Connect a data source", complete: false, detail: "" }], connector_count: 0, asset_count: 0 },
    });
    return Promise.resolve({ data: [] });
  });
  wrap(<Sidebar />);
  expect(await screen.findByText(/connect a data source/i)).toBeInTheDocument();
});

test("hides onboarding strip when connectors are configured", async () => {
  const { apiClient } = require("../../api/client");
  apiClient.get.mockImplementation((url: string) => {
    if (url.includes("onboarding")) return Promise.resolve({
      data: { steps: [], connector_count: 1, asset_count: 10 },
    });
    return Promise.resolve({ data: [] });
  });
  wrap(<Sidebar />);
  // Wait for query to settle
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.queryByText(/connect a data source/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend && npx jest src/components/__tests__/Sidebar.test.tsx --no-coverage
```

Expected: FAIL on the two new onboarding tests.

- [ ] **Step 3: Add onboarding query and strip to Sidebar.tsx**

In `frontend/src/components/Sidebar.tsx`, add an onboarding checklist query inside the `Sidebar` function (after the existing queries):

```tsx
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
const incompleteSteps = checklist?.steps.filter((s) => !s.complete) ?? [];
```

Then in the Sidebar JSX, add this strip immediately before the `<nav>` element:

```tsx
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/components/__tests__/Sidebar.test.tsx --no-coverage
```

Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/__tests__/Sidebar.test.tsx
git commit -m "feat: pin onboarding checklist strip in sidebar until first connector is live"
```

---

### Task 4: Smoke-test the navigation in the running app

**Files:** none (verification only)

- [ ] **Step 1: Restart the frontend container**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait 10 seconds for Vite to finish building.

- [ ] **Step 2: Verify sidebar structure**

Open http://100.101.186.39 (Tailscale IP) in a browser. Verify:
- Operations section is visible and collapsible
- Runbooks, Scheduled Ops, Maintenance Windows appear under Operations
- "Vuln Remediation" does NOT appear in the sidebar
- "Approvals Queue" does NOT appear in the sidebar
- "Change Requests" appears with an amber badge if any CRs are awaiting approval

- [ ] **Step 3: Verify /remediation redirect**

Navigate to `/remediation`. Verify URL changes to `/assets?...` with the criticality:critical filter applied and assets are pre-filtered.

- [ ] **Step 4: Commit if any final tweaks made**

```bash
git add -p && git commit -m "fix: nav smoke test tweaks"
```
