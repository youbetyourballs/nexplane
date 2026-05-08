import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight, ChevronDown, Play, Loader2,
  CheckCircle2, AlertTriangle, XCircle, Minus,
} from "lucide-react";
import { apiClient } from "../api/client";
import { complianceApi, CisControlRow, CisCheckRow } from "../api/endpoints";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ score, method }: { score: number | null; method: string }) {
  if (method === "not_tracked") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
        <Minus size={12} /> Not tracked
      </span>
    );
  }
  if (score === null) {
    return <span className="text-xs text-slate-400">No data</span>;
  }
  if (score >= 0.8) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700">
        <CheckCircle2 size={13} /> Passing
      </span>
    );
  }
  if (score >= 0.6) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-600">
        <AlertTriangle size={13} /> At risk
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
      <XCircle size={13} /> Failing
    </span>
  );
}

// ── Score bar ─────────────────────────────────────────────────────────────────

function ScoreBar({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="text-slate-300 text-sm">—</span>;
  }
  const pct = Math.round(score * 100);
  const color =
    score >= 0.8 ? "bg-green-500" :
    score >= 0.6 ? "bg-amber-400" :
    "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-sm tabular-nums text-slate-700">{pct}%</span>
    </div>
  );
}

// ── Failing asset list ────────────────────────────────────────────────────────

function FailingAssetList({ check }: { check: CisCheckRow }) {
  if (check.failing_assets.length === 0) return null;
  return (
    <div className="ml-8 mt-1 mb-2 bg-red-50 border border-red-100 rounded-md overflow-hidden">
      <div className="px-3 py-1 border-b border-red-100 text-xs font-medium text-red-700 uppercase tracking-wide">
        Failing assets
      </div>
      <table className="w-full text-xs">
        <tbody>
          {check.failing_assets.map((a) => (
            <tr key={a.id} className="border-b border-red-50 last:border-0">
              <td className="px-3 py-1.5 font-medium text-slate-800 w-40">{a.name}</td>
              <td className="px-3 py-1.5 text-slate-500 font-mono">{a.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Check row (expandable) ────────────────────────────────────────────────────

function CheckRow({ check }: { check: CisCheckRow }) {
  const [expanded, setExpanded] = useState(false);
  const hasFailing = check.fail_count > 0;
  return (
    <div>
      <div
        className={`flex items-center gap-2 px-4 py-1.5 text-sm hover:bg-slate-50 ${hasFailing ? "cursor-pointer" : "cursor-default"}`}
        onClick={() => hasFailing && setExpanded((e) => !e)}
      >
        {hasFailing ? (
          expanded ? <ChevronDown size={12} className="text-slate-400 shrink-0" /> : <ChevronRight size={12} className="text-slate-400 shrink-0" />
        ) : (
          <span className="w-3" />
        )}
        <span className="w-8 text-xs text-slate-400 shrink-0">{check.id}</span>
        <span className="flex-1 text-slate-700">{check.title}</span>
        <span className="w-10 text-center text-xs text-green-600">{check.pass_count}</span>
        <span className="w-10 text-center text-xs text-red-500">{check.fail_count}</span>
        <span className="w-10 text-center text-xs text-slate-400">{check.pass_count + check.fail_count}</span>
      </div>
      {expanded && <FailingAssetList check={check} />}
    </div>
  );
}

// ── Control row (expandable) ──────────────────────────────────────────────────

function ControlRow({ ctrl }: { ctrl: CisControlRow }) {
  const [expanded, setExpanded] = useState(false);
  const isTracked = ctrl.method !== "not_tracked";
  const hasData = ctrl.score !== null;

  return (
    <div className={`border-b border-slate-100 ${!isTracked ? "opacity-50" : ""}`}>
      {/* Main row */}
      <div
        className={`flex items-center gap-3 px-4 py-3 ${isTracked ? "cursor-pointer hover:bg-slate-50" : ""}`}
        onClick={() => isTracked && setExpanded((e) => !e)}
      >
        {isTracked ? (
          expanded
            ? <ChevronDown size={14} className="text-slate-400 shrink-0" />
            : <ChevronRight size={14} className="text-slate-400 shrink-0" />
        ) : (
          <span className="w-3.5" />
        )}
        <span className="w-6 text-xs font-mono text-slate-400 shrink-0">{ctrl.id}</span>
        <span className="flex-1 text-sm font-medium text-slate-800">{ctrl.name}</span>
        <div className="w-32">
          <ScoreBar score={ctrl.score} />
        </div>
        <div className="w-20 text-sm text-slate-500 text-center">
          {hasData ? `${ctrl.assets_passing}/${ctrl.assets_total}` : "—"}
        </div>
        <div className="w-28 text-right">
          <StatusBadge score={ctrl.score} method={ctrl.method} />
        </div>
      </div>

      {/* Expanded: checks sub-table */}
      {expanded && ctrl.checks.length > 0 && (
        <div className="bg-slate-50 border-t border-slate-100">
          {/* Checks header */}
          <div className="flex items-center gap-2 px-4 py-1 text-xs font-medium text-slate-500 uppercase tracking-wide border-b border-slate-100">
            <span className="w-3" />
            <span className="w-8" />
            <span className="flex-1">Check</span>
            <span className="w-10 text-center text-green-600">Pass</span>
            <span className="w-10 text-center text-red-500">Fail</span>
            <span className="w-10 text-center">Total</span>
          </div>
          {ctrl.checks.map((ch) => (
            <CheckRow key={ch.id + ch.title} check={ch} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Compliance() {
  const queryClient = useQueryClient();

  const { data: summary, isLoading } = useQuery({
    queryKey: ["cis-summary"],
    queryFn: complianceApi.getSummary,
    refetchInterval: 30_000,
  });

  const runAuditMutation = useMutation({
    mutationFn: async () => {
      const assets: { id: string }[] = await apiClient
        .get("/assets", { params: { asset_type: "server" } })
        .then((r) => r.data);

      await Promise.all(
        assets.map(async (asset) => {
          try {
            const cr = await apiClient
              .post("/change-requests", {
                title: "CIS v8 Full Audit",
                description: "Full CIS Controls v8 compliance audit",
                change_type: "agent_compliance",
                target_asset_ids: [asset.id],
                desired_outcome: { dry_run: false },
              })
              .then((r) => r.data);
            const crId = cr.id;
            await apiClient.post(`/change-requests/${crId}/plan`);
            await apiClient.post(`/change-requests/${crId}/submit-for-approval`);
            await apiClient.post(`/change-requests/${crId}/approve`, {
              decision: "approved",
              comment: "Auto-approved via Run Full Audit",
            });
            await apiClient.post(`/change-requests/${crId}/execute`);
          } catch {
            // Skip assets where audit fails (e.g., no agent registered)
          }
        })
      );
    },
    onSuccess: () => {
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["cis-summary"] }), 5000);
    },
  });

  if (isLoading) return <PageLoading />;

  const overallPct = summary?.overall_score != null
    ? Math.round(summary.overall_score * 100)
    : null;

  const overallColor =
    overallPct == null ? "bg-slate-300" :
    overallPct >= 80    ? "bg-green-500" :
    overallPct >= 60    ? "bg-amber-400" :
    "bg-red-500";

  return (
    <div className="max-w-5xl mx-auto py-8 px-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Compliance</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            CIS Controls v8
            {summary?.last_updated && (
              <> · Last updated {formatDistanceToNow(new Date(summary.last_updated), { addSuffix: true })}</>
            )}
          </p>
        </div>
        <button
          onClick={() => runAuditMutation.mutate()}
          disabled={runAuditMutation.isPending}
          className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 transition-colors"
        >
          {runAuditMutation.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Play size={14} />
          )}
          Run Full Audit
        </button>
      </div>

      {/* Overall score bar */}
      {summary && (
        <div className="bg-white border border-slate-200 rounded-lg p-4 mb-6">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-slate-700">
              Overall — {summary.tracked_controls} of 18 controls tracked
            </span>
            <span className="text-lg font-semibold text-slate-900">
              {overallPct != null ? `${overallPct}%` : "—"}
            </span>
          </div>
          <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${overallColor}`}
              style={{ width: overallPct != null ? `${overallPct}%` : "0%" }}
            />
          </div>
        </div>
      )}

      {/* 18-control table */}
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        {/* Table header */}
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-200 bg-slate-50 text-xs font-medium text-slate-500 uppercase tracking-wide">
          <span className="w-3.5" />
          <span className="w-6">#</span>
          <span className="flex-1">Control</span>
          <span className="w-32">Score</span>
          <span className="w-20 text-center">Assets</span>
          <span className="w-28 text-right">Status</span>
        </div>

        {summary?.controls.map((ctrl) => (
          <ControlRow key={ctrl.id} ctrl={ctrl} />
        ))}

        {!summary && (
          <div className="text-center py-12 text-slate-400 text-sm">
            No compliance data yet — run an audit to populate scores
          </div>
        )}
      </div>
    </div>
  );
}

export default Compliance;
