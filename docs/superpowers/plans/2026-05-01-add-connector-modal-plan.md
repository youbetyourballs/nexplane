# Add Connector Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Add Connector" button to the Connectors page that opens a modal for creating a new connector, then flows directly into the CredentialModal.

**Architecture:** New `AddConnectorModal` component handles the two-field form (type + name) and calls `connectorsApi.create`. On success it calls `onCreated(connector)`, which the parent uses to immediately open the existing `CredentialModal`. No backend changes — `POST /connectors` and `connectorsApi.create` already exist.

**Tech Stack:** React 18, TypeScript, TanStack Query (`useMutation`), Tailwind CSS.

**Working directory:** `f:\Nexplane\nexplane`

---

### Task 1: Create `AddConnectorModal` component

**Files:**
- Create: `frontend/src/components/AddConnectorModal.tsx`

- [ ] **Step 1: Create `frontend/src/components/AddConnectorModal.tsx`**

```tsx
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { connectorsApi } from "../api/endpoints";
import type { ConnectorRead, ConnectorType } from "../types/api";

const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  aws_mock: "AWS",
  azure_mock: "Azure",
  cloudflare_mock: "Cloudflare",
  okta_mock: "Okta",
  paloalto_mock: "Palo Alto",
  ssh_runner_mock: "SSH Runner",
  active_directory_mock: "Active Directory",
  crowdstrike_mock: "CrowdStrike Falcon",
  tenable_mock: "Tenable",
  nexplane_agent: "Nexplane Agent",
};

const CONNECTOR_ICONS: Record<ConnectorType, string> = {
  aws_mock: "☁️",
  azure_mock: "🔷",
  cloudflare_mock: "🟠",
  okta_mock: "🔐",
  paloalto_mock: "🛡️",
  ssh_runner_mock: "🖥️",
  active_directory_mock: "🏢",
  crowdstrike_mock: "🦅",
  tenable_mock: "🔍",
  nexplane_agent: "🤖",
};

const ALL_TYPES = Object.keys(CONNECTOR_LABELS) as ConnectorType[];

interface Props {
  token: string;
  onClose: () => void;
  onCreated: (connector: ConnectorRead) => void;
}

export default function AddConnectorModal({ token, onClose, onCreated }: Props) {
  const [connectorType, setConnectorType] = useState<ConnectorType>("aws_mock");
  const [name, setName] = useState<string>(CONNECTOR_LABELS["aws_mock"]);

  function handleTypeChange(type: ConnectorType) {
    setConnectorType(type);
    setName(CONNECTOR_LABELS[type]);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      connectorsApi.create({
        connector_type: connectorType,
        name: name.trim(),
        scoped_permissions: {},
      }),
    onSuccess: (newConnector) => {
      onCreated(newConnector);
      onClose();
    },
  });

  const canSubmit = name.trim().length > 0 && !createMutation.isPending;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold text-slate-900 mb-1">Add Connector</h2>
        <p className="text-sm text-slate-500 mb-5">
          Choose a connector type and give it a name. You'll configure credentials next.
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Connector Type
            </label>
            <select
              value={connectorType}
              onChange={(e) => handleTypeChange(e.target.value as ConnectorType)}
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              {ALL_TYPES.map((type) => (
                <option key={type} value={type}>
                  {CONNECTOR_ICONS[type]} {CONNECTOR_LABELS[type]}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Display Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Production AWS"
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        {createMutation.isError && (
          <p className="text-sm text-red-500 mt-3">
            Failed to create connector. Please try again.
          </p>
        )}

        <div className="flex gap-3 mt-6">
          <button
            onClick={() => createMutation.mutate()}
            disabled={!canSubmit}
            className="flex-1 bg-indigo-600 text-white rounded-md py-2 text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {createMutation.isPending ? "Adding…" : "Add Connector"}
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```
cd frontend && npx tsc --noEmit 2>&1 | grep AddConnectorModal
```

Expected: no output (no errors for this file)

- [ ] **Step 3: Commit**

```
git add frontend/src/components/AddConnectorModal.tsx
git commit -m "feat(connectors): add AddConnectorModal component"
```

---

### Task 2: Wire `AddConnectorModal` into `Connectors.tsx`

**Files:**
- Modify: `frontend/src/pages/Connectors.tsx`

- [ ] **Step 1: Add the import at the top of `Connectors.tsx`**

After the existing `import ScheduleModal from "../components/ScheduleModal";` line, add:

```tsx
import AddConnectorModal from "../components/AddConnectorModal";
```

- [ ] **Step 2: Add `addModalOpen` state inside the `Connectors` function**

After the existing state declarations (after line with `scheduleModal` state), add:

```tsx
const [addModalOpen, setAddModalOpen] = useState(false);
```

- [ ] **Step 3: Add the "Add Connector" button to the `PageHeader`**

The current `PageHeader` render is:
```tsx
<PageHeader
  title="Connectors"
  subtitle="Mock connectors for infrastructure target systems"
/>
```

Replace it with:
```tsx
<div className="flex items-center justify-between mb-6">
  <PageHeader
    title="Connectors"
    subtitle="Integrations with infrastructure target systems"
  />
  <button
    onClick={() => setAddModalOpen(true)}
    className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700"
  >
    <span className="text-base leading-none">+</span>
    Add Connector
  </button>
</div>
```

- [ ] **Step 4: Add `AddConnectorModal` render alongside the other modals**

After the closing `)}` of the `ScheduleModal` block (end of the component), add before the final closing `</div>`:

```tsx
{addModalOpen && (
  <AddConnectorModal
    token={token}
    onClose={() => setAddModalOpen(false)}
    onCreated={(connector) => {
      setAddModalOpen(false);
      qc.invalidateQueries({ queryKey: ["connectors"] });
      setCredModalConnector(connector);
    }}
  />
)}
```

- [ ] **Step 5: Build frontend to verify no TypeScript errors**

```
cd frontend && npm run build 2>&1 | tail -15
```

Expected: Build succeeds. Fix any TypeScript errors before proceeding — there should be none from these changes.

- [ ] **Step 6: Run backend tests to confirm nothing broke**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

Expected: 116 passed

- [ ] **Step 7: Commit**

```
git add frontend/src/pages/Connectors.tsx
git commit -m "feat(connectors): add Add Connector button and wire AddConnectorModal with credential handoff"
```
