import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import apiClient from "../lib/apiClient";

interface ScheduledReboot {
  id: string;
  status: string;
  metadata: {
    reboot_at: string;
    verify_services: string[];
    graceful_delay_seconds: number;
  };
  created_at: string;
}

interface AccessReviewSchedule {
  id: string;
  frequency_days: number;
  scope: string;
  reviewer_assignment_rule: string;
  last_review_created_at: string | null;
  enabled: boolean;
}

export default function ScheduledOperations() {
  const queryClient = useQueryClient();
  const [rebootForm, setRebootForm] = useState({
    assetId: "",
    rebootAt: "",
    services: "",
    delaySeconds: "60",
  });

  // Scheduled reboots
  const { data: reboots, isLoading: rebootsLoading } = useQuery<ScheduledReboot[]>({
    queryKey: ["scheduled-reboots"],
    queryFn: () =>
      apiClient
        .get<ScheduledReboot[]>("/change-requests", {
          params: { change_type: "scheduled_reboot", limit: 20 },
        })
        .then((r) => r.data),
    refetchInterval: 30_000,
  });

  // Access review schedules
  const { data: reviewSchedules, isLoading: reviewsLoading } = useQuery<AccessReviewSchedule[]>({
    queryKey: ["access-review-schedules"],
    queryFn: () =>
      apiClient.get<AccessReviewSchedule[]>("/access-review-schedules").then((r) => r.data),
  });

  // Schedule a reboot
  const scheduleReboot = useMutation({
    mutationFn: () =>
      apiClient.post("/change-requests", {
        change_type: "scheduled_reboot",
        target_asset_id: rebootForm.assetId,
        parameters: {
          target_asset_ids: [rebootForm.assetId],
          reboot_at: new Date(rebootForm.rebootAt).toISOString(),
          verify_services: rebootForm.services.split(",").map((s) => s.trim()).filter(Boolean),
          graceful_delay_seconds: parseInt(rebootForm.delaySeconds, 10) || 60,
        },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["scheduled-reboots"] }),
  });

  function statusBadge(status: string) {
    const colors: Record<string, string> = {
      completed: "bg-green-100 text-green-800",
      failed: "bg-red-100 text-red-800",
      executing: "bg-yellow-100 text-yellow-800",
      approved: "bg-blue-100 text-blue-800",
      pending: "bg-gray-100 text-gray-700",
    };
    return (
      <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[status] ?? "bg-gray-100 text-gray-700"}`}>
        {status}
      </span>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold">Scheduled Operations</h1>

      {/* Schedule a reboot */}
      <section className="border rounded-lg p-4 space-y-4">
        <h2 className="font-semibold text-lg">Schedule a Reboot</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Asset ID</label>
            <input
              className="w-full border rounded px-3 py-1.5 text-sm"
              placeholder="asset-uuid"
              value={rebootForm.assetId}
              onChange={(e) => setRebootForm((f) => ({ ...f, assetId: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Reboot At</label>
            <input
              type="datetime-local"
              className="w-full border rounded px-3 py-1.5 text-sm"
              value={rebootForm.rebootAt}
              onChange={(e) => setRebootForm((f) => ({ ...f, rebootAt: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Services to Verify (comma-separated)
            </label>
            <input
              className="w-full border rounded px-3 py-1.5 text-sm"
              placeholder="nginx, postgresql"
              value={rebootForm.services}
              onChange={(e) => setRebootForm((f) => ({ ...f, services: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Graceful Delay (seconds, min 60)
            </label>
            <input
              type="number"
              min={60}
              className="w-full border rounded px-3 py-1.5 text-sm"
              value={rebootForm.delaySeconds}
              onChange={(e) => setRebootForm((f) => ({ ...f, delaySeconds: e.target.value }))}
            />
          </div>
        </div>
        <button
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
          disabled={scheduleReboot.isPending || !rebootForm.assetId || !rebootForm.rebootAt}
          onClick={() => scheduleReboot.mutate()}
        >
          {scheduleReboot.isPending ? "Scheduling…" : "Schedule Reboot"}
        </button>
        {scheduleReboot.isSuccess && (
          <p className="text-green-700 text-sm">Reboot scheduled — awaiting approval.</p>
        )}
      </section>

      {/* Scheduled reboots list */}
      <section className="border rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-lg">Scheduled Reboots</h2>
        {rebootsLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b text-gray-500 text-xs uppercase">
                  <th className="pb-2 pr-4">Reboot At</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Services</th>
                  <th className="pb-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {(reboots ?? []).map((r) => (
                  <tr key={r.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-4 text-xs font-mono">
                      {r.metadata?.reboot_at
                        ? new Date(r.metadata.reboot_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-2 pr-4">{statusBadge(r.status)}</td>
                    <td className="py-2 pr-4 text-xs text-gray-600">
                      {(r.metadata?.verify_services ?? []).join(", ") || "—"}
                    </td>
                    <td className="py-2 text-xs text-gray-500">
                      {new Date(r.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {(reboots ?? []).length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-4 text-center text-gray-400 text-sm">
                      No scheduled reboots.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Access review schedules */}
      <section className="border rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-lg">Access Review Schedules</h2>
        {reviewsLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b text-gray-500 text-xs uppercase">
                  <th className="pb-2 pr-4">Scope</th>
                  <th className="pb-2 pr-4">Frequency</th>
                  <th className="pb-2 pr-4">Reviewer Rule</th>
                  <th className="pb-2 pr-4">Last Review</th>
                  <th className="pb-2">Enabled</th>
                </tr>
              </thead>
              <tbody>
                {(reviewSchedules ?? []).map((s) => (
                  <tr key={s.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-4 text-xs">{s.scope}</td>
                    <td className="py-2 pr-4 text-xs">Every {s.frequency_days} days</td>
                    <td className="py-2 pr-4 text-xs">{s.reviewer_assignment_rule}</td>
                    <td className="py-2 pr-4 text-xs text-gray-500">
                      {s.last_review_created_at
                        ? new Date(s.last_review_created_at).toLocaleDateString()
                        : "Never"}
                    </td>
                    <td className="py-2 text-xs">
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          s.enabled
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-500"
                        }`}
                      >
                        {s.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </td>
                  </tr>
                ))}
                {(reviewSchedules ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-gray-400 text-sm">
                      No access review schedules configured.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Compliance scan schedule info */}
      <section className="border rounded-lg p-4 space-y-2">
        <h2 className="font-semibold text-lg">Compliance Scan Schedule</h2>
        <p className="text-sm text-gray-600">
          CIS compliance audits run automatically every Sunday at 01:00 UTC across all managed Linux
          assets. Results are stored in asset metadata and alerts are raised for score regressions
          of 10% or greater.
        </p>
        <p className="text-sm text-gray-500">
          To trigger an immediate audit, create a change request with type{" "}
          <code className="font-mono text-xs bg-gray-100 px-1 rounded">audit_cis_compliance</code>.
        </p>
      </section>
    </div>
  );
}
