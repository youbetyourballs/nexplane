// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useQuery } from "@tanstack/react-query";
import { getAssetDrift } from "../api/drift";

export default function AssetDriftTab({ assetId }: { assetId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["asset-drift", assetId],
    queryFn: () => getAssetDrift(assetId),
  });

  if (isLoading) return <div className="p-4">Loading drift data...</div>;
  if (!data) return null;

  return (
    <div className="p-4 space-y-6">
      <div>
        <h3 className="font-semibold mb-2">Monitored Surfaces</h3>
        {data.resource_states.length === 0 && <p className="text-sm text-gray-500">No surfaces monitored yet.</p>}
        <div className="space-y-1">
          {data.resource_states.map((rs) => (
            <div key={rs.surface_type} className="flex items-center justify-between text-sm border rounded p-2">
              <span className="font-mono">{rs.surface_type}</span>
              <span className="text-xs text-gray-400">{rs.source} · {new Date(rs.captured_at).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="font-semibold mb-2">Open Drift Events ({data.open_events.length})</h3>
        {data.open_events.length === 0 && <p className="text-sm text-gray-500">No open drift events.</p>}
        {data.open_events.map((ev) => (
          <a key={ev.id} href={`/drift?event=${ev.id}`} className="block border rounded p-2 text-sm hover:bg-gray-50 mb-1">
            <span className={`mr-2 text-xs font-medium px-1.5 py-0.5 rounded ${
              ev.severity === "high" ? "bg-red-100 text-red-700" : ev.severity === "medium" ? "bg-yellow-100 text-yellow-700" : "bg-blue-100 text-blue-700"
            }`}>{ev.severity}</span>
            {ev.surface_type} · {new Date(ev.detected_at).toLocaleString()}
          </a>
        ))}
      </div>
    </div>
  );
}
