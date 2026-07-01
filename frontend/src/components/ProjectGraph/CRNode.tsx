// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import React from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { CR_STATUS_COLORS } from './graphUtils';

interface CRNodeData {
  title: string;
  status: string;
  connectorType?: string;
}

export default function CRNode({ data }: NodeProps<CRNodeData>) {
  const colors = CR_STATUS_COLORS[data.status] ?? CR_STATUS_COLORS.draft;
  return (
    <>
      <Handle type="target" position={Position.Left} style={{ width: 8, height: 8, background: '#9ca3af' }} />
      <div
        style={{ background: colors.bg, borderColor: colors.border, color: colors.text, width: 200 }}
        className="rounded-lg border-2 px-3 py-2 shadow-sm"
      >
        <div className="text-xs font-semibold line-clamp-2" title={data.title}>{data.title}</div>
        <div className="flex items-center gap-1 mt-1">
          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
            style={{ background: colors.border + '22', color: colors.text }}>
            {data.status.replace(/_/g, ' ')}
          </span>
          {data.connectorType && (
            <span className="text-[10px] text-gray-400 truncate">
              {data.connectorType.replace('_mock', '').replace('_', ' ')}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Right} style={{ width: 8, height: 8, background: '#9ca3af' }} />
    </>
  );
}
