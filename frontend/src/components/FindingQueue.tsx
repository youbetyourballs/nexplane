import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Finding {
  id: string;
  scanner: string;
  finding_type: string;
  severity: string;
  cve_id: string | null;
  title: string;
  asset_id: string | null;
  asset_name: string | null;
  status: string;
  change_request_id: string | null;
  ingested_at: string;
  sla_due_at: string | null;
  sla_breached: boolean | null;
  sla_escalated?: boolean | null;
}

interface FindingListResponse {
  total: number;
  page: number;
  page_size: number;
  findings: Finding[];
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-100 text-red-800 border-red-200",
  high: "bg-orange-100 text-orange-800 border-orange-200",
  medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
  low: "bg-green-100 text-green-800 border-green-200",
  informational: "bg-gray-100 text-gray-800 border-gray-200",
};

function SLABadge({
  dueAt,
  breached,
  escalated,
}: {
  dueAt: string | null;
  breached: boolean | null;
  escalated?: boolean | null;
}) {
  if (escalated) {
    return (
      <span className="inline-flex items-center gap-1 text-red-700 font-semibold text-xs bg-red-50 px-1.5 py-0.5 rounded">
        🔴 ESCALATED
      </span>
    );
  }
  if (breached) {
    return (
      <span className="inline-flex items-center gap-1 text-orange-600 font-semibold text-xs bg-orange-50 px-1.5 py-0.5 rounded">
        🟠 OVERDUE
      </span>
    );
  }
  if (!dueAt) return null;
  const due = new Date(dueAt);
  const now = new Date();
  const hoursLeft = (due.getTime() - now.getTime()) / 3600000;
  if (hoursLeft < 0) {
    return (
      <span className="text-orange-600 font-semibold text-xs">OVERDUE</span>
    );
  }
  if (hoursLeft < 24) {
    return (
      <span className="text-yellow-600 text-xs font-medium">
        {Math.round(hoursLeft)}h left
      </span>
    );
  }
  return (
    <span className="text-slate-400 text-xs">
      {due.toLocaleDateString()}
    </span>
  );
}

export default function FindingQueue() {
  const [severityFilter, setSeverityFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("open");
  const [page, setPage] = useState(1);
  const queryClient = useQueryClient();

  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (severityFilter) params.set("severity", severityFilter);
  if (statusFilter) params.set("status", statusFilter);

  const { data, isLoading } = useQuery<FindingListResponse>({
    queryKey: ["findings", severityFilter, statusFilter, page],
    queryFn: () =>
      apiClient.get<FindingListResponse>(`/api/v1/vulnerability/findings?${params}`).then((r) => r.data),
    refetchInterval: 60_000,
  });

  const suppressMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.patch(`/api/v1/vulnerability/findings/${id}/status`, {
        status: "accepted_risk",
        reason: "Suppressed from queue",
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["findings"] }),
  });

  const generateCRMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.post(`/api/v1/vulnerability/findings/${id}/generate-change-request`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["findings"] }),
  });

  return (
    <section className="bg-white rounded-lg border">
      <div className="p-4 border-b flex items-center justify-between">
        <h2 className="text-lg font-semibold">Pending Remediation</h2>
        <div className="flex gap-2">
          <select
            className="border rounded px-2 py-1 text-sm"
            value={severityFilter}
            onChange={(e) => { setSeverityFilter(e.target.value); setPage(1); }}
          >
            <option value="">All severities</option>
            {["critical", "high", "medium", "low"].map((s) => (
              <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            ))}
          </select>
          <select
            className="border rounded px-2 py-1 text-sm"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          >
            <option value="open">Open</option>
            <option value="change_request_generated">CR Generated</option>
            <option value="accepted_risk">Accepted Risk</option>
            <option value="">All statuses</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="p-8 text-center text-gray-500">Loading findings...</div>
      ) : !data?.findings.length ? (
        <div className="p-8 text-center text-gray-500">No findings match the current filters.</div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left p-3 font-medium">Severity</th>
                <th className="text-left p-3 font-medium">Finding</th>
                <th className="text-left p-3 font-medium">Asset</th>
                <th className="text-left p-3 font-medium">SLA</th>
                <th className="text-left p-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.findings.map((f) => (
                <tr key={f.id} className="border-t hover:bg-gray-50">
                  <td className="p-3">
                    <span className={`px-2 py-0.5 rounded border text-xs font-semibold uppercase ${SEVERITY_COLORS[f.severity] ?? ""}`}>
                      {f.severity}
                    </span>
                  </td>
                  <td className="p-3">
                    <p className="font-medium text-sm">{f.cve_id ?? f.finding_type}</p>
                    <p className="text-xs text-gray-500 truncate max-w-xs">{f.title}</p>
                  </td>
                  <td className="p-3 text-xs text-gray-700">{f.asset_name ?? "Unmatched"}</td>
                  <td className="p-3">
                    <SLABadge
                      dueAt={f.sla_due_at}
                      breached={f.sla_breached}
                      escalated={f.sla_escalated}
                    />
                  </td>
                  <td className="p-3 space-x-2">
                    {f.change_request_id ? (
                      <a
                        href={`/change-requests/${f.change_request_id}`}
                        className="text-blue-600 hover:underline text-xs"
                      >
                        Review CR
                      </a>
                    ) : (
                      <button
                        className="text-blue-600 hover:underline text-xs"
                        onClick={() => generateCRMutation.mutate(f.id)}
                        disabled={generateCRMutation.isPending}
                      >
                        Generate CR
                      </button>
                    )}
                    <button
                      className="text-gray-500 hover:text-red-600 text-xs"
                      onClick={() => suppressMutation.mutate(f.id)}
                      disabled={suppressMutation.isPending}
                    >
                      Suppress
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="p-3 border-t flex items-center justify-between text-sm text-gray-600">
            <span>Total: {data.total}</span>
            <div className="flex gap-2">
              <button
                className="px-2 py-1 border rounded disabled:opacity-50"
                onClick={() => setPage((p) => p - 1)}
                disabled={page === 1}
              >
                Prev
              </button>
              <span>Page {page}</span>
              <button
                className="px-2 py-1 border rounded disabled:opacity-50"
                onClick={() => setPage((p) => p + 1)}
                disabled={data.findings.length < 25}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
