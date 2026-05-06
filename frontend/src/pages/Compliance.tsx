import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, AlertTriangle, Download, Play, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";

interface CISLatest {
  score: number;
  level: number;
  collected_at: string;
  controls: ControlResult[];
}

interface ControlResult {
  id: string;
  title: string;
  section: string;
  status: "pass" | "fail" | "skip";
  expected: string;
  actual: string;
}

interface AssetWithCIS {
  id: string;
  name: string;
  cis_compliance?: { latest?: CISLatest };
}

function ScoreBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    score >= 0.8 ? "bg-green-100 text-green-800" :
    score >= 0.6 ? "bg-yellow-100 text-yellow-800" :
    "bg-red-100 text-red-800";
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${color}`}>
      {pct}%
    </span>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "pass") return <ShieldCheck size={16} className="text-green-600 inline" />;
  if (status === "fail") return <ShieldAlert size={16} className="text-red-500 inline" />;
  return <AlertTriangle size={16} className="text-slate-400 inline" />;
}

function ScoreTrend({ history }: { history: { score: number; collected_at: string }[] }) {
  if (!history || history.length < 2) return null;
  const recent = history.slice(-5);
  const max = Math.max(...recent.map(h => h.score));
  return (
    <div className="flex items-end gap-0.5 h-5 ml-2" title="Score trend (last 5 audits)">
      {recent.map((h, i) => (
        <div
          key={i}
          className="w-1.5 rounded-sm bg-blue-400 opacity-70"
          style={{ height: `${Math.round((h.score / (max || 1)) * 100)}%` }}
        />
      ))}
    </div>
  );
}

function ControlBreakdownTable({ controls }: { controls: ControlResult[] }) {
  const failing = controls.filter(c => c.status === "fail");
  const passing = controls.filter(c => c.status === "pass");
  return (
    <div>
      {failing.length > 0 && (
        <div className="mb-2">
          <p className="text-xs font-semibold text-red-600 mb-1">Failing controls ({failing.length})</p>
          <table className="w-full text-sm border-t border-slate-200">
            <thead>
              <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
                <th className="py-1 pr-3 font-medium">ID</th>
                <th className="py-1 pr-3 font-medium">Section</th>
                <th className="py-1 pr-3 font-medium">Title</th>
                <th className="py-1 pr-3 font-medium">Expected</th>
                <th className="py-1 font-medium">Actual</th>
              </tr>
            </thead>
            <tbody>
              {failing.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 bg-red-50/30">
                  <td className="py-1 pr-3 font-mono text-xs text-slate-600">{c.id}</td>
                  <td className="py-1 pr-3 text-xs text-slate-500 capitalize">{c.section}</td>
                  <td className="py-1 pr-3 text-slate-700">{c.title}</td>
                  <td className="py-1 pr-3 font-mono text-xs text-slate-500 max-w-[150px] truncate">{c.expected}</td>
                  <td className="py-1 font-mono text-xs text-red-600 max-w-[150px] truncate">{c.actual}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {passing.length > 0 && (
        <details className="mt-1">
          <summary className="text-xs text-slate-400 cursor-pointer select-none">
            {passing.length} passing controls
          </summary>
          <table className="w-full text-sm border-t border-slate-100 mt-1">
            <tbody>
              {passing.map((c) => (
                <tr key={c.id} className="border-t border-slate-50">
                  <td className="py-0.5 pr-3 font-mono text-xs text-slate-400">{c.id}</td>
                  <td className="py-0.5 pr-3 text-xs text-slate-400 capitalize">{c.section}</td>
                  <td className="py-0.5 text-xs text-slate-500">{c.title}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

export function Compliance() {
  const [expandedAsset, setExpandedAsset] = useState<string | null>(null);
  const [auditingAssets, setAuditingAssets] = useState<Set<string>>(new Set());
  const queryClient = useQueryClient();

  const { data: allAssets, isLoading } = useQuery<AssetWithCIS[]>({
    queryKey: ["assets-with-cis"],
    queryFn: () => apiClient.get("/assets").then((r) => r.data),
    refetchInterval: auditingAssets.size > 0 ? 8000 : false,
  });

  const { data: driftAlerts } = useQuery({
    queryKey: ["drift-alerts"],
    queryFn: () => apiClient.get("/compliance/drift-alerts").then((r) => r.data),
  });

  const runAuditMutation = useMutation({
    mutationFn: async (asset: AssetWithCIS) => {
      // Create an agent_compliance CR targeting this asset
      const cr = await apiClient.post("/change-requests", {
        title: `CIS Audit — ${asset.name}`,
        description: `On-demand CIS compliance audit for ${asset.name}`,
        change_type: "agent_compliance",
        target_asset_ids: [asset.id],
        desired_outcome: { dry_run: true },
      });
      const crId = cr.data.id;
      // Plan → approve → execute
      await apiClient.post(`/change-requests/${crId}/plan`);
      await apiClient.post(`/change-requests/${crId}/submit-for-approval`);
      await apiClient.post(`/change-requests/${crId}/approve`, {
        decision: "approved",
        comment: "On-demand audit",
      });
      await apiClient.post(`/change-requests/${crId}/execute`);
      return crId;
    },
    onMutate: (asset) => {
      setAuditingAssets(prev => new Set([...prev, asset.id]));
    },
    onSettled: (_data, _error, asset) => {
      // Poll for results — remove from auditing set after 60s or when results appear
      setTimeout(() => {
        setAuditingAssets(prev => {
          const next = new Set(prev);
          next.delete(asset.id);
          return next;
        });
        queryClient.invalidateQueries({ queryKey: ["assets-with-cis"] });
      }, 60000);
    },
  });

  if (isLoading) return <PageLoading />;

  const assets = allAssets ?? [];
  const audited = assets.filter((a: AssetWithCIS) => a.cis_compliance?.latest);
  const unaudited = assets.filter((a: AssetWithCIS) => !a.cis_compliance?.latest && a.id);
  const drifted = driftAlerts ?? [];

  return (
    <div className="max-w-4xl mx-auto py-8 px-4 space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Compliance</h1>
          <p className="text-slate-500 text-sm mt-1">
            CIS benchmark scores for audited hosts. Run audits per-asset below.
          </p>
        </div>
        <Link
          to="/change-requests/new?change_type=enforce_cis_benchmark"
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
        >
          <ShieldCheck size={16} />
          New CIS Campaign
        </Link>
      </div>

      {drifted.length > 0 && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4">
          <p className="text-yellow-800 font-medium text-sm flex items-center gap-1">
            <AlertTriangle size={16} /> {drifted.length} asset(s) drifted from baseline
          </p>
          <ul className="mt-2 text-yellow-700 text-sm list-disc list-inside">
            {drifted.slice(0, 5).map((d: any) => (
              <li key={d.asset_id}>{d.asset_name}: {d.drifted_controls?.length ?? 0} control(s)</li>
            ))}
          </ul>
        </div>
      )}

      {/* Audited assets */}
      {audited.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-base font-semibold text-slate-700">Audited Assets</h2>
          {audited.map((asset) => {
            const latest = asset.cis_compliance!.latest!;
            const history = (asset.cis_compliance as any)?.history ?? [];
            const isAuditing = auditingAssets.has(asset.id);
            const failCount = latest.controls?.filter((c: ControlResult) => c.status === "fail").length ?? 0;
            return (
              <div key={asset.id} className="border border-slate-200 rounded-xl bg-white shadow-sm">
                <div
                  className="flex items-center justify-between px-5 py-4 cursor-pointer"
                  onClick={() => setExpandedAsset(expandedAsset === asset.id ? null : asset.id)}
                >
                  <div className="flex items-center gap-3">
                    <ScoreBadge score={latest.score} />
                    <ScoreTrend history={history} />
                    <div>
                      <p className="font-medium text-slate-900">{asset.name}</p>
                      <p className="text-xs text-slate-400">
                        Last audited {formatDistanceToNow(new Date(latest.collected_at), { addSuffix: true })}
                        {failCount > 0 && <span className="text-red-500 ml-2">· {failCount} failing</span>}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => { e.stopPropagation(); runAuditMutation.mutate(asset); }}
                      disabled={isAuditing}
                      className="inline-flex items-center gap-1 px-2.5 py-1 text-xs text-slate-500 hover:text-blue-600 border border-slate-200 hover:border-blue-300 rounded-lg transition-colors disabled:opacity-50"
                    >
                      {isAuditing ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                      {isAuditing ? "Auditing..." : "Re-audit"}
                    </button>
                    <Link
                      to={`/compliance/evidence-collection?asset_id=${asset.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-blue-600"
                    >
                      <Download size={12} /> Evidence
                    </Link>
                  </div>
                </div>
                {expandedAsset === asset.id && latest.controls && (
                  <div className="px-5 pb-4">
                    <ControlBreakdownTable controls={latest.controls} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Unaudited assets with Run Audit button */}
      {unaudited.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-base font-semibold text-slate-700">Not Yet Audited</h2>
          <div className="grid grid-cols-1 gap-2">
            {unaudited.slice(0, 10).map((asset) => {
              const isAuditing = auditingAssets.has(asset.id);
              return (
                <div key={asset.id} className="flex items-center justify-between px-4 py-3 border border-slate-100 rounded-lg bg-slate-50/50">
                  <span className="text-sm text-slate-600">{asset.name}</span>
                  <button
                    onClick={() => runAuditMutation.mutate(asset)}
                    disabled={isAuditing}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {isAuditing ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                    {isAuditing ? "Running..." : "Run Audit"}
                  </button>
                </div>
              );
            })}
            {unaudited.length > 10 && (
              <p className="text-xs text-slate-400 px-4">+{unaudited.length - 10} more assets</p>
            )}
          </div>
        </div>
      )}

      {audited.length === 0 && unaudited.length === 0 && (
        <div className="text-center py-16 text-slate-400">
          <ShieldCheck size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">No assets found. Add connectors to discover assets.</p>
        </div>
      )}
    </div>
  );
}
