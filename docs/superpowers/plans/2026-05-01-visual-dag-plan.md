# Visual Dependency DAG Implementation Plan (Sub-project C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a node-graph view to Project Detail showing change requests (colored by status) and their target assets as nodes, with dependency and target edges, using React Flow and Dagre auto-layout.

**Architecture:** New `ProjectGraph/` component directory. `useGraphLayout` hook transforms existing project data into React Flow nodes+edges. Tab toggle (List/Graph) added to `ProjectDetail.tsx`. No backend changes.

**Tech Stack:** React Flow 11, @dagrejs/dagre, TanStack Query (already in project).

**Working directory:** `f:\Nexplane\nexplane\.worktrees\remaining-features`

---

### Task 1: Install React Flow and Dagre

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install dependencies**

```
cd frontend && npm install reactflow @dagrejs/dagre
```

Expected: both packages added to `node_modules/`

- [ ] **Step 2: Verify no peer dependency errors**

```
cd frontend && npm ls reactflow 2>&1 | head -5
```

- [ ] **Step 3: Commit**

```
git add frontend/package.json frontend/package-lock.json
git commit -m "feat(dag): install reactflow and dagre dependencies"
```

---

### Task 2: Graph utilities and type definitions

**Files:**
- Create: `frontend/src/components/ProjectGraph/graphUtils.ts`

- [ ] **Step 1: Create `frontend/src/components/ProjectGraph/graphUtils.ts`**

```typescript
import type { Node, Edge } from 'reactflow';
import dagre from '@dagrejs/dagre';

// Color maps
export const CR_STATUS_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  draft:            { bg: '#f9fafb', border: '#d1d5db', text: '#374151' },
  pending_approval: { bg: '#fffbeb', border: '#f59e0b', text: '#92400e' },
  approved:         { bg: '#eff6ff', border: '#3b82f6', text: '#1e40af' },
  in_review:        { bg: '#eff6ff', border: '#3b82f6', text: '#1e40af' },
  executing:        { bg: '#eef2ff', border: '#6366f1', text: '#3730a3' },
  completed:        { bg: '#f0fdf4', border: '#22c55e', text: '#14532d' },
  verified:         { bg: '#f0fdf4', border: '#22c55e', text: '#14532d' },
  failed:           { bg: '#fef2f2', border: '#ef4444', text: '#7f1d1d' },
  rolled_back:      { bg: '#fff7ed', border: '#f97316', text: '#7c2d12' },
};

export const ASSET_TYPE_COLORS: Record<string, { bg: string; border: string }> = {
  server:        { bg: '#f8fafc', border: '#64748b' },
  identity:      { bg: '#faf5ff', border: '#a855f7' },
  application:   { bg: '#ecfeff', border: '#06b6d4' },
  firewall:      { bg: '#fff7ed', border: '#f97316' },
  cloud_account: { bg: '#f0f9ff', border: '#0ea5e9' },
  network:       { bg: '#f0fdfa', border: '#14b8a6' },
  database:      { bg: '#fff1f2', border: '#f43f5e' },
};

export const NODE_WIDTH = 200;
export const CR_NODE_HEIGHT = 70;
export const ASSET_NODE_HEIGHT = 50;

export function applyDagreLayout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 80 });

  nodes.forEach(node => {
    const height = node.type === 'assetNode' ? ASSET_NODE_HEIGHT : CR_NODE_HEIGHT;
    g.setNode(node.id, { width: NODE_WIDTH, height });
  });

  edges.forEach(edge => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  return nodes.map(node => {
    const { x, y } = g.node(node.id);
    const height = node.type === 'assetNode' ? ASSET_NODE_HEIGHT : CR_NODE_HEIGHT;
    return {
      ...node,
      position: { x: x - NODE_WIDTH / 2, y: y - height / 2 },
    };
  });
}
```

- [ ] **Step 2: Build to verify no TypeScript errors**

```
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: Commit**

```
git add frontend/src/components/ProjectGraph/graphUtils.ts
git commit -m "feat(dag): add graph utilities and Dagre layout helper"
```

---

### Task 3: Custom node components

**Files:**
- Create: `frontend/src/components/ProjectGraph/CRNode.tsx`
- Create: `frontend/src/components/ProjectGraph/AssetNode.tsx`

- [ ] **Step 1: Create `frontend/src/components/ProjectGraph/CRNode.tsx`**

```tsx
import React from 'react';
import { Handle, Position } from 'reactflow';
import type { NodeProps } from 'reactflow';
import { CR_STATUS_COLORS } from './graphUtils';

interface CRNodeData {
  title: string;
  status: string;
  connectorType: string;
  changeType: string;
}

export default function CRNode({ data }: NodeProps<CRNodeData>) {
  const colors = CR_STATUS_COLORS[data.status] ?? CR_STATUS_COLORS.draft;

  return (
    <>
      <Handle type="target" position={Position.Left} className="!w-2 !h-2 !bg-gray-400" />
      <div
        style={{
          background: colors.bg,
          borderColor: colors.border,
          color: colors.text,
          width: 200,
        }}
        className="rounded-lg border-2 px-3 py-2 shadow-sm"
      >
        <div className="text-xs font-semibold truncate" title={data.title}>
          {data.title}
        </div>
        <div className="flex items-center gap-1 mt-1">
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
            style={{ background: colors.border + '22', color: colors.text }}
          >
            {data.status.replace(/_/g, ' ')}
          </span>
          <span className="text-[10px] text-gray-400 truncate">{data.connectorType?.replace('_mock', '')}</span>
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!w-2 !h-2 !bg-gray-400" />
    </>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/ProjectGraph/AssetNode.tsx`**

```tsx
import React from 'react';
import { Handle, Position } from 'reactflow';
import type { NodeProps } from 'reactflow';
import { ASSET_TYPE_COLORS } from './graphUtils';

interface AssetNodeData {
  name: string;
  assetType: string;
}

export default function AssetNode({ data }: NodeProps<AssetNodeData>) {
  const colors = ASSET_TYPE_COLORS[data.assetType] ?? { bg: '#f8fafc', border: '#94a3b8' };

  return (
    <>
      <Handle type="target" position={Position.Left} className="!w-1.5 !h-1.5 !bg-gray-300" />
      <div
        style={{ background: colors.bg, borderColor: colors.border, width: 200 }}
        className="rounded-full border-2 px-4 py-2 shadow-sm"
      >
        <div className="text-xs font-medium text-gray-700 truncate text-center" title={data.name}>
          {data.name}
        </div>
        <div className="text-[10px] text-gray-400 text-center">{data.assetType}</div>
      </div>
    </>
  );
}
```

- [ ] **Step 3: Commit**

```
git add frontend/src/components/ProjectGraph/CRNode.tsx frontend/src/components/ProjectGraph/AssetNode.tsx
git commit -m "feat(dag): add custom CRNode and AssetNode components"
```

---

### Task 4: `useGraphLayout` hook

**Files:**
- Create: `frontend/src/components/ProjectGraph/useGraphLayout.ts`

- [ ] **Step 1: Create `frontend/src/components/ProjectGraph/useGraphLayout.ts`**

```typescript
import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import type { Node, Edge } from 'reactflow';
import { applyDagreLayout } from './graphUtils';

interface ProjectMember {
  id: string;
  sequence_order: number;
  depends_on: string[];
  change_request: {
    id: string;
    title: string;
    status: string;
    change_type: string;
    asset_ids: string[];
    connector?: { connector_type: string };
  };
}

interface Asset {
  id: string;
  name: string;
  asset_type: string;
}

export function useGraphLayout(members: ProjectMember[], token: string) {
  // Collect all unique asset IDs across all CRs
  const allAssetIds = useMemo(() => {
    const ids = new Set<string>();
    members.forEach(m => m.change_request.asset_ids?.forEach(id => ids.add(id)));
    return Array.from(ids);
  }, [members]);

  // Fetch asset details for each unique asset
  const assetQueries = useQueries({
    queries: allAssetIds.map(assetId => ({
      queryKey: ['asset', assetId],
      queryFn: () =>
        fetch(`/assets/${assetId}`, {
          headers: { Authorization: `Bearer ${token}` },
        }).then(r => r.ok ? r.json() as Promise<Asset> : null),
      staleTime: 5 * 60 * 1000,
    })),
  });

  const assets = useMemo(() => {
    const map = new Map<string, Asset>();
    assetQueries.forEach(q => {
      if (q.data) map.set(q.data.id, q.data);
    });
    return map;
  }, [assetQueries]);

  const loading = assetQueries.some(q => q.isLoading);

  const { nodes, edges } = useMemo(() => {
    if (loading || members.length === 0) return { nodes: [], edges: [] };

    const nodes: Node[] = [];
    const edges: Edge[] = [];
    const crIdToMemberId = new Map<string, string>();

    // CR nodes
    members.forEach(member => {
      const cr = member.change_request;
      const nodeId = `cr-${cr.id}`;
      crIdToMemberId.set(cr.id, nodeId);
      nodes.push({
        id: nodeId,
        type: 'crNode',
        position: { x: 0, y: 0 }, // Dagre will set this
        data: {
          title: cr.title,
          status: cr.status,
          changeType: cr.change_type,
          connectorType: cr.connector?.connector_type ?? '',
        },
      });
    });

    // Asset nodes (deduplicated)
    const addedAssets = new Set<string>();
    members.forEach(member => {
      member.change_request.asset_ids?.forEach(assetId => {
        const asset = assets.get(assetId);
        const nodeId = `asset-${assetId}`;
        if (!addedAssets.has(assetId)) {
          addedAssets.add(assetId);
          nodes.push({
            id: nodeId,
            type: 'assetNode',
            position: { x: 0, y: 0 },
            data: {
              name: asset?.name ?? assetId.slice(0, 8),
              assetType: asset?.asset_type ?? 'server',
            },
          });
        }
        // CR → Asset edge
        edges.push({
          id: `e-cr-${member.change_request.id}-asset-${assetId}`,
          source: `cr-${member.change_request.id}`,
          target: nodeId,
          type: 'smoothstep',
          style: { strokeDasharray: '5,5', stroke: '#14b8a6', strokeWidth: 1.5 },
          label: 'targets',
          labelStyle: { fontSize: 10, fill: '#94a3b8' },
        });
      });
    });

    // CR → CR dependency edges (depends_on holds change_request_ids)
    members.forEach(member => {
      member.depends_on?.forEach(depId => {
        const sourceId = crIdToMemberId.get(depId);
        if (sourceId) {
          edges.push({
            id: `e-dep-${depId}-${member.change_request.id}`,
            source: sourceId,
            target: `cr-${member.change_request.id}`,
            type: 'smoothstep',
            style: { stroke: '#6b7280', strokeWidth: 2 },
            markerEnd: { type: 'arrowclosed' as const },
            label: 'depends on',
            labelStyle: { fontSize: 10, fill: '#9ca3af' },
          });
        }
      });
    });

    const laidOutNodes = applyDagreLayout(nodes, edges);
    return { nodes: laidOutNodes, edges };
  }, [members, assets, loading]);

  return { nodes, edges, loading };
}
```

- [ ] **Step 2: Commit**

```
git add frontend/src/components/ProjectGraph/useGraphLayout.ts
git commit -m "feat(dag): add useGraphLayout hook with Dagre auto-positioning"
```

---

### Task 5: `ProjectGraph` component and `ProjectDetail` tab integration

**Files:**
- Create: `frontend/src/components/ProjectGraph/index.tsx`
- Modify: `frontend/src/pages/ProjectDetail.tsx`

- [ ] **Step 1: Create `frontend/src/components/ProjectGraph/index.tsx`**

```tsx
import React, { useCallback } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
} from 'reactflow';
import 'reactflow/dist/style.css';

import CRNode from './CRNode';
import AssetNode from './AssetNode';
import { useGraphLayout } from './useGraphLayout';

const nodeTypes = { crNode: CRNode, assetNode: AssetNode };

interface ProjectMember {
  id: string;
  sequence_order: number;
  depends_on: string[];
  change_request: {
    id: string;
    title: string;
    status: string;
    change_type: string;
    asset_ids: string[];
    connector?: { connector_type: string };
  };
}

interface Props {
  members: ProjectMember[];
  token: string;
}

function ProjectGraphInner({ members, token }: Props) {
  const { nodes: initialNodes, edges: initialEdges, loading } = useGraphLayout(members, token);
  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

  // Sync when layout changes (e.g. after assets load)
  React.useEffect(() => {
    if (initialNodes.length > 0) {
      onNodesChange(initialNodes.map(n => ({ type: 'reset' as const, item: n })));
      onEdgesChange(initialEdges.map(e => ({ type: 'reset' as const, item: e })));
    }
  }, [initialNodes, initialEdges]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-400 text-sm">
        Loading graph...
      </div>
    );
  }

  if (members.length === 0) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-400 text-sm">
        No change requests in this project yet.
      </div>
    );
  }

  return (
    <div style={{ height: 600 }} className="rounded-lg border border-gray-200 bg-gray-50">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        attributionPosition="bottom-right"
      >
        <Background color="#e5e7eb" gap={16} />
        <Controls />
        <MiniMap
          nodeColor={node => {
            if (node.type === 'assetNode') return '#14b8a6';
            const status = (node.data as { status?: string }).status ?? 'draft';
            const colors: Record<string, string> = {
              completed: '#22c55e', failed: '#ef4444', executing: '#6366f1',
              approved: '#3b82f6', draft: '#9ca3af',
            };
            return colors[status] ?? '#9ca3af';
          }}
          maskColor="rgba(255,255,255,0.8)"
        />
      </ReactFlow>
    </div>
  );
}

export default function ProjectGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <ProjectGraphInner {...props} />
    </ReactFlowProvider>
  );
}
```

- [ ] **Step 2: Update `frontend/src/pages/ProjectDetail.tsx` to add List/Graph tab**

Find the section that renders the list of project members/change requests. Add tab state and toggle above it:

```tsx
import ProjectGraph from '../components/ProjectGraph';

// Add near top of component:
const [view, setView] = useState<'list' | 'graph'>('list');

// Add tab toggle UI (insert before the existing member list):
<div className="flex gap-1 mb-4 border-b border-gray-200">
  <button
    onClick={() => setView('list')}
    className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
      view === 'list'
        ? 'border-indigo-600 text-indigo-600'
        : 'border-transparent text-gray-500 hover:text-gray-700'
    }`}
  >
    List
  </button>
  <button
    onClick={() => setView('graph')}
    className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
      view === 'graph'
        ? 'border-indigo-600 text-indigo-600'
        : 'border-transparent text-gray-500 hover:text-gray-700'
    }`}
  >
    Graph
  </button>
</div>

// Wrap existing list in a conditional, add graph view:
{view === 'list' ? (
  /* existing member list JSX */
  <ExistingListContent />
) : (
  <ProjectGraph members={project.members ?? []} token={token} />
)}
```

The key is to find the exact JSX block rendering project members and wrap it with this conditional. The `token` is already available in ProjectDetail (used for existing API calls).

- [ ] **Step 3: Build frontend to verify no TypeScript errors**

```
cd frontend && npm run build 2>&1 | tail -15
```

Expected: Build succeeds. Fix any TypeScript errors before proceeding.

- [ ] **Step 4: Commit**

```
git add frontend/src/components/ProjectGraph/ frontend/src/pages/ProjectDetail.tsx
git commit -m "feat(dag): add ProjectGraph component and List/Graph tab toggle to ProjectDetail"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run frontend build one more time cleanly**

```
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 2: Verify no backend tests broken (no backend changes)**

```
docker compose exec backend python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 3: Final commit**

```
git add -A && git commit -m "feat(dag): visual dependency DAG complete — React Flow + Dagre + CRs + Assets" --allow-empty
```
