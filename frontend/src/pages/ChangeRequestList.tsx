// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Trash2 } from "lucide-react";
import { apiClient } from "../api/client";
import { changeRequestsApi } from "../api/endpoints";
import { StatusBadge } from "../components/StatusBadge";
import { RiskBadge } from "../components/RiskBadge";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";
import type { ChangeRequestStatus, RiskLevel, ChangeType } from "../types/api";
import { irApi, ForensicBundle, StepResult } from "../api/ir";
import { IRPlaybookLauncher } from "../components/IRPlaybookLauncher";
import { IRStepStatusBadge } from "../components/IRStepStatusBadge";

const STATUSES: ChangeRequestStatus[] = [
  "draft","planned","awaiting_approval","approved","executing","verifying","completed","failed","rolled_back","rejected",
];
const RISK_LEVELS: RiskLevel[] = ["low","medium","high","critical"];
const CHANGE_TYPES: ChangeType[] = [
  "dns_update","snapshot_asset","security_group_update","key_rotation",
  "telemetry_agent_deploy","remote_command","microsegmentation_policy",
];

function IRBundlePanel() {
  const { data: bundles = [] } = useQuery({
    queryKey: ["ir-bundles"],
    queryFn: () => irApi.getBundles({ limit: 50 }),
  });

  if (bundles.length === 0) {
    return <p style={{ color: "#94a3b8" }}>No forensic bundles collected recently.</p>;
  }

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", color: "#f1f5f9", fontSize: 13 }}>
      <thead>
        <tr style={{ borderBottom: "1px solid #334155" }}>
          <th style={{ textAlign: "left", padding: "6px 8px" }}>Asset ID</th>
          <th style={{ textAlign: "left", padding: "6px 8px" }}>Collected</th>
          <th style={{ textAlign: "right", padding: "6px 8px" }}>Size</th>
          <th style={{ padding: "6px 8px" }}></th>
        </tr>
      </thead>
      <tbody>
        {bundles.map((b: ForensicBundle) => (
          <tr key={b.id} style={{ borderBottom: "1px solid #1e293b" }}>
            <td style={{ padding: "6px 8px", fontFamily: "monospace", fontSize: 11 }}>{b.asset_id}</td>
            <td style={{ padding: "6px 8px" }}>{new Date(b.collected_at).toLocaleString()}</td>
            <td style={{ padding: "6px 8px", textAlign: "right" }}>
              {b.size_bytes != null ? `${(b.size_bytes / 1024 / 1024).toFixed(1)} MB` : "—"}
            </td>
            <td style={{ padding: "6px 8px" }}>
              <button
                style={{ padding: "4px 10px", background: "#3b82f6", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 12 }}
                onClick={async () => {
                  const { url } = await irApi.getBundleDownloadUrl(b.id);
                  window.open(url, "_blank");
                }}
              >
                Download
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ChangeRequestList() {
  const [activeTab, setActiveTab] = useState<"all" | "ir">("all");
  const [status, setStatus] = useState("");
  const [riskLevel, setRiskLevel] = useState("");
  const [changeType, setChangeType] = useState("");
  const queryClient = useQueryClient();

  const cleanupMutation = useMutation({
    mutationFn: () => apiClient.post("/change-requests/cleanup-stuck").then((r) => r.data),
    onSuccess: (data: { cleaned: number }) => {
      queryClient.invalidateQueries({ queryKey: ["change-requests"] });
      alert(`Cleaned up ${data.cleaned} stuck change request(s).`);
    },
  });

  const { data, isLoading } = useQuery({
    queryKey: ["change-requests", status, riskLevel, changeType],
    queryFn: () =>
      changeRequestsApi.list({
        ...(status && { status }),
        ...(riskLevel && { risk_level: riskLevel }),
        ...(changeType && { change_type: changeType }),
      }),
  });

  // Fetch IR change requests (incident_response=true)
  const { data: irChangeRequests = [] } = useQuery({
    queryKey: ["change-requests-ir"],
    queryFn: () => changeRequestsApi.list({ change_type: "isolate_host" }),
    enabled: activeTab === "ir",
  });

  return (
    <div className="p-8">
      {/* Tab bar */}
      <div style={{ display: "flex", gap: 0, marginBottom: 20, borderBottom: "1px solid #334155" }}>
        {(["all", "ir"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: "8px 20px",
              background: "none",
              border: "none",
              borderBottom: activeTab === tab ? "2px solid #3b82f6" : "2px solid transparent",
              color: activeTab === tab ? "#f1f5f9" : "#94a3b8",
              cursor: "pointer",
              fontWeight: activeTab === tab ? 600 : 400,
              marginBottom: -1,
            }}
          >
            {tab === "all" ? "All Change Requests" : "Incident Response"}
          </button>
        ))}
      </div>

      {activeTab === "ir" && (
        <div>
          <h2 style={{ color: "#f1f5f9", marginBottom: 16 }}>Launch IR Playbook</h2>
          <IRPlaybookLauncher />

          <h2 style={{ color: "#f1f5f9", margin: "32px 0 16px" }}>Active IR Change Requests</h2>
          {(irChangeRequests as Array<{ id: string; title: string; status: string; step_results?: Record<string, StepResult> }>).map((cr) => (
            <div key={cr.id} style={{ background: "#1e293b", padding: 12, borderRadius: 6, marginBottom: 8 }}>
              <span style={{ color: "#f1f5f9", fontWeight: 600 }}>{cr.title}</span>
              <span style={{ marginLeft: 12, color: "#94a3b8", fontSize: 12 }}>{cr.status}</span>
              <div style={{ marginTop: 8 }}>
                <IRStepStatusBadge stepResults={cr.step_results ?? {}} />
              </div>
            </div>
          ))}

          <h2 style={{ color: "#f1f5f9", margin: "32px 0 16px" }}>Recent Forensic Bundles</h2>
          <IRBundlePanel />
        </div>
      )}

      {activeTab === "all" && (
        <>
          <PageHeader
            title="Change Requests"
            subtitle="All governed infrastructure change requests"
            actions={
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    if (confirm("Mark all executing/verifying CRs as failed? This clears phantom activity from crashed runs.")) {
                      cleanupMutation.mutate();
                    }
                  }}
                  disabled={cleanupMutation.isPending}
                  className="inline-flex items-center gap-1.5 px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium rounded-md transition-colors disabled:opacity-50"
                  title="Clear stuck executing/verifying CRs orphaned by server restarts"
                >
                  <Trash2 className="w-4 h-4" />
                  Clean Up Stuck
                </button>
                <Link
                  to="/change-requests/new"
                  className="inline-flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  New Request
                </Link>
              </div>
            }
          />

          <div className="flex gap-3 mb-5">
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="text-sm border border-slate-200 rounded-md px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="">All Statuses</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </select>

            <select
              value={riskLevel}
              onChange={(e) => setRiskLevel(e.target.value)}
              className="text-sm border border-slate-200 rounded-md px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="">All Risk Levels</option>
              {RISK_LEVELS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>

            <select
              value={changeType}
              onChange={(e) => setChangeType(e.target.value)}
              className="text-sm border border-slate-200 rounded-md px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="">All Change Types</option>
              {CHANGE_TYPES.map((c) => (
                <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
              ))}
            </select>
          </div>

          {isLoading ? (
            <PageLoading />
          ) : (
            <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Title
                    </th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Type
                    </th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Risk
                    </th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Status
                    </th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Requester
                    </th>
                    <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Created
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {(data ?? []).length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-5 py-8 text-center text-slate-400">
                        No change requests found.
                      </td>
                    </tr>
                  )}
                  {(data ?? []).map((cr) => (
                    <tr key={cr.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-5 py-3.5">
                        <Link
                          to={`/change-requests/${cr.id}`}
                          className="font-medium text-slate-900 hover:text-brand-700"
                        >
                          {cr.title}
                        </Link>
                      </td>
                      <td className="px-4 py-3.5 text-slate-500">
                        {cr.change_type.replace(/_/g, " ")}
                      </td>
                      <td className="px-4 py-3.5">
                        <RiskBadge level={cr.risk_level} size="sm" />
                      </td>
                      <td className="px-4 py-3.5">
                        <StatusBadge status={cr.status} size="sm" />
                      </td>
                      <td className="px-4 py-3.5 text-slate-500">{cr.requester.name}</td>
                      <td className="px-4 py-3.5 text-slate-400 text-xs">
                        {formatDistanceToNow(new Date(cr.created_at), { addSuffix: true })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
