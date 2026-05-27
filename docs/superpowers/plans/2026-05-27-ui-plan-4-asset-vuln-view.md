# UI Plan 4: Asset Vulnerability View

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add filter presets (Unmitigated Criticals, Public-Facing High+), a vulnerability class filter dropdown, and a public exploit toggle to the Assets page. The presets apply pre-defined filter combinations with a single click.

**Architecture:** All changes in `frontend/src/pages/Assets.tsx`. Filter presets are client-side URL param shortcuts. Vuln class and exploit filters map to search token syntax (vuln_class:rce, exploit:true) — the backend already accepts arbitrary filter params via the `q` field. Note: vuln_class and exploit filters will only return results if the connected scanner connectors provide this data in asset metadata. The UI shows these filters regardless; empty results are expected when scanner data is absent.

**Tech Stack:** React, React Router v6, TanStack Query, Tailwind CSS

---

## File Map

- Modify: `frontend/src/pages/Assets.tsx` — add preset buttons row, vuln_class dropdown, exploit available toggle

---

### Task 1: Filter preset buttons

**Files:**
- Modify: `frontend/src/pages/Assets.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Assets.presets.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  assetsApi: {
    list: jest.fn().mockResolvedValue([]),
    tags: jest.fn().mockResolvedValue([]),
    create: jest.fn(),
    delete: jest.fn(),
    bulkTag: jest.fn(),
  },
  connectorsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: { post: jest.fn() },
}));

import { Assets } from "../Assets";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows Unmitigated Criticals preset button", () => {
  wrap(<Assets />);
  expect(screen.getByText("Unmitigated Criticals")).toBeInTheDocument();
});

test("shows Public-Facing High+ preset button", () => {
  wrap(<Assets />);
  expect(screen.getByText("Public-Facing High+")).toBeInTheDocument();
});

test("clicking Unmitigated Criticals applies criticality filter", () => {
  wrap(<Assets />);
  const btn = screen.getByText("Unmitigated Criticals");
  fireEvent.click(btn);
  // After click, filter should be applied — assetsApi.list should have been called
  // with criticality:critical in the params (checked via input value)
  const input = screen.getByPlaceholderText(/search assets/i) as HTMLInputElement;
  expect(input.value).toContain("critical");
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Assets.presets.test.tsx --no-coverage
```

Expected: FAIL — "Unmitigated Criticals" not found.

- [ ] **Step 3: Add preset constants and preset button row to Assets.tsx**

In `frontend/src/pages/Assets.tsx`, add the preset definitions before the `Assets` function (after the existing `parseSearch`/`buildSearchString` helpers):

```tsx
interface FilterPreset {
  label: string;
  search: string;
  description: string;
}

const FILTER_PRESETS: FilterPreset[] = [
  {
    label: "Unmitigated Criticals",
    search: "criticality:critical",
    description: "Critical severity assets",
  },
  {
    label: "Public-Facing High+",
    search: "env:prod criticality:high",
    description: "Production assets rated high or critical",
  },
  {
    label: "Public Exploit Available",
    search: "exploit:true",
    description: "Assets with known public exploits (requires scanner data)",
  },
];
```

Inside the `Assets` function, find the `// Derive filter params from URL` comment. Add a `activePreset` derivation below it:

```tsx
const activePreset = FILTER_PRESETS.find((p) => p.search === rawSearch)?.label ?? null;
```

In the return JSX, add a preset buttons row immediately before the `{/* Search bar + filter dropdowns */}` div:

```tsx
{/* Filter presets */}
<div className="mb-3 flex flex-wrap gap-2">
  {FILTER_PRESETS.map((preset) => (
    <button
      key={preset.label}
      onClick={() => {
        if (activePreset === preset.label) {
          setInputValue("");
        } else {
          setInputValue(preset.search);
        }
      }}
      title={preset.description}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-full border transition-colors ${
        activePreset === preset.label
          ? "bg-brand-600 text-white border-brand-600"
          : "bg-white text-slate-600 border-slate-300 hover:border-brand-400 hover:text-brand-700"
      }`}
    >
      {preset.label}
      {activePreset === preset.label && <span className="ml-1">×</span>}
    </button>
  ))}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/Assets.presets.test.tsx --no-coverage
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Assets.tsx frontend/src/pages/__tests__/Assets.presets.test.tsx
git commit -m "feat: add filter preset buttons to asset inventory (Unmitigated Criticals, Public-Facing High+)"
```

---

### Task 2: Vulnerability class filter dropdown

**Files:**
- Modify: `frontend/src/pages/Assets.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Assets.vulnClass.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  assetsApi: { list: jest.fn().mockResolvedValue([]), tags: jest.fn().mockResolvedValue([]), create: jest.fn(), delete: jest.fn(), bulkTag: jest.fn() },
  connectorsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({ apiClient: { post: jest.fn() } }));

import { Assets } from "../Assets";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows vuln class dropdown", () => {
  wrap(<Assets />);
  expect(screen.getByRole("combobox", { name: /vuln class/i })).toBeInTheDocument();
});

test("vuln class dropdown has RCE option", () => {
  wrap(<Assets />);
  const select = screen.getByRole("combobox", { name: /vuln class/i });
  expect(select).toContainHTML("rce");
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Assets.vulnClass.test.tsx --no-coverage
```

Expected: FAIL — vuln class combobox not found.

- [ ] **Step 3: Add vuln_class to parseSearch KEY_MAP and add dropdown**

In `frontend/src/pages/Assets.tsx`, update `parseSearch` to handle `vuln_class`:

```tsx
// In parseSearch KEY_MAP, add:
const KEY_MAP: Record<string, string> = {
  env: "env",
  type: "asset_type",
  criticality: "criticality",
  tag: "tag",
  connector_id: "connector_id",
  vuln_class: "vuln_class",  // add this
  exploit: "exploit",         // add this
};
```

Also update `buildSearchString` REVERSE_MAP:

```tsx
const REVERSE_MAP: Record<string, string> = {
  env: "env",
  asset_type: "type",
  criticality: "criticality",
  tag: "tag",
  connector_id: "connector_id",
  vuln_class: "vuln_class",  // add this
  exploit: "exploit",         // add this
};
```

Then add the vuln class dropdown in the filter row JSX (after the existing criticality dropdown, before the connector dropdown):

```tsx
<select
  aria-label="Vuln class"
  value={filters.vuln_class ?? ""}
  onChange={(e) => setFilter("vuln_class", e.target.value)}
  className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
>
  <option value="">All Vuln Classes</option>
  <option value="rce">Remote Code Execution</option>
  <option value="cmd_injection">Command Injection</option>
  <option value="lpe">Local Privilege Escalation</option>
  <option value="auth_bypass">Auth Bypass</option>
  <option value="info_disclosure">Info Disclosure</option>
  <option value="dos">Denial of Service</option>
</select>
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/Assets.vulnClass.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Assets.tsx frontend/src/pages/__tests__/Assets.vulnClass.test.tsx
git commit -m "feat: add vulnerability class filter to asset inventory"
```

---

### Task 3: Public exploit available toggle

**Files:**
- Modify: `frontend/src/pages/Assets.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Assets.exploit.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  assetsApi: { list: jest.fn().mockResolvedValue([]), tags: jest.fn().mockResolvedValue([]), create: jest.fn(), delete: jest.fn(), bulkTag: jest.fn() },
  connectorsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({ apiClient: { post: jest.fn() } }));

import { Assets } from "../Assets";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows public exploit toggle button", () => {
  wrap(<Assets />);
  expect(screen.getByRole("button", { name: /public exploit/i })).toBeInTheDocument();
});

test("clicking exploit toggle updates the search filter", () => {
  wrap(<Assets />);
  const btn = screen.getByRole("button", { name: /public exploit/i });
  fireEvent.click(btn);
  const input = screen.getByPlaceholderText(/search assets/i) as HTMLInputElement;
  expect(input.value).toContain("exploit:true");
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Assets.exploit.test.tsx --no-coverage
```

Expected: FAIL — exploit toggle not found.

- [ ] **Step 3: Add exploit toggle to filter row in Assets.tsx**

In `frontend/src/pages/Assets.tsx`, add the exploit toggle in the filter row JSX — after the vuln class dropdown added in Task 2, before the tag input:

```tsx
{/* Exploit available toggle */}
<button
  aria-label={filters.exploit === "true" ? "Public exploit filter active, click to clear" : "Filter by public exploit available"}
  onClick={() => setFilter("exploit", filters.exploit === "true" ? "" : "true")}
  title="Filter to assets with known public exploits (requires scanner data)"
  className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border transition-colors ${
    filters.exploit === "true"
      ? "bg-red-600 text-white border-red-600"
      : "border-slate-200 text-slate-600 hover:border-red-300 hover:text-red-600"
  }`}
>
  <span className="text-xs font-medium">⚡ Public Exploit</span>
</button>
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/Assets.exploit.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Assets.tsx frontend/src/pages/__tests__/Assets.exploit.test.tsx
git commit -m "feat: add public exploit available toggle to asset filters"
```

---

### Task 4: Browser verification

- [ ] **Step 1: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 2: Verify asset filter UI**

Open http://100.101.186.39/assets. Verify:
1. Three preset buttons appear above the filter row ("Unmitigated Criticals", "Public-Facing High+", "Public Exploit Available")
2. Clicking "Unmitigated Criticals" applies `criticality:critical` filter — assets filter to critical-only
3. Clicking an active preset again clears the filter
4. "Vuln Class" dropdown appears in filter row with correct options
5. "⚡ Public Exploit" toggle appears and toggles `exploit:true` in the search

- [ ] **Step 3: Verify /remediation redirect still works**

Navigate to `/remediation`. Confirm it redirects to `/assets` with the "Unmitigated Criticals" preset button highlighted.

- [ ] **Step 4: Commit any tweaks**

```bash
git add -p && git commit -m "fix: asset vuln view tweaks from browser verification"
```
