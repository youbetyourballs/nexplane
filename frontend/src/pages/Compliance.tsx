import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, AlertTriangle, Download } from "lucide-react";
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

function ControlBreakdownTable({ controls }: { controls: ControlResult[] }) {
  return (
    <table className="w-full text-sm border-t border-slate-200 mt-2">
      <thead>
        <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
          <th className="py-1 pr-3 font-medium">ID</th>
          <th className="py-1 pr-3 font-medium">Section</th>
          <th className="py-1 pr-3 font-medium">Title</th>
          <th className="py-1 pr-3 font-medium">Status</th>
          <th className="py-1 pr-3 font-medium">Expected</th>
          <th className="py-1 font-medium">Actual</th>
        </tr>
      </thead>
      <tbody>
        {controls.map((c) => (
          <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50">
            <td className="py-1 pr-3 font-mono text-xs text-slate-600">{c.id}</td>
            <td className="py-1 pr-3 text-xs text-slate-500 capitalize">{c.section}</td>
            <td className="py-1 pr-3 text-slate-700">{c.title}</td>
            <td className="py-1 pr-3"><StatusIcon status={c.status} /> {c.status}</td>
            <td className="py-1 pr-3 font-mono text-xs text-slate-500 max-w-[200px] truncate">{c.expected}</td>
            <td className="py-1 font-mono text-xs text-slate-500 max-w-[200px] truncate">{c.actual}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Compliance() {
  const [expandedAsset, setExpandedAsset] = useState<string | null>(null);

  const { data: assets, isLoading } = useQuery<AssetWithCIS[]>({
    queryKey: ["assets-with-cis"],
    queryFn: () =>
      apiClient.get("/assets").then((r) =>
        r.data.filter((a: AssetWithCIS) => a.cis_compliance?.latest)
      ),
  });

  const { data: driftAlerts } = useQuery({
    queryKey: ["drift-alerts"],
    queryFn: () => apiClient.get("/compliance/drift-alerts").then((r) => r.data),
  });

  if (isLoading) return <PageLoading />;

  const audited = assets ?? [];
  const drifted = driftAlerts ?? [];

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Compliance</h1>
          <p className="text-slate-500 mt-1 text-sm">CIS Benchmark scores and drift alerts across your fleet</p>
        </div>
        <Link
          to="/change-requests/new?type=enforce_cis_benchmark"
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors"
        >
          <ShieldCheck size={16} />
          Run CIS Audit
        </Link>
      </div>

      {drifted.length > 0 && (
        <div className="mb-6 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <div className="flex items-center gap-2 text-yellow-800 font-semibold mb-2">
            <AlertTriangle size={18} />
            {drifted.length} open drift {drifted.length === 1 ? "alert" : "alerts"}
          </div>
          <ul className="space-y-1">
            {drifted.slice(0, 5).map((alert: any) => (
              <li key={alert.change_request_id} className="text-sm text-yellow-700">
                <Link
                  to={`/change-requests/${alert.change_request_id}`}
                  className="hover:underline font-medium"
                >
                  {alert.title}
                </Link>
                {" — "}
                <span className="text-yellow-600">
                  {formatDistanceToNow(new Date(alert.created_at), { addSuffix: true })}
                </span>
              </li>
            ))}
          </ul>
          {drifted.length > 5 && (
            <p className="text-xs text-yellow-600 mt-1">
              +{drifted.length - 5} more — <Link to="/change-requests?change_type=enforce_cis_benchmark&status=draft" className="underline">view all</Link>
            </p>
          )}
        </div>
      )}

      {audited.length === 0 ? (
        <div className="text-center text-slate-500 py-16">
          <ShieldCheck size={40} className="mx-auto mb-3 text-slate-300" />
          <p className="text-lg font-medium text-slate-600">No CIS audit data yet</p>
          <p className="text-sm mt-1">Run a CIS Audit change request to see compliance scores here.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {audited.map((asset) => {
            const latest = asset.cis_compliance!.latest!;
            const isExpanded = expandedAsset === asset.id;
            return (
              <div key={asset.id} className="bg-white rounded-lg border border-slate-200 overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-5 py-4 hover:bg-slate-50 transition-colors"
                  onClick={() => setExpandedAsset(isExpanded ? null : asset.id)}
                >
                  <div className="flex items-center gap-3">
                    <ScoreBadge score={latest.score} />
                    <span className="font-medium text-slate-800">{asset.name || asset.id}</span>
                    <span className="text-xs text-slate-400">
                      Level {latest.level} · audited {formatDistanceToNow(new Date(latest.collected_at), { addSuffix: true })}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <a
                      href={`/compliance/evidence-collection?asset_id=${asset.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800"
                    >
                      <Download size={13} />
                      Evidence
                    </a>
                    <span className="text-slate-400 text-sm">{isExpanded ? "▲" : "▼"}</span>
                  </div>
                </button>
                {isExpanded && (
                  <div className="px-5 pb-4">
                    <ControlBreakdownTable controls={latest.controls} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
