// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import React from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { ASSET_TYPE_COLORS } from './graphUtils';

interface AssetNodeData {
  name: string;
  assetType: string;
}

export default function AssetNode({ data }: NodeProps<AssetNodeData>) {
  const colors = ASSET_TYPE_COLORS[data.assetType] ?? { bg: '#f8fafc', border: '#94a3b8' };
  return (
    <>
      <Handle type="target" position={Position.Left} style={{ width: 6, height: 6, background: '#d1d5db' }} />
      <div
        style={{ background: colors.bg, borderColor: colors.border, width: 200 }}
        className="rounded-full border-2 px-4 py-1.5 shadow-sm text-center"
      >
        <div className="text-xs font-medium text-gray-700 truncate" title={data.name}>{data.name}</div>
        <div className="text-[10px] text-gray-400">{data.assetType}</div>
      </div>
    </>
  );
}
