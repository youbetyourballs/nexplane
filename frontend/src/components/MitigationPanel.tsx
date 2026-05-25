import React, { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Suggestion {
  control: string;
  title: string;
  description: string;
  rationale: string;
  confidence: number;
  impact: string;
  recommended: boolean;
}

interface Props {
  findingId: string;
  onApplied: () => void;
}

export default function MitigationPanel({ findingId, onApplied }: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useQuery({
    queryKey: ["mitigation-suggestions", findingId],
    queryFn: () =>
      apiClient
        .get<{ suggestions: Suggestion[]; ai_summary: string }>(
          `/api/v1/vulnerability/findings/${findingId}/mitigations`
        )
        .then(r => r.data),
  });

  // Pre-select recommended controls when data loads (React Query v5: no onSuccess in useQuery)
  const initialized = React.useRef(false);
  useEffect(() => {
    if (data?.suggestions && !initialized.current) {
      initialized.current = true;
      setSelected(new Set(data.suggestions.filter(s => s.recommended).map(s => s.control)));
    }
  }, [data]);

  const applyMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/api/v1/vulnerability/findings/${findingId}/mitigate`, {
        selected_controls: Array.from(selected),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["findings"] });
      onApplied();
    },
  });

  const toggle = (control: string) => {
    const next = new Set(selected);
    if (next.has(control)) next.delete(control);
    else next.add(control);
    setSelected(next);
  };

  if (isLoading) return <p className="text-sm text-slate-500 py-2">Analysing CVE…</p>;
  if (error || !data) return <p className="text-sm text-red-500 py-2">Could not load suggestions</p>;

  return (
    <div className="bg-white border border-slate-200 rounded space-y-0 overflow-hidden">
      {/* AI summary */}
      <div className="bg-blue-50 border-b border-blue-100 px-3 py-2 flex gap-2 items-start">
        <span className="text-base mt-0.5">🤖</span>
        <p className="text-xs text-blue-800">{data.ai_summary}</p>
      </div>

      {/* Suggestions */}
      {data.suggestions.map((s) => (
        <div
          key={s.control}
          onClick={() => toggle(s.control)}
          className={`px-3 py-2.5 border-b border-slate-100 flex gap-3 cursor-pointer transition-colors ${
            selected.has(s.control)
              ? "bg-green-50 hover:bg-slate-50"
              : s.impact === "high"
              ? "bg-red-50 hover:bg-red-100"
              : "hover:bg-slate-50"
          }`}
        >
          <div className={`w-5 h-5 mt-0.5 rounded flex-shrink-0 flex items-center justify-center border-2 ${
            selected.has(s.control) ? "bg-green-500 border-green-500" : "border-slate-300"
          }`}>
            {selected.has(s.control) && <span className="text-white text-xs font-bold">✓</span>}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-slate-800">{s.title}</span>
              {s.recommended && (
                <span className="text-xs bg-green-100 text-green-800 px-1.5 py-0.5 rounded">
                  Recommended · {Math.round(s.confidence * 100)}%
                </span>
              )}
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                s.impact === "high" ? "bg-red-100 text-red-700" :
                s.impact === "medium" ? "bg-yellow-100 text-yellow-700" :
                "bg-green-100 text-green-600"
              }`}>
                {s.impact} impact
              </span>
              {s.impact === "high" && (
                <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">⚠ High impact</span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">{s.rationale}</p>
          </div>
        </div>
      ))}

      {/* Apply button */}
      <div className="px-3 py-2.5 flex items-center gap-3">
        <button
          onClick={(e) => { e.stopPropagation(); applyMutation.mutate(); }}
          disabled={selected.size === 0 || applyMutation.isPending}
          className="px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700 disabled:opacity-50"
        >
          {applyMutation.isPending ? "Applying…" : `Apply ${selected.size} mitigations (defense in depth)`}
        </button>
        <span className="text-xs text-slate-400">All mitigations are reversible. Finding stays open until patched.</span>
      </div>

      {applyMutation.isSuccess && (
        <div className="px-3 py-2 bg-green-50 text-sm text-green-700 border-t border-green-100">
          ✓ Mitigation CRs created — awaiting approval
        </div>
      )}
      {applyMutation.isError && (
        <div className="px-3 py-2 bg-red-50 text-sm text-red-700 border-t border-red-100">
          ✗ Failed to apply mitigations. Please try again.
        </div>
      )}
    </div>
  );
}
