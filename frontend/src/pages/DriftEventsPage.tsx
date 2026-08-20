// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle, Clock, XCircle } from "lucide-react";
import { listDriftEvents, acceptDriftEvent, attestDriftEvent, dismissDriftEvent, DriftEventRead } from "../api/drift";

const SEVERITY_COLOR: Record<string, string> = {
  high: "text-red-600 bg-red-50",
  medium: "text-yellow-600 bg-yellow-50",
  low: "text-blue-600 bg-blue-50",
};

function DiffTable({ diff }: { diff: DriftEventRead["diff"] }) {
  const rows: { key: string; from?: unknown; to?: unknown; kind: string }[] = [
    ...Object.entries(diff.added || {}).map(([k, v]) => ({ key: k, to: v, kind: "added" })),
    ...Object.entries(diff.removed || {}).map(([k, v]) => ({ key: k, from: v, kind: "removed" })),
    ...Object.entries(diff.changed || {}).map(([k, v]) => ({ key: k, from: (v as { from: unknown; to: unknown }).from, to: (v as { from: unknown; to: unknown }).to, kind: "changed" })),
  ];
  if (rows.length === 0) return <p className="text-sm text-gray-500">No diff data</p>;
  return (
    <table className="text-xs w-full border-collapse">
      <thead>
        <tr className="bg-gray-100">
          <th className="text-left p-1 border">Key</th>
          <th className="text-left p-1 border">From</th>
          <th className="text-left p-1 border">To</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className={r.kind === "added" ? "bg-green-50" : r.kind === "removed" ? "bg-red-50" : ""}>
            <td className="p-1 border font-mono">{r.key}</td>
            <td className="p-1 border font-mono">{r.from !== undefined ? JSON.stringify(r.from) : "—"}</td>
            <td className="p-1 border font-mono">{r.to !== undefined ? JSON.stringify(r.to) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function DriftEventsPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<DriftEventRead | null>(null);
  const [note, setNote] = useState("");
  const [snoozeDays, setSnoozeDays] = useState(7);

  const { data: events = [], isLoading } = useQuery({
    queryKey: ["drift-events"],
    queryFn: () => listDriftEvents({ status: "open" }),
  });

  const accept = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) => acceptDriftEvent(id, note),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drift-events"] }); setSelected(null); },
  });
  const attest = useMutation({
    mutationFn: ({ id, note, days }: { id: string; note: string; days: number }) => attestDriftEvent(id, note, days),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drift-events"] }); setSelected(null); },
  });
  const dismiss = useMutation({
    mutationFn: (id: string) => dismissDriftEvent(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["drift-events"] }); setSelected(null); },
  });

  if (isLoading) return <div className="p-6">Loading drift events...</div>;

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold mb-4 flex items-center gap-2">
        <AlertTriangle className="text-yellow-500" /> Drift Events
      </h1>
      {events.length === 0 && <p className="text-gray-500">No open drift events.</p>}
      <div className="space-y-2">
        {events.map((ev) => (
          <div
            key={ev.id}
            className="border rounded p-3 cursor-pointer hover:bg-gray-50 flex items-center justify-between"
            onClick={() => { setSelected(ev); setNote(""); }}
          >
            <div className="flex items-center gap-3">
              <span className={`text-xs font-medium px-2 py-0.5 rounded ${SEVERITY_COLOR[ev.severity]}`}>
                {ev.severity.toUpperCase()}
              </span>
              <span className="font-mono text-sm">{ev.surface_type}</span>
              <span className="text-xs text-gray-400">{new Date(ev.detected_at).toLocaleString()}</span>
            </div>
            <span className="text-xs text-gray-400">{Object.keys(ev.diff.changed || {}).length + Object.keys(ev.diff.added || {}).length + Object.keys(ev.diff.removed || {}).length} changes</span>
          </div>
        ))}
      </div>

      {selected && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setSelected(null)}>
          <div className="bg-white rounded-lg shadow-xl w-3/4 max-h-[80vh] overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-1">{selected.surface_type} drift</h2>
            <p className="text-xs text-gray-400 mb-4">Asset: {selected.asset_id} · Detected: {new Date(selected.detected_at).toLocaleString()}</p>

            <DiffTable diff={selected.diff} />

            <div className="mt-4 space-y-2">
              <textarea
                className="w-full border rounded p-2 text-sm"
                placeholder="Optional note..."
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <div className="flex gap-2 flex-wrap">
                {selected.shadow_cr_id && (
                  <a href={`/change-requests/${selected.shadow_cr_id}`} className="btn btn-sm border rounded px-3 py-1 text-sm hover:bg-gray-100 flex items-center gap-1">
                    <CheckCircle size={14} /> Approve Shadow CR
                  </a>
                )}
                <button onClick={() => accept.mutate({ id: selected.id, note })} className="border rounded px-3 py-1 text-sm hover:bg-green-50 flex items-center gap-1">
                  <CheckCircle size={14} className="text-green-500" /> Accept New State
                </button>
                <button onClick={() => attest.mutate({ id: selected.id, note, days: snoozeDays })} className="border rounded px-3 py-1 text-sm hover:bg-yellow-50 flex items-center gap-1">
                  <Clock size={14} className="text-yellow-500" /> Attest
                  <input type="number" className="w-12 border ml-1 rounded px-1 text-xs" value={snoozeDays} min={1} max={90}
                    onChange={(e) => { e.stopPropagation(); setSnoozeDays(Number(e.target.value)); }} />
                  d
                </button>
                <button onClick={() => dismiss.mutate(selected.id)} className="border rounded px-3 py-1 text-sm hover:bg-red-50 flex items-center gap-1">
                  <XCircle size={14} className="text-red-400" /> Dismiss
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
