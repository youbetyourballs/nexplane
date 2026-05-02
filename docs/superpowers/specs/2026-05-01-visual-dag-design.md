# Sub-project C: Visual Dependency DAG — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Node-graph visualization of project dependencies in Project Detail. Nodes are change requests (colored by status) and assets (colored by type). Edges are CR→CR dependency relationships and CR→Asset target relationships. Replaces the list-only view with a tab that toggles between list and graph.

---

## Design Decisions

- **React Flow:** Best-maintained React graph library. Supports drag, zoom, pan, custom nodes. No D3 needed.
- **Two node types:** CRs (rectangular, status-colored) and Assets (rounded, type-colored). Keeps the graph informative without overwhelming.
- **Two edge types:** CR→CR dependency edges (solid, labeled "depends on") and CR→Asset edges (dashed, labeled "targets").
- **Tab toggle:** "List" and "Graph" tabs added to Project Detail. List view unchanged. Graph view is additive.
- **No backend changes:** All data already available — CRs from project members, `depends_on` from `ProjectChangeRequest.depends_on`, asset links from `ChangeRequest.asset_ids`.
- **Auto-layout:** Dagre layout algorithm (via `@dagrejs/dagre` + `reactflow`) for automatic left-to-right DAG positioning on load. User can then drag nodes freely.

---

## Data Flow

```
ProjectDetail loads:
  - project.members → list of ProjectChangeRequest
  - each member.change_request → ChangeRequest (with asset_ids, status, title)
  - each member.depends_on → list of change_request_ids (edges)

GraphView transforms this into:
  - CR nodes: id=cr.id, label=cr.title, status=cr.status
  - Asset nodes: id=asset.id, label=asset.name, type=asset.asset_type (deduplicated)
  - CR→CR edges: from depends_on array
  - CR→Asset edges: from change_request.asset_ids

Asset details fetched via GET /assets/{id} for each unique asset_id across all CRs.
```

---

## Node Visual Design

**CR nodes** (100×60px, rounded corners):

| Status | Color |
|--------|-------|
| draft | gray (`bg-gray-100 border-gray-300`) |
| pending_approval | amber (`bg-amber-50 border-amber-400`) |
| approved | blue (`bg-blue-50 border-blue-400`) |
| executing | indigo (`bg-indigo-50 border-indigo-400`) |
| completed | green (`bg-green-50 border-green-400`) |
| failed | red (`bg-red-50 border-red-400`) |
| rolled_back | orange (`bg-orange-50 border-orange-400`) |

Shows: CR title (truncated to 2 lines), status badge, connector type icon.

**Asset nodes** (80×50px, pill shape):

| Asset type | Color |
|------------|-------|
| server | slate |
| identity | purple |
| application | cyan |
| firewall | orange |
| cloud_account | sky |
| network | teal |

Shows: asset name (truncated), type label.

**Edges:**
- CR→CR: solid dark gray arrow, label "depends on" (small, gray)
- CR→Asset: dashed teal arrow, no label

---

## Component Structure

```
frontend/src/
  components/
    ProjectGraph/
      index.tsx              # Main graph component, exported as <ProjectGraph />
      CRNode.tsx             # Custom CR node renderer
      AssetNode.tsx          # Custom Asset node renderer
      useGraphLayout.ts      # Dagre layout hook: transforms project data → RF nodes+edges
      graphUtils.ts          # Color maps, node/edge builders
```

**`ProjectGraph` props:**
```typescript
interface ProjectGraphProps {
  project: ProjectRead;
  members: ProjectChangeRequestRead[];  // includes nested change_request
}
```

**`useGraphLayout` hook:**
```typescript
function useGraphLayout(members: ProjectChangeRequestRead[]): {
  nodes: Node[];
  edges: Edge[];
  loading: boolean;
}
```
Fetches asset details for all unique `asset_ids` across CRs, then builds the graph using Dagre for initial positioning.

---

## Integration in `ProjectDetail.tsx`

Add two tabs below the project header:

```tsx
const [view, setView] = useState<'list' | 'graph'>('list');

// Tab buttons
<button onClick={() => setView('list')} className={view === 'list' ? 'active' : ''}>List</button>
<button onClick={() => setView('graph')} className={view === 'graph' ? 'active' : ''}>Graph</button>

// Content
{view === 'list' ? <ExistingListView /> : <ProjectGraph project={project} members={members} />}
```

The graph renders inside a fixed-height container (600px, full width) with React Flow controls (zoom in/out, fit view, minimap).

---

## New Dependencies

```json
"reactflow": "^11.11.0",
"@dagrejs/dagre": "^1.1.4"
```

---

## New / Modified Files

| File | Purpose |
|------|---------|
| `frontend/src/components/ProjectGraph/index.tsx` | Main graph component |
| `frontend/src/components/ProjectGraph/CRNode.tsx` | Custom CR node |
| `frontend/src/components/ProjectGraph/AssetNode.tsx` | Custom Asset node |
| `frontend/src/components/ProjectGraph/useGraphLayout.ts` | Dagre layout + data transformation hook |
| `frontend/src/components/ProjectGraph/graphUtils.ts` | Color maps, type definitions |
| `frontend/src/pages/ProjectDetail.tsx` | Add List/Graph tab toggle |
| `frontend/package.json` | Add reactflow + dagre deps |
