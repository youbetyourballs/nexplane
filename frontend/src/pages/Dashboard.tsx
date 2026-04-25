import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, Play, XCircle, Activity } from "lucide-react";
import { changeRequestsApi } from "../api/endpoints";
import { StatusBadge } from "../components/StatusBadge";
import { RiskBadge } from "../components/RiskBadge";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";
import type { ChangeRequestSummary } from "../types/api";

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

export function Dashboard() {
  const { data: all, isLoading } = useQuery({
    queryKey: ["change-requests"],
    queryFn: () => changeRequestsApi.list(),
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

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-xl font-semibold text-slate-900">Operations Dashboard</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Governed infrastructure change — real-time status
        </p>
      </div>

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
