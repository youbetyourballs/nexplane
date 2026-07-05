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

const STATUSES: ChangeRequestStatus[] = [
  "draft","planned","awaiting_approval","approved","executing","verifying","completed","failed","rolled_back","rejected",
];
const RISK_LEVELS: RiskLevel[] = ["low","medium","high","critical"];
const CHANGE_TYPES: ChangeType[] = [
  "dns_update","snapshot_asset","security_group_update","key_rotation",
  "telemetry_agent_deploy","remote_command","microsegmentation_policy",
];

export function ChangeRequestList() {
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

  return (
    <div className="p-8">
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
    </div>
  );
}
