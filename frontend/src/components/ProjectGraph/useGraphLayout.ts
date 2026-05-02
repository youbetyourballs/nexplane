import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { MarkerType, type Node, type Edge } from 'reactflow';
import type { ProjectMember, Asset } from '../../types/api';
import { applyDagreLayout } from './graphUtils';

export function useGraphLayout(members: ProjectMember[], token: string) {
  // ChangeRequestSummary does not expose asset_ids; cast to access if present at runtime
  const allAssetIds = useMemo(() => {
    const ids = new Set<string>();
    members.forEach(m => {
      const cr = m.change_request as unknown as { asset_ids?: string[] };
      (cr.asset_ids ?? []).forEach(id => ids.add(id));
    });
    return Array.from(ids);
  }, [members]);

  const assetQueries = useQueries({
    queries: allAssetIds.map(assetId => ({
      queryKey: ['asset', assetId],
      queryFn: () =>
        fetch(`/assets/${assetId}`, { headers: { Authorization: `Bearer ${token}` } })
          .then(r => r.ok ? r.json() as Promise<Asset> : null),
      staleTime: 5 * 60 * 1000,
    })),
  });

  const assets = useMemo(() => {
    const map = new Map<string, Asset>();
    assetQueries.forEach(q => { if (q.data) map.set(q.data.id, q.data); });
    return map;
  }, [assetQueries]);

  const loading = assetQueries.some(q => q.isLoading);

  const { nodes, edges } = useMemo(() => {
    if (members.length === 0) return { nodes: [] as Node[], edges: [] as Edge[] };

    const nodes: Node[] = [];
    const edges: Edge[] = [];
    const crToNodeId = new Map<string, string>();

    members.forEach(member => {
      const cr = member.change_request;
      const nodeId = `cr-${cr.id}`;
      crToNodeId.set(cr.id, nodeId);
      const crExt = cr as unknown as { connector?: { connector_type: string } };
      nodes.push({
        id: nodeId,
        type: 'crNode',
        position: { x: 0, y: 0 },
        data: {
          title: cr.title,
          status: cr.status,
          connectorType: crExt.connector?.connector_type,
        },
      });
    });

    const addedAssets = new Set<string>();
    members.forEach(member => {
      const crExt = member.change_request as unknown as { asset_ids?: string[] };
      (crExt.asset_ids ?? []).forEach(assetId => {
        const nodeId = `asset-${assetId}`;
        if (!addedAssets.has(assetId)) {
          addedAssets.add(assetId);
          const asset = assets.get(assetId);
          nodes.push({
            id: nodeId,
            type: 'assetNode',
            position: { x: 0, y: 0 },
            data: {
              name: asset?.name ?? assetId.slice(0, 8) + '…',
              assetType: asset?.asset_type ?? 'server',
            },
          });
        }
        edges.push({
          id: `e-ta-${member.change_request.id}-${assetId}`,
          source: `cr-${member.change_request.id}`,
          target: nodeId,
          type: 'smoothstep',
          style: { strokeDasharray: '5,5', stroke: '#14b8a6', strokeWidth: 1.5 },
        });
      });
    });

    members.forEach(member => {
      (member.depends_on ?? []).forEach(depCRId => {
        // depends_on stores project member IDs; find the matching member to get the CR id
        const depMember = members.find(m => m.id === depCRId);
        const srcNodeId = depMember
          ? crToNodeId.get(depMember.change_request.id)
          : crToNodeId.get(depCRId);
        if (srcNodeId) {
          edges.push({
            id: `e-dep-${depCRId}-${member.change_request.id}`,
            source: srcNodeId,
            target: `cr-${member.change_request.id}`,
            type: 'smoothstep',
            style: { stroke: '#6b7280', strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
            label: 'depends on',
            labelStyle: { fontSize: 10, fill: '#9ca3af' },
          });
        }
      });
    });

    const laidOut = applyDagreLayout(nodes, edges);
    return { nodes: laidOut, edges };
  }, [members, assets]);

  return { nodes, edges, loading };
}
