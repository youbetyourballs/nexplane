# UI Plan 5: Projects Chat Interface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI chat interface the primary way to start a new project. When an operator creates a new project, the AI panel opens automatically. The "AI Assistant" button becomes more prominent and the project creation form positions the chat as the natural next step.

**Architecture:** Changes are confined to `frontend/src/pages/ProjectDetail.tsx`. The `AIPanel` component (in `frontend/src/components/AIPanel.tsx`) already handles the full chat + proposed CRs workflow — we don't change it. The key change: after project creation, navigate with `?showAI=1` in the URL; on mount, detect that flag and open the AI panel immediately. Also, the "AI Assistant" button gets promoted to a primary action on new drafts.

**Tech Stack:** React, React Router v6, TanStack Query, Tailwind CSS

---

## File Map

- Modify: `frontend/src/pages/ProjectDetail.tsx` — auto-open AI panel after project creation, promote AI button

---

### Task 1: Auto-open AI panel after project creation

**Files:**
- Modify: `frontend/src/pages/ProjectDetail.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/ProjectDetail.autoAI.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockProject = {
  id: "proj1",
  name: "Harden prod servers",
  goal: "Apply CIS benchmarks",
  status: "draft",
  members: [],
  ai_context: [],
  created_at: new Date().toISOString(),
};

jest.mock("../../api/endpoints", () => ({
  projectsApi: {
    get: jest.fn().mockResolvedValue(mockProject),
    create: jest.fn(),
    update: jest.fn(),
    addMember: jest.fn(),
    removeMember: jest.fn(),
    updateMember: jest.fn(),
    aiChat: jest.fn(),
    getPromptPreview: jest.fn(),
  },
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]), create: jest.fn(), execute: jest.fn(), submitForApproval: jest.fn() },
  assetsApi: { list: jest.fn().mockResolvedValue([]) },
}));

import { ProjectDetail } from "../ProjectDetail";

function wrap(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("AI panel is open when URL has showAI=1", async () => {
  wrap("/projects/proj1?showAI=1");
  // AIPanel renders "AI Assistant" heading
  expect(await screen.findByText("✦ AI Assistant")).toBeInTheDocument();
});

test("AI panel is closed when URL has no showAI param", async () => {
  wrap("/projects/proj1");
  // Wait for project to load, then check AI panel is not open
  expect(await screen.findByText("Harden prod servers")).toBeInTheDocument();
  expect(screen.queryByText("✦ AI Assistant")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/ProjectDetail.autoAI.test.tsx --no-coverage
```

Expected: FAIL — AI panel not opening when `showAI=1`.

- [ ] **Step 3: Add showAI URL param detection to ProjectDetail.tsx**

In `frontend/src/pages/ProjectDetail.tsx`:

1. The `useSearchParams` hook is already imported. The existing code uses it to read `name` and `goal` on new projects.

2. Find the `showAIPanel` state declaration:

```tsx
const [showAIPanel, setShowAIPanel] = useState(false);
```

Replace it with:

```tsx
const [showAIPanel, setShowAIPanel] = useState(
  () => searchParams.get("showAI") === "1"
);
```

3. Find the `createProject` mutation's `onSuccess`:

```tsx
const createProject = useMutation({
  mutationFn: () => projectsApi.create({ name: name.trim(), goal: goal.trim() }),
  onSuccess: (p) => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    navigate(`/projects/${p.id}`, { replace: true });
  },
});
```

Replace the `navigate` call:

```tsx
onSuccess: (p) => {
  qc.invalidateQueries({ queryKey: ["projects"] });
  navigate(`/projects/${p.id}?showAI=1`, { replace: true });
},
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/ProjectDetail.autoAI.test.tsx --no-coverage
```

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ProjectDetail.tsx frontend/src/pages/__tests__/ProjectDetail.autoAI.test.tsx
git commit -m "feat: auto-open AI panel after project creation"
```

---

### Task 2: Promote AI Assistant button on new draft projects

**Files:**
- Modify: `frontend/src/pages/ProjectDetail.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/ProjectDetail.aiButton.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockDraftProject = {
  id: "proj1",
  name: "Test project",
  goal: "Some goal",
  status: "draft",
  members: [],
  ai_context: [],
  created_at: new Date().toISOString(),
};

jest.mock("../../api/endpoints", () => ({
  projectsApi: { get: jest.fn().mockResolvedValue(mockDraftProject), create: jest.fn(), update: jest.fn(), addMember: jest.fn(), removeMember: jest.fn(), updateMember: jest.fn(), aiChat: jest.fn(), getPromptPreview: jest.fn() },
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]), create: jest.fn(), execute: jest.fn(), submitForApproval: jest.fn() },
  assetsApi: { list: jest.fn().mockResolvedValue([]) },
}));

import { ProjectDetail } from "../ProjectDetail";

function wrap(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("AI Assistant button is visible and prominent on a draft project", async () => {
  wrap("/projects/proj1");
  const btn = await screen.findByRole("button", { name: /AI Assistant/i });
  expect(btn).toBeInTheDocument();
  // Should have a primary-style class indicating it's promoted
  expect(btn.className).toMatch(/bg-brand/);
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/ProjectDetail.aiButton.test.tsx --no-coverage
```

Expected: FAIL — AI Assistant button doesn't have `bg-brand` class (it's a secondary button currently).

- [ ] **Step 3: Promote the AI Assistant button styling in ProjectDetail.tsx**

Find the AI Assistant button in the ProjectDetail header section:

```tsx
{!isNew && isDraft && (
  <button
    onClick={() => setShowAIPanel(!showAIPanel)}
    className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors ${
      showAIPanel
        ? "bg-brand-50 border-brand-300 text-brand-700"
        : "border-slate-200 text-slate-600 hover:bg-slate-50"
    }`}
  >
    <Sparkles className="w-3.5 h-3.5" />
    AI Assistant
  </button>
)}
```

Replace with:

```tsx
{!isNew && isDraft && (
  <button
    onClick={() => setShowAIPanel(!showAIPanel)}
    className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
      showAIPanel
        ? "bg-brand-700 text-white"
        : "bg-brand-600 text-white hover:bg-brand-700"
    }`}
  >
    <Sparkles className="w-3.5 h-3.5" />
    AI Assistant
  </button>
)}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/ProjectDetail.aiButton.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ProjectDetail.tsx frontend/src/pages/__tests__/ProjectDetail.aiButton.test.tsx
git commit -m "feat: promote AI Assistant button to primary on draft projects"
```

---

### Task 3: Add AI-first prompt to new project form on Projects list page

**Files:**
- Modify: `frontend/src/pages/Projects.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/pages/__tests__/Projects.newProjectHint.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  projectsApi: { list: jest.fn().mockResolvedValue([]) },
}));

import { Projects } from "../Projects";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("empty state mentions AI assistance", async () => {
  wrap(<Projects />);
  expect(await screen.findByText(/AI/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

```
cd frontend && npx jest src/pages/__tests__/Projects.newProjectHint.test.tsx --no-coverage
```

Expected: FAIL — no AI mention in empty state.

- [ ] **Step 3: Update empty state in Projects.tsx**

In `frontend/src/pages/Projects.tsx`, find the empty state JSX:

```tsx
<div className="flex flex-col items-center justify-center py-24 text-center">
  <FolderOpen className="w-12 h-12 text-slate-300 mb-4" />
  <p className="text-slate-500 text-sm">No projects yet.</p>
  <div className="flex gap-3 mt-4">
    <button
      onClick={() => setShowTemplates(true)}
      className="text-brand-600 text-sm hover:underline"
    >
      Start from a template
    </button>
    <span className="text-slate-300">·</span>
    <button
      onClick={() => navigate("/projects/new")}
      className="text-brand-600 text-sm hover:underline"
    >
      Create blank project
    </button>
  </div>
</div>
```

Replace with:

```tsx
<div className="flex flex-col items-center justify-center py-24 text-center max-w-sm mx-auto">
  <FolderOpen className="w-12 h-12 text-slate-300 mb-4" />
  <p className="text-slate-700 text-sm font-medium">No projects yet</p>
  <p className="text-slate-400 text-xs mt-1 mb-6">
    Describe a goal to the AI and it will propose the change requests needed to achieve it.
  </p>
  <button
    onClick={() => navigate("/projects/new")}
    className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
  >
    <Sparkles className="w-4 h-4" />
    Start with AI
  </button>
  <div className="flex gap-3 mt-4">
    <button
      onClick={() => setShowTemplates(true)}
      className="text-slate-500 text-xs hover:text-slate-700 hover:underline"
    >
      Use a template
    </button>
    <span className="text-slate-300 text-xs">·</span>
    <button
      onClick={() => navigate("/projects/new")}
      className="text-slate-500 text-xs hover:text-slate-700 hover:underline"
    >
      Blank project
    </button>
  </div>
</div>
```

Also add `Sparkles` to the imports from lucide-react at the top of `Projects.tsx`:

```tsx
import { Plus, FolderOpen, LayoutTemplate, X, Sparkles } from "lucide-react";
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && npx jest src/pages/__tests__/Projects.newProjectHint.test.tsx --no-coverage
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Projects.tsx frontend/src/pages/__tests__/Projects.newProjectHint.test.tsx
git commit -m "feat: update project empty state to lead with AI-first messaging"
```

---

### Task 4: Browser verification of full project chat flow

- [ ] **Step 1: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 2: Verify new project flow**

Open http://100.101.186.39/projects. Verify:
1. Empty state shows "Start with AI" as the primary button
2. Clicking "Start with AI" navigates to /projects/new
3. Filling in a project name and clicking "Create Project" navigates to /projects/{id}?showAI=1
4. On that page, the AI panel is immediately visible and open (not hidden behind a button)
5. The "AI Assistant" button is blue/primary colored, not grey/secondary

- [ ] **Step 3: Verify AI panel works end-to-end**

In the AI panel, type a message like "List my connected assets". Verify:
1. Message sends and the AI responds
2. If the AI returns proposed CRs, they appear in the Proposed Changes section
3. "Add to Project" buttons work on proposed CRs

- [ ] **Step 4: Verify existing projects are unaffected**

Navigate to an existing project (without `?showAI=1`). Verify:
1. AI panel is closed by default
2. "AI Assistant" button is visible and blue
3. Clicking it opens the panel

- [ ] **Step 5: Commit any tweaks**

```bash
git add -p && git commit -m "fix: project chat flow tweaks from browser verification"
```
