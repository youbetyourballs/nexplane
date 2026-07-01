// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle, ExternalLink } from "lucide-react";
import { changeRequestsApi } from "../api/endpoints";
import { StatusBadge } from "../components/StatusBadge";
import { RiskBadge } from "../components/RiskBadge";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";
import { formatDistanceToNow } from "date-fns";

export function ApprovalsQueue() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [comments, setComments] = useState<Record<string, string>>({});

  const { data, isLoading } = useQuery({
    queryKey: ["change-requests", "awaiting_approval"],
    queryFn: () => changeRequestsApi.list({ status: "awaiting_approval" }),
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, comment }: { id: string; comment: string }) =>
      changeRequestsApi.approve(id, { decision: "approved", comment }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["change-requests"] }),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, comment }: { id: string; comment: string }) =>
      changeRequestsApi.reject(id, { decision: "rejected", comment }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["change-requests"] }),
  });

  const canApprove = user?.role === "approver" || user?.role === "admin";

  if (isLoading) return <PageLoading />;

  const queue = data ?? [];

  return (
    <div className="p-8">
      <PageHeader
        title="Approvals Queue"
        subtitle={`${queue.length} change request${queue.length !== 1 ? "s" : ""} awaiting approval`}
      />

      {!canApprove && (
        <div className="mb-6 p-4 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700">
          Your role ({user?.role?.replace("_", " ")}) does not have approval permissions. Approvers and admins can approve requests.
        </div>
      )}

      {queue.length === 0 && (
        <div className="bg-white border border-slate-200 rounded-lg py-16 text-center">
          <CheckCircle2 className="w-10 h-10 text-slate-200 mx-auto mb-3" />
          <div className="text-slate-500 text-sm">No change requests awaiting approval</div>
        </div>
      )}

      <div className="space-y-4">
        {queue.map((cr) => (
          <div key={cr.id} className="bg-white border border-slate-200 rounded-lg overflow-hidden">
            <div className="p-5">
              <div className="flex items-start justify-between gap-4 mb-3">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <Link
                      to={`/change-requests/${cr.id}`}
                      className="text-base font-semibold text-slate-900 hover:text-brand-700 flex items-center gap-1.5"
                    >
                      {cr.title}
                      <ExternalLink className="w-3.5 h-3.5 text-slate-400" />
                    </Link>
                    {(cr as any).priority === "emergency" && (
                      <span className="text-xs bg-red-600 text-white px-2 py-0.5 rounded-full font-bold tracking-wide animate-pulse">
                        EMERGENCY
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <RiskBadge level={cr.risk_level} />
                    <StatusBadge status={cr.status} size="sm" />
                    <span className="text-xs text-slate-400">
                      {cr.change_type.replace(/_/g, " ")}
                    </span>
                  </div>
                  {(cr as any).finding_ids?.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(cr as any).finding_ids.map((fid: string) => (
                        <span key={fid} className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">
                          Finding {fid.slice(0, 8)}…
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="text-right flex-shrink-0">
                  <div className="text-xs text-slate-500">{cr.requester.name}</div>
                  <div className="text-xs text-slate-400">
                    {formatDistanceToNow(new Date(cr.created_at), { addSuffix: true })}
                  </div>
                </div>
              </div>

              {(cr.risk_level === "high" || cr.risk_level === "critical") && (
                <div className="mb-3 p-2.5 bg-orange-50 border border-orange-200 rounded text-xs text-orange-700">
                  <strong>{cr.risk_level === "critical" ? "Critical" : "High"} risk:</strong>{" "}
                  {cr.risk_level === "critical"
                    ? "Requires approval from both approver and admin. Will not auto-execute."
                    : "Requires approval from both approver and admin."}
                </div>
              )}

              {canApprove && (
                <div className="mt-4 pt-4 border-t border-slate-100">
                  <input
                    type="text"
                    placeholder="Add approval comment (optional)"
                    value={comments[cr.id] ?? ""}
                    onChange={(e) => setComments((prev) => ({ ...prev, [cr.id]: e.target.value }))}
                    className="w-full text-sm border border-slate-200 rounded px-3 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => approveMutation.mutate({ id: cr.id, comment: comments[cr.id] ?? "" })}
                      disabled={approveMutation.isPending}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 text-white text-sm rounded hover:bg-emerald-700 disabled:opacity-50"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      Approve
                    </button>
                    <button
                      onClick={() => rejectMutation.mutate({ id: cr.id, comment: comments[cr.id] ?? "" })}
                      disabled={rejectMutation.isPending}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-700 disabled:opacity-50"
                    >
                      <XCircle className="w-3.5 h-3.5" />
                      Reject
                    </button>
                    <Link
                      to={`/change-requests/${cr.id}`}
                      className="px-3 py-1.5 border border-slate-200 text-slate-600 text-sm rounded hover:bg-slate-50"
                    >
                      Review Plan
                    </Link>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
