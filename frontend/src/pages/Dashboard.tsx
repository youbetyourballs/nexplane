import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, Play, XCircle, Activity, Plug } from "lucide-react";
import { changeRequestsApi } from "../api/endpoints";
import { apiClient } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { RiskBadge } from "../components/RiskBadge";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";
import type { ChangeRequestSummary, ConnectorRead } from "../types/api";

function StatCard({
  label,
  value,
  icon: Icon,
  color,
  linkTo,
}: {
  label: string;
  value: number;
  icon: React.ElementType;
  color: string;
  linkTo?: string;
}) {
  const content = (
    <div className="bg-white rounded-lg border border-slate-200 p-5 hover:border-slate-300 transition-colors">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-2xl font-bold text-slate-900">{value}</div>
          <div className="text-sm text-slate-500 mt-0.5">{label}</div>
        </div>
        <div className={`p-2.5 rounded-lg ${color}`}>
          <Icon className="w-5 h-5" />
        </div>
      </div>
    </div>
  );
  return linkTo ? <Link to={linkTo}>{content}</Link> : content;
}

const ALL_CONNECTOR_TYPES = ["aws", "azure_ad", "ldap", "gcp", "azure", "okta", "tailscale", "crowdstrike", "tenable"] as const;

const CONNECTOR_TYPE_LABELS: Record<string, string> = {
  aws: "AWS",
  azure_ad: "Azure AD",
  ldap: "LDAP / AD",
  gcp: "GCP",
  azure: "Azure",
  okta: "Okta",
  tailscale: "Tailscale",
  crowdstrike: "CrowdStrike",
  tenable: "Tenable",
};

const CONNECTOR_TYPE_ICONS: Record<string, string> = {
  aws: "☁️",
  azure_ad: "👥",
  ldap: "🏢",
  gcp: "☁️",
  azure: "🔷",
  okta: "🔐",
  tailscale: "🔒",
  crowdstrike: "🦅",
  tenable: "🔍",
};

export function Dashboard() {
  const { data: all, isLoading } = useQuery({
    queryKey: ["change-requests"],
    queryFn: () => changeRequestsApi.list(),
  });

  const { data: connectors } = useQuery<ConnectorRead[]>({
    queryKey: ["connectors"],
    queryFn: () => apiClient.get("/connectors").then((r) => r.data),
  });

  const { data: checklist } = useQuery({
    queryKey: ["onboarding-checklist"],
    queryFn: () => apiClient.get("/onboarding/checklist").then((r) => r.data as {
      steps: { id: string; label: string; complete: boolean; detail: string }[];
      connector_count: number;
      asset_count: number;
    }),
  });

  const { data: allAssets } = useQuery<{ id: string; name: string; criticality: string; environment: string; asset_type: string }[]>({
    queryKey: ["assets", {}],
    queryFn: () => apiClient.get("/assets").then((r) => r.data),
    staleTime: 120_000,
  });

  if (isLoading) return <PageLoading />;

  const crs = all ?? [];
  const awaiting = crs.filter((c) => c.status === "awaiting_approval");
  const executing = crs.filter((c) =>
    ["executing", "verifying"].includes(c.status)
  );
  const failed = crs.filter((c) =>
    ["failed", "rolled_back"].includes(c.status)
  );
  const completed = crs.filter((c) => c.status === "completed");
  const highRisk = crs.filter((c) =>
    ["high", "critical"].includes(c.risk_level)
  );
  const recent = [...crs].slice(0, 8);

  const allChecklistDone = checklist?.steps?.every((s) => s.complete) ?? true;

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-xl font-semibold text-slate-900">Operations Dashboard</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Governed infrastructure change — real-time status
        </p>
      </div>

      <ConnectorHealthBanner connectors={connectors ?? []} />

      {/* Onboarding checklist — shown until all steps complete */}
      {!allChecklistDone && checklist && (
        <div className="mb-6 p-4 rounded-xl border border-indigo-200 bg-indigo-50">
          <h3 className="text-sm font-semibold text-indigo-900 mb-3">Get started with Nexplane</h3>
          <div className="space-y-2">
            {checklist.steps.map((step) => (
              <div key={step.id} className="flex items-start gap-2 text-sm">
                <span className={step.complete ? "text-green-600 mt-0.5" : "text-slate-400 mt-0.5"}>
                  {step.complete ? <CheckCircle2 size={14} /> : <Clock size={14} />}
                </span>
                <div>
                  <span className={step.complete ? "line-through text-slate-400" : "text-slate-700 font-medium"}>
                    {step.label}
                  </span>
                  <p className="text-xs text-slate-500">{step.detail}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-indigo-100">
            <Link to="/connectors" className="text-xs font-medium text-indigo-700 hover:text-indigo-900">
              Configure connectors →
            </Link>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-8">
        <StatCard
          label="Total"
          value={crs.length}
          icon={Activity}
          color="bg-slate-100 text-slate-600"
          linkTo="/change-requests"
        />
        <StatCard
          label="Approval Required"
          value={awaiting.length}
          icon={Clock}
          color="bg-amber-100 text-amber-700"
          linkTo="/approvals"
        />
        <StatCard
          label="Executing"
          value={executing.length}
          icon={Play}
          color="bg-blue-100 text-blue-700"
        />
        <StatCard
          label="Failed"
          value={failed.length}
          icon={XCircle}
          color="bg-red-100 text-red-700"
        />
        <StatCard
          label="Completed"
          value={completed.length}
          icon={CheckCircle2}
          color="bg-emerald-100 text-emerald-700"
        />
        <StatCard
          label="High-Risk Changes"
          value={highRisk.length}
          icon={AlertTriangle}
          color="bg-orange-100 text-orange-700"
        />
      </div>

      <InFlightPanel crs={executing} />
      <ExposureSummary assets={allAssets ?? []} />
      <TopRisksPanel assets={allAssets ?? []} />

      {/* Connector Status Widget */}
      <div className="bg-white rounded-lg border border-slate-200 mb-6">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Plug className="w-4 h-4 text-slate-400" />
            <h2 className="text-sm font-semibold text-slate-900">Connector Status</h2>
          </div>
          <Link to="/settings" className="text-xs text-brand-600 hover:text-brand-700 font-medium">
            Settings →
          </Link>
        </div>
        <div className="px-5 py-3 grid grid-cols-3 sm:grid-cols-5 gap-3">
          {ALL_CONNECTOR_TYPES.map((type) => {
            const configured = (connectors ?? []).find((c) => c.connector_type === type);
            return (
              <div key={type} className={`flex flex-col items-center gap-1 p-2 rounded-lg border text-center ${
                configured ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-slate-50"
              }`}>
                <span className="text-lg">{CONNECTOR_TYPE_ICONS[type]}</span>
                <span className="text-xs font-medium text-slate-700">{CONNECTOR_TYPE_LABELS[type]}</span>
                {configured ? (
                  <span className="text-xs text-emerald-600">● {configured.status}</span>
                ) : (
                  <Link to="/settings" className="text-xs text-indigo-500 hover:underline">+ Add</Link>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-lg border border-slate-200">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Recent Change Requests</h2>
          <Link
            to="/change-requests"
            className="text-xs text-brand-600 hover:text-brand-700 font-medium"
          >
            View all →
          </Link>
        </div>
        <div className="divide-y divide-slate-50">
          {recent.length === 0 && (
            <div className="px-5 py-8 text-center text-slate-400 text-sm">
              No change requests yet.{" "}
              <Link to="/change-requests/new" className="text-brand-600 hover:underline">
                Create one
              </Link>
            </div>
          )}
          {recent.map((cr) => (
            <ChangeRequestRow key={cr.id} cr={cr} />
          ))}
        </div>
      </div>
    </div>
  );
}

function ConnectorHealthBanner({ connectors }: { connectors: ConnectorRead[] }) {
  const failing = connectors.filter(
    (c) => c.status === "error" || c.status === "credential_expired"
  );
  if (failing.length === 0) return null;
  return (
    <div className="mb-6 flex items-start gap-3 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg">
      <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-amber-900">
          {failing.length} connector {failing.length === 1 ? "issue" : "issues"} detected
        </p>
        <p className="text-xs text-amber-700 mt-0.5">
          {failing.map((c) => c.name).join(", ")} —{" "}
          {failing.some((c) => c.status === "credential_expired")
            ? "credentials expired or auth failure"
            : "last sync failed"}
        </p>
      </div>
      <Link to="/connectors" className="text-xs font-medium text-amber-800 hover:text-amber-900 shrink-0 underline">
        Fix now →
      </Link>
    </div>
  );
}

function InFlightPanel({ crs }: { crs: ChangeRequestSummary[] }) {
  if (crs.length === 0) return null;
  return (
    <div className="bg-white rounded-lg border border-slate-200 mb-6">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-2">
        <Play className="w-4 h-4 text-blue-500" />
        <h2 className="text-sm font-semibold text-slate-900">In-Flight Changes</h2>
        <span className="text-xs text-slate-400">({crs.length})</span>
      </div>
      <div className="divide-y divide-slate-50">
        {crs.map((cr) => (
          <Link
            key={cr.id}
            to={`/change-requests/${cr.id}`}
            className="flex items-center gap-4 px-5 py-3 hover:bg-slate-50 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-slate-900 truncate">{cr.title}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                {cr.change_type.replace(/_/g, " ")} · {cr.requester.name}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <StatusBadge status={cr.status} size="sm" />
              {(cr as any).rollback_available ? (
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
                  <CheckCircle2 className="w-3 h-3" />
                  rollback available
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-xs text-slate-400 bg-slate-50 px-2 py-0.5 rounded-full border border-slate-200">
                  no rollback
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function ExposureSummary({
  assets,
}: {
  assets: { id: string; criticality: string; environment: string }[];
}) {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const a of assets) {
    if (a.criticality in counts) counts[a.criticality as keyof typeof counts]++;
  }
  const tiers: { key: keyof typeof counts; label: string; color: string; bg: string }[] = [
    { key: "critical", label: "Critical", color: "text-red-700", bg: "bg-red-50 border-red-200" },
    { key: "high", label: "High", color: "text-orange-700", bg: "bg-orange-50 border-orange-200" },
    { key: "medium", label: "Medium", color: "text-amber-700", bg: "bg-amber-50 border-amber-200" },
    { key: "low", label: "Low", color: "text-slate-600", bg: "bg-slate-50 border-slate-200" },
  ];
  return (
    <div className="bg-white rounded-lg border border-slate-200 mb-6">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">Exposure Summary</h2>
        </div>
        <Link to="/assets?preset=unmitigated_criticals" className="text-xs text-brand-600 hover:text-brand-700 font-medium">
          View critical assets →
        </Link>
      </div>
      <div className="px-5 py-4 grid grid-cols-4 gap-3">
        {tiers.map(({ key, label, color, bg }) => (
          <Link
            key={key}
            to={`/assets?search=criticality:${key}`}
            className={`flex flex-col items-center p-3 rounded-lg border ${bg} hover:opacity-80 transition-opacity`}
          >
            <span className={`text-2xl font-bold ${color}`}>{counts[key]}</span>
            <span className={`text-xs font-medium ${color} mt-0.5`}>{label}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

const CRITICALITY_ORDER: Record<string, number> = {
  critical: 0, high: 1, medium: 2, low: 3,
};

function TopRisksPanel({
  assets,
}: {
  assets: { id: string; name: string; criticality: string; environment: string; asset_type: string }[];
}) {
  const top5 = [...assets]
    .sort((a, b) => (CRITICALITY_ORDER[a.criticality] ?? 9) - (CRITICALITY_ORDER[b.criticality] ?? 9))
    .slice(0, 5)
    .filter((a) => ["critical", "high"].includes(a.criticality));

  if (top5.length === 0) return null;

  return (
    <div className="bg-white rounded-lg border border-slate-200 mb-6">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <h2 className="text-sm font-semibold text-slate-900">Top At-Risk Assets</h2>
        </div>
        <Link to="/assets?preset=unmitigated_criticals" className="text-xs text-brand-600 hover:text-brand-700 font-medium">
          View all →
        </Link>
      </div>
      <div className="divide-y divide-slate-50">
        {top5.map((asset) => (
          <Link
            key={asset.id}
            to={`/assets/${asset.id}`}
            className="flex items-center gap-3 px-5 py-3 hover:bg-slate-50 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-slate-900 truncate">{asset.name}</div>
              <div className="text-xs text-slate-400 mt-0.5">
                {asset.asset_type?.replace(/_/g, " ")} · {asset.environment}
              </div>
            </div>
            <RiskBadge level={asset.criticality as any} size="sm" />
          </Link>
        ))}
      </div>
    </div>
  );
}

function ChangeRequestRow({ cr }: { cr: ChangeRequestSummary }) {
  return (
    <Link
      to={`/change-requests/${cr.id}`}
      className="flex items-center gap-4 px-5 py-3.5 hover:bg-slate-50 transition-colors"
    >
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-slate-900 truncate">{cr.title}</div>
        <div className="text-xs text-slate-400 mt-0.5">
          {cr.change_type.replace(/_/g, " ")} · {cr.requester.name} ·{" "}
          {formatDistanceToNow(new Date(cr.created_at), { addSuffix: true })}
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <RiskBadge level={cr.risk_level} size="sm" />
        <StatusBadge status={cr.status} size="sm" />
      </div>
    </Link>
  );
}
