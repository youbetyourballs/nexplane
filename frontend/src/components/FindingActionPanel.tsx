// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

const MitigationPanel = React.lazy(() => import("./MitigationPanel"));

interface Finding {
  id: string;
  cve_id: string | null;
  title: string;
  description: string | null;
  affected_package: string | null;
  affected_version: string | null;
  fixed_version: string | null;
  asset_id: string | null;
  asset_name: string | null;
  severity: string;
  status: string;
  assigned_to_user_id: string | null;
  exploitability_result: string | null;
  poc_source: string | null;
  poc_ref: string | null;
  verification_result: string | null;
  verified_at: string | null;
  verification_failed: boolean;
}

interface FindingCR {
  id: string;
  cr_id: string;
  role: string;
  created_at: string;
  cr_status: string;
  cr_title: string;
}

interface Props {
  finding: Finding;
  onClose: () => void;
  onUpdated: () => void;
}

export default function FindingActionPanel({ finding, onClose, onUpdated }: Props) {
  const qc = useQueryClient();
  const [acceptReason, setAcceptReason] = useState("");
  const [acceptExpiry, setAcceptExpiry] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 90);
    return d.toISOString().split("T")[0];
  });
  const [activeAction, setActiveAction] = useState<string | null>(null);

  const patchMutation = useMutation({
    mutationFn: () => apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/patch`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const acceptMutation = useMutation({
    mutationFn: () => apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/accept-risk`, {
      reason: acceptReason,
      expires_at: new Date(acceptExpiry + "T23:59:59Z").toISOString(),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const fpMutation = useMutation({
    mutationFn: () => apiClient.patch(`/api/v1/vulnerability/findings/${finding.id}/status`, {
      status: "false_positive", reason: "Marked via action panel"
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const { data: crList } = useQuery<FindingCR[]>({
    queryKey: ["finding-crs", finding.id],
    queryFn: () =>
      apiClient
        .get<FindingCR[]>(`/api/v1/vulnerability/findings/${finding.id}/change-requests`)
        .then((r) => r.data),
    refetchInterval: 10000,
  });

  const pocMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/poc-validate`, {
        asset_id: finding.asset_id,
      }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const challengeMutation = useMutation({
    mutationFn: (reason: string) =>
      apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/challenge`, { reason }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const verifyMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/verify`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const [challengeReason, setChallengeReason] = useState("");
  const [showChallengeInput, setShowChallengeInput] = useState(false);

  function pocBadge() {
    if (finding.poc_source === "cisa_kev") return <span className="badge-red">KEV confirmed</span>;
    if (finding.exploitability_result === "exploited") return <span className="badge-red">PoC — exploited</span>;
    if (finding.exploitability_result === "not_exploited") return <span className="badge-green">PoC — not exploited</span>;
    if (finding.exploitability_result === "inconclusive") return <span className="badge-yellow">PoC — inconclusive</span>;
    return <span className="badge-gray">No PoC run</span>;
  }

  return (
    <div className="bg-slate-50 border-t border-slate-200 px-4 py-4 space-y-3">
      {/* CVE summary */}
      <div className="text-sm text-slate-600">
        {finding.description && <p className="mb-2">{finding.description}</p>}
        {finding.affected_package && (
          <p className="font-mono text-xs bg-white border border-slate-200 rounded px-2 py-1 inline-block">
            {finding.affected_package} {finding.affected_version}
            {finding.fixed_version && <> → <span className="text-green-700">{finding.fixed_version}</span></>}
          </p>
        )}
      </div>

      {/* Exploitability bar */}
      <div className="flex items-center gap-3 flex-wrap">
        {finding.cve_id && <span className="font-mono text-sm text-slate-600">{finding.cve_id}</span>}
        {pocBadge()}
        {finding.poc_source !== "cisa_kev" && (
          <button
            className="text-xs text-blue-600 underline disabled:opacity-40"
            disabled={finding.status === "challenged" || pocMutation.isPending}
            onClick={() => setShowChallengeInput(true)}
          >
            Challenge exploitability
          </button>
        )}
        {pocMutation.isPending && <span className="text-xs text-slate-400">Running PoC…</span>}
        {!finding.exploitability_result && (
          <button
            className="text-xs text-blue-600 underline disabled:opacity-40"
            disabled={pocMutation.isPending}
            onClick={() => pocMutation.mutate()}
          >
            Run PoC validation
          </button>
        )}
      </div>

      {showChallengeInput && (
        <div className="flex gap-2 items-start">
          <textarea
            className="flex-1 border rounded p-1 text-sm"
            rows={2}
            placeholder="Reason this finding is not exploitable in your environment…"
            value={challengeReason}
            onChange={(e) => setChallengeReason(e.target.value)}
          />
          <button
            className="text-sm bg-orange-500 text-white px-2 py-1 rounded disabled:opacity-40"
            disabled={!challengeReason.trim() || challengeMutation.isPending}
            onClick={() => {
              challengeMutation.mutate(challengeReason);
              setShowChallengeInput(false);
            }}
          >
            Submit
          </button>
        </div>
      )}

      {/* Active CR tracker */}
      {crList && crList.length > 0 && (
        <div className="border rounded p-2 space-y-1">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Active Remediation CRs</p>
          {crList.map((cr) => (
            <div key={cr.id} className="flex items-center justify-between text-sm">
              <span className="text-slate-700">
                <span className="capitalize text-xs bg-slate-100 px-1 rounded mr-1">{cr.role}</span>
                {cr.cr_title}
              </span>
              <span className={`text-xs font-mono ${cr.cr_status === "executed" ? "text-green-600" : "text-slate-400"}`}>
                {cr.cr_status}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Verification result */}
      {finding.verification_result && (
        <div className={`text-sm px-3 py-2 rounded ${finding.verification_result === "resolved" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
          {finding.verification_result === "resolved" && "Scanner confirmed resolved"}
          {finding.verification_result === "still_vulnerable" && "Scanner: still vulnerable — add mitigations or escalate"}
          {finding.verification_result === "inconclusive" && "Verification inconclusive — re-run manually"}
          {finding.verified_at && (
            <span className="text-xs ml-2 opacity-60">{new Date(finding.verified_at).toLocaleString()}</span>
          )}
          <button
            className="ml-3 text-xs underline"
            disabled={verifyMutation.isPending}
            onClick={() => verifyMutation.mutate()}
          >
            Re-verify now
          </button>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        {finding.asset_id && (
          <button
            onClick={() => patchMutation.mutate()}
            disabled={patchMutation.isPending}
            className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {patchMutation.isPending ? "Creating CR…" : "Patch"}
          </button>
        )}
        <button
          onClick={() => setActiveAction(activeAction === "mitigate" ? null : "mitigate")}
          className="px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700"
        >
          Mitigate
        </button>
        <button
          onClick={() => setActiveAction(activeAction === "accept" ? null : "accept")}
          className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-100"
        >
          Accept Risk
        </button>
        <button
          onClick={() => fpMutation.mutate()}
          disabled={fpMutation.isPending}
          className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-100 disabled:opacity-50"
        >
          False Positive
        </button>
        <a
          href={`/vulnerability?tab=findings&blast_radius=${finding.cve_id || ""}`}
          className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-100"
        >
          Blast Radius →
        </a>
      </div>

      {/* Accept Risk sub-panel */}
      {activeAction === "accept" && (
        <div className="bg-white border border-slate-200 rounded p-3 space-y-2">
          <p className="text-xs font-medium text-slate-700">Accept Risk</p>
          <input
            className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
            placeholder="Reason (required)"
            value={acceptReason}
            onChange={e => setAcceptReason(e.target.value)}
          />
          <div className="flex gap-2 items-center">
            <label className="text-xs text-slate-500">Expires</label>
            <input
              type="date"
              className="border border-slate-300 rounded px-2 py-1 text-sm"
              value={acceptExpiry}
              onChange={e => setAcceptExpiry(e.target.value)}
            />
          </div>
          <button
            onClick={() => acceptMutation.mutate()}
            disabled={!acceptReason || acceptMutation.isPending}
            className="px-3 py-1.5 bg-slate-700 text-white text-sm rounded hover:bg-slate-800 disabled:opacity-50"
          >
            {acceptMutation.isPending ? "Saving…" : "Confirm Accept Risk"}
          </button>
        </div>
      )}

      {/* Mitigate sub-panel placeholder — wired in Task 8 */}
      {activeAction === "mitigate" && (
        <MitigationInlinePanelLoader findingId={finding.id} onApplied={onUpdated} />
      )}

      {/* Status feedback */}
      {patchMutation.isSuccess && (
        <p className="text-sm text-green-700">✓ Patch CR created — awaiting approval</p>
      )}
      {acceptMutation.isSuccess && (
        <p className="text-sm text-green-700">✓ Risk accepted until {acceptExpiry}</p>
      )}
      {patchMutation.isError && (
        <p className="text-sm text-red-600">✗ Failed to create patch CR. Please try again.</p>
      )}
      {acceptMutation.isError && (
        <p className="text-sm text-red-600">✗ Failed to accept risk. Please try again.</p>
      )}
      {fpMutation.isSuccess && (
        <p className="text-sm text-green-700">✓ Marked as false positive</p>
      )}
      {fpMutation.isError && (
        <p className="text-sm text-red-600">✗ Failed to mark as false positive.</p>
      )}
    </div>
  );
}

function MitigationInlinePanelLoader({ findingId, onApplied }: { findingId: string; onApplied: () => void }) {
  return (
    <React.Suspense fallback={<p className="text-sm text-slate-500">Loading suggestions…</p>}>
      <MitigationPanel findingId={findingId} onApplied={onApplied} />
    </React.Suspense>
  );
}
