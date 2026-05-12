import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Campaign {
  id: string;
  title: string;
  cve_id: string | null;
  status: string;
  total_assets: number;
  passed_count: number;
  failed_count: number;
  pending_count: number;
  created_at: string;
  batches: any[];
}

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  running: "bg-blue-100 text-blue-800",
  paused: "bg-yellow-100 text-yellow-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  aborted: "bg-gray-100 text-gray-600",
};

export default function PatchCampaignList({ onCreateNew }: { onCreateNew: () => void }) {
  const qc = useQueryClient();
  const { data: campaigns = [], isLoading } = useQuery<Campaign[]>({
    queryKey: ["campaigns"],
    queryFn: () => apiClient.get("/api/v1/vulnerability/campaigns").then(r => r.data),
    refetchInterval: 10000,
  });

  const pauseMutation = useMutation({
    mutationFn: (id: string) => apiClient.post(`/api/v1/vulnerability/campaigns/${id}/pause`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
  const resumeMutation = useMutation({
    mutationFn: (id: string) => apiClient.post(`/api/v1/vulnerability/campaigns/${id}/resume`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
  const abortMutation = useMutation({
    mutationFn: (id: string) => apiClient.post(`/api/v1/vulnerability/campaigns/${id}/abort`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });

  if (isLoading) return <p className="text-sm text-slate-500 py-4">Loading campaigns…</p>;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="font-semibold text-slate-900">Patch Campaigns</h3>
        <button
          onClick={onCreateNew}
          className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
        >
          + New Campaign
        </button>
      </div>

      {campaigns.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
          <p className="text-slate-500 text-sm">No campaigns yet.</p>
          <button onClick={onCreateNew} className="mt-3 px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Create First Campaign
          </button>
        </div>
      ) : (
        <div className="border border-slate-200 rounded-lg overflow-hidden">
          {campaigns.map((c, i) => {
            const pct = c.total_assets > 0 ? Math.round((c.passed_count / c.total_assets) * 100) : 0;
            return (
              <div key={c.id} className={`p-4 ${i < campaigns.length - 1 ? "border-b border-slate-100" : ""}`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-slate-900 text-sm">{c.title}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[c.status] || "bg-slate-100 text-slate-700"}`}>
                        {c.status.charAt(0).toUpperCase() + c.status.slice(1)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                      <span>{c.total_assets} assets</span>
                      {c.passed_count > 0 && <span className="text-green-700">✓ {c.passed_count}</span>}
                      {c.pending_count > 0 && <span className="text-slate-500">{c.pending_count} pending</span>}
                      {c.failed_count > 0 && <span className="text-red-600">✗ {c.failed_count} failed</span>}
                    </div>
                    {c.status === "running" && c.total_assets > 0 && (
                      <div className="mt-2 bg-slate-200 rounded-full h-1.5">
                        <div
                          className="bg-blue-600 h-1.5 rounded-full transition-all"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    )}
                    {c.failed_count > 0 && (
                      <div className="mt-2 bg-red-50 border border-red-200 rounded px-2 py-1 text-xs text-red-700">
                        {c.failed_count} asset{c.failed_count !== 1 ? "s" : ""} failed — rollback applied
                      </div>
                    )}
                  </div>
                  <div className="flex gap-1.5 flex-shrink-0">
                    {c.status === "running" && (
                      <button onClick={() => pauseMutation.mutate(c.id)} className="px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-50">Pause</button>
                    )}
                    {c.status === "paused" && (
                      <button onClick={() => resumeMutation.mutate(c.id)} className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700">Resume</button>
                    )}
                    {["running", "paused"].includes(c.status) && (
                      <button onClick={() => abortMutation.mutate(c.id)} className="px-2 py-1 text-xs text-red-600 border border-red-200 rounded hover:bg-red-50">Abort</button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
