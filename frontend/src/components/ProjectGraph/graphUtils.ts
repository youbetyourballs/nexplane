import type { Node, Edge } from 'reactflow';
import dagre from '@dagrejs/dagre';

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
    const h = node.type === 'assetNode' ? ASSET_NODE_HEIGHT : CR_NODE_HEIGHT;
    g.setNode(node.id, { width: NODE_WIDTH, height: h });
  });
  edges.forEach(edge => g.setEdge(edge.source, edge.target));
  dagre.layout(g);

  return nodes.map(node => {
    const { x, y } = g.node(node.id);
    const h = node.type === 'assetNode' ? ASSET_NODE_HEIGHT : CR_NODE_HEIGHT;
    return { ...node, position: { x: x - NODE_WIDTH / 2, y: y - h / 2 } };
  });
}
