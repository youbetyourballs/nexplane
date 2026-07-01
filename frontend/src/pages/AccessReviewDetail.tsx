// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, XCircle, AlertTriangle, ShieldAlert, Download, ArrowLeft, Play } from "lucide-react";
import { reviewCampaignsApi, type ReviewEntryOut } from "../api/reviewCampaigns";
import { apiClient } from "../api/client";
import { PageLoading } from "../components/LoadingSpinner";

function RiskBadges({ entry }: { entry: ReviewEntryOut }) {
  return (
    <div className="flex gap-1 flex-wrap">
      {entry.is_privileged && (
        <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-xs bg-red-100 text-red-700 rounded font-medium">
          <ShieldAlert className="w-3 h-3" /> Privileged
        </span>
      )}
      {entry.evidence.flagged_inactive && (
        <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-xs bg-orange-100 text-orange-700 rounded font-medium">
          <AlertTriangle className="w-3 h-3" /> Inactive {entry.evidence.days_inactive}d
        </span>
      )}
      {entry.reviewer_unresolved && (
        <span className="px-1.5 py-0.5 text-xs bg-yellow-100 text-yellow-700 rounded">Unassigned</span>
      )}
    </div>
  );
}

export function AccessReviewDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [decisionFilter, setDecisionFilter] = useState<"all" | "pending" | "keep" | "revoke">("all");
  const [noteEntry, setNoteEntry] = useState<string | null>(null);
  const [noteText, setNoteText] = useState("");

  const { data: campaign, isLoading: campaignLoading } = useQuery({
    queryKey: ["review-campaign", id],
    queryFn: () => reviewCampaignsApi.get(id!),
    refetchInterval: (q) => (q.state.data?.status === "collecting" ? 2000 : false),
    enabled: !!id,
  });

  const { data: entries, isLoading: entriesLoading } = useQuery({
    queryKey: ["review-entries", id, decisionFilter],
    queryFn: () => reviewCampaignsApi.listEntries(id!, decisionFilter === "pending" ? { decision: "pending" } : decisionFilter !== "all" ? { decision: decisionFilter } : {}),
    enabled: !!id && !!campaign && campaign.status !== "draft" && campaign.status !== "collecting",
  });

  const decisionMutation = useMutation({
    mutationFn: ({ entryId, decision, note }: { entryId: string; decision: "keep" | "revoke"; note?: string }) =>
      reviewCampaignsApi.submitDecision(id!, entryId, { decision, note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-entries", id] });
      qc.invalidateQueries({ queryKey: ["review-campaign", id] });
      setNoteEntry(null);
      setNoteText("");
    },
  });

  const launchMutation = useMutation({
    mutationFn: () => reviewCampaignsApi.launch(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review-campaign", id] }),
  });

  const approveMutation = useMutation({
    mutationFn: () => reviewCampaignsApi.approve(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-campaign", id] });
      qc.invalidateQueries({ queryKey: ["review-entries", id] });
    },
  });

  const createRemediationCrMutation = useMutation({
    mutationFn: (entryId: string) =>
      apiClient.post(`/review-campaigns/${id}/entries/${entryId}/create-remediation`).then((r) => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["review-entries", id] });
      if (data?.cr_id) {
        navigate(`/change-requests/${data.cr_id}`);
      }
    },
  });

  const handleDownload = async () => {
    const data = await reviewCampaignsApi.exportEvidence(id!);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `access-review-${id}-evidence.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (campaignLoading) return <PageLoading />;
  if (!campaign) return <div className="p-6 text-red-600">Campaign not found</div>;

  const allEntries = entries ?? [];
  const total = allEntries.length;
  const decided = allEntries.filter((e) => e.decision !== null).length;
  const pending = total - decided;
  const revokeCount = allEntries.filter((e) => e.decision === "revoke").length;
  const flagged = allEntries.filter((e) => e.is_privileged || e.evidence.flagged_inactive).length;
  const canDecide = campaign.status === "in_review" || campaign.status === "awaiting_approval";
  const canApprove = campaign.status === "awaiting_approval";

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5">
      <div>
        <Link to="/access-reviews" className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 mb-3">
          <ArrowLeft className="w-4 h-4" /> All Campaigns
        </Link>
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{campaign.title}</h1>
            <div className="flex items-center gap-2 mt-1 text-sm text-slate-500">
              <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                campaign.status === "completed" ? "bg-green-100 text-green-800"
                : campaign.status === "in_review" || campaign.status === "awaiting_approval" ? "bg-blue-100 text-blue-800"
                : campaign.status === "collecting" ? "bg-yellow-100 text-yellow-800"
                : "bg-slate-100 text-slate-600"}`}>
                {campaign.status.replace(/_/g, " ")}
              </span>
              <span>{campaign.campaign_type.replace(/_/g, " ")}</span>
              {campaign.due_date && <span>Due {new Date(campaign.due_date).toLocaleDateString()}</span>}
            </div>
          </div>
          <div className="flex gap-2">
            {campaign.status === "draft" && (
              <button onClick={() => launchMutation.mutate()} disabled={launchMutation.isPending}
                className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50">
                <Play className="w-4 h-4" /> {launchMutation.isPending ? "Launching..." : "Launch Collection"}
              </button>
            )}
            {canApprove && (
              <button onClick={() => approveMutation.mutate()} disabled={approveMutation.isPending || pending > 0}
                className="px-4 py-2 bg-green-600 text-white text-sm rounded-md hover:bg-green-700 disabled:opacity-50">
                {approveMutation.isPending ? "Approving..." : `Approve (${revokeCount} revocations)`}
              </button>
            )}
            {campaign.status === "completed" && (
              <button onClick={handleDownload}
                className="flex items-center gap-2 px-4 py-2 border border-slate-300 text-slate-700 text-sm rounded-md hover:bg-slate-50">
                <Download className="w-4 h-4" /> Download Evidence
              </button>
            )}
          </div>
        </div>
      </div>

      {campaign.status === "collecting" && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">
          Collecting access entries from connected systems... this usually completes in under a minute.
        </div>
      )}
      {campaign.error_message && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          Collection error: {campaign.error_message}
        </div>
      )}

      {total > 0 && (
        <div className="bg-white rounded-lg border border-slate-200 p-4">
          <div className="flex gap-6 text-sm mb-3">
            <span><strong>{total}</strong> total</span>
            <span className="text-green-600"><strong>{decided}</strong> decided</span>
            <span className="text-slate-400"><strong>{pending}</strong> pending</span>
            <span className="text-red-600"><strong>{revokeCount}</strong> revoke</span>
            {flagged > 0 && <span className="text-orange-600"><strong>{flagged}</strong> flagged</span>}
          </div>
          <div className="w-full bg-slate-100 rounded-full h-2">
            <div className="bg-brand-600 h-2 rounded-full transition-all"
              style={{ width: total ? `${(decided / total) * 100}%` : "0%" }} />
          </div>
        </div>
      )}

      {(campaign.status === "in_review" || campaign.status === "awaiting_approval" || campaign.status === "completed") && (
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700">Entries</span>
            <div className="flex gap-1 ml-auto">
              {(["all", "pending", "keep", "revoke"] as const).map((f) => (
                <button key={f} onClick={() => setDecisionFilter(f)}
                  className={`px-2 py-1 text-xs rounded transition-colors ${decisionFilter === f ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
                  {f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
          </div>
          {entriesLoading ? (
            <div className="py-8 text-center text-slate-400 text-sm">Loading entries...</div>
          ) : allEntries.length === 0 ? (
            <div className="py-8 text-center text-slate-400 text-sm">No entries match this filter.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-100">
                <tr>
                  <th className="text-left px-4 py-2 font-medium text-slate-600">User</th>
                  <th className="text-left px-4 py-2 font-medium text-slate-600">Resource</th>
                  <th className="text-left px-4 py-2 font-medium text-slate-600">Permission</th>
                  <th className="text-left px-4 py-2 font-medium text-slate-600">Risk</th>
                  <th className="text-left px-4 py-2 font-medium text-slate-600">Decision</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {allEntries.map((entry) => (
                  <tr key={entry.id} className={`hover:bg-slate-50 ${entry.decision === "revoke" ? "bg-red-50/30" : ""}`}>
                    <td className="px-4 py-3">
                      <p className="font-medium text-slate-900 truncate max-w-[180px]">{entry.user_display_name ?? entry.user_email}</p>
                      <p className="text-xs text-slate-400 truncate max-w-[180px]">{entry.user_email}</p>
                      {entry.user_status !== "active" && <span className="text-xs text-orange-600">{entry.user_status}</span>}
                    </td>
                    <td className="px-4 py-3">
                      <p className="truncate max-w-[180px]">{entry.resource_name}</p>
                      <p className="text-xs text-slate-400">{entry.resource_type}</p>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{entry.permission_level}</td>
                    <td className="px-4 py-3">
                      <RiskBadges entry={entry} />
                      {entry.evidence.last_login_at && (
                        <p className="text-xs text-slate-400 mt-1">Last login: {new Date(entry.evidence.last_login_at).toLocaleDateString()}</p>
                      )}
                      {entry.evidence.asset_criticality && (
                        <p className="text-xs text-slate-400">Asset: {entry.evidence.asset_criticality}</p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {canDecide ? (
                        <div className="space-y-1">
                          <div className="flex gap-1">
                            <button onClick={() => decisionMutation.mutate({ entryId: entry.id, decision: "keep" })}
                              className={`flex items-center gap-1 px-2 py-1 text-xs rounded border transition-colors ${entry.decision === "keep" ? "bg-green-600 text-white border-green-600" : "border-slate-300 text-slate-600 hover:bg-green-50"}`}>
                              <CheckCircle className="w-3 h-3" /> Keep
                            </button>
                            <button onClick={() => setNoteEntry(noteEntry === entry.id ? null : entry.id)}
                              className={`flex items-center gap-1 px-2 py-1 text-xs rounded border transition-colors ${entry.decision === "revoke" ? "bg-red-600 text-white border-red-600" : "border-slate-300 text-slate-600 hover:bg-red-50"}`}>
                              <XCircle className="w-3 h-3" /> Revoke
                            </button>
                          </div>
                          {noteEntry === entry.id && (
                            <div className="flex gap-1">
                              <input value={noteText} onChange={(e) => setNoteText(e.target.value)}
                                placeholder="Reason (optional)"
                                className="border border-slate-300 rounded px-2 py-1 text-xs flex-1 focus:outline-none focus:ring-1 focus:ring-brand-500"
                                autoFocus />
                              <button onClick={() => decisionMutation.mutate({ entryId: entry.id, decision: "revoke", note: noteText })}
                                className="px-2 py-1 bg-red-600 text-white text-xs rounded">
                                Confirm
                              </button>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="space-y-1">
                          <span className={`flex items-center gap-1 text-xs font-medium ${entry.decision === "keep" ? "text-green-600" : entry.decision === "revoke" ? "text-red-600" : "text-slate-400"}`}>
                            {entry.decision === "keep" && <CheckCircle className="w-3 h-3" />}
                            {entry.decision === "revoke" && <XCircle className="w-3 h-3" />}
                            {entry.decision ?? "Pending"}
                          </span>
                          {entry.decision === "revoke" && (
                            (entry as unknown as { remediation_cr_id?: string }).remediation_cr_id ? (
                              <Link
                                to={`/change-requests/${(entry as unknown as { remediation_cr_id: string }).remediation_cr_id}`}
                                className="text-xs text-brand-600 hover:underline"
                              >
                                View CR
                              </Link>
                            ) : (
                              <button
                                onClick={() => createRemediationCrMutation.mutate(entry.id)}
                                disabled={createRemediationCrMutation.isPending}
                                className="text-xs text-slate-500 hover:text-brand-600 underline disabled:opacity-50"
                              >
                                {createRemediationCrMutation.isPending ? "Creating..." : "Create Remediation CR"}
                              </button>
                            )
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
