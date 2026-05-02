import React, { useEffect } from 'react';
import ReactFlow, {
  Background, Controls, MiniMap, ReactFlowProvider,
  useNodesState, useEdgesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import type { ProjectMember } from '../../types/api';
import CRNode from './CRNode';
import AssetNode from './AssetNode';
import { useGraphLayout } from './useGraphLayout';

const nodeTypes = { crNode: CRNode, assetNode: AssetNode };

function ProjectGraphInner({ members, token }: { members: ProjectMember[]; token: string }) {
  const { nodes: initialNodes, edges: initialEdges, loading } = useGraphLayout(members, token);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  useEffect(() => {
    if (initialNodes.length > 0) {
      setNodes(initialNodes);
      setEdges(initialEdges);
    }
  }, [initialNodes, initialEdges]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-400 text-sm">
        Loading graph…
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
        <MiniMap maskColor="rgba(255,255,255,0.8)" />
      </ReactFlow>
    </div>
  );
}

export default function ProjectGraph(props: { members: ProjectMember[]; token: string }) {
  return <ReactFlowProvider><ProjectGraphInner {...props} /></ReactFlowProvider>;
}
