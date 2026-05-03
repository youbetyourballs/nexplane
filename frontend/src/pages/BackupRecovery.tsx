import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import apiClient from "../lib/apiClient";

interface BackupRecord {
  id: string;
  change_type: string;
  status: string;
  result?: {
    snapshot_id?: string;
    state?: string;
    restore_time?: string;
    health_detail?: string;
  };
  created_at: string;
}

interface RestoreFormState {
  snapshotId: string;
  restorePaths: string;
  destinationPath: string;
  assetId: string;
}

export default function BackupRecovery() {
  const queryClient = useQueryClient();
  const [restoreForm, setRestoreForm] = useState<RestoreFormState>({
    snapshotId: "",
    restorePaths: "",
    destinationPath: "/tmp/restore",
    assetId: "",
  });

  // Fetch recent backup change requests
  const { data: backups, isLoading } = useQuery<BackupRecord[]>({
    queryKey: ["backup-change-requests"],
    queryFn: () =>
      apiClient
        .get<BackupRecord[]>("/change-requests", {
          params: { change_type: "create_backup,verify_backup", limit: 20 },
        })
        .then((r) => r.data),
    refetchInterval: 15_000,
  });

  // On-demand backup mutation
  const createBackup = useMutation({
    mutationFn: (assetId: string) =>
      apiClient.post("/change-requests", {
        change_type: "create_backup",
        target_asset_id: assetId,
        parameters: {
          backup_type: "agent_backup",
          backup_name: `manual-${new Date().toISOString().slice(0, 10)}`,
          retention_days: 30,
          paths: ["/etc", "/var/www"],
        },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backup-change-requests"] }),
  });

  // Restore request mutation
  const requestRestore = useMutation({
    mutationFn: (form: RestoreFormState) =>
      apiClient.post("/change-requests", {
        change_type: "restore_files",
        target_asset_id: form.assetId,
        parameters: {
          backup_snapshot_id: form.snapshotId,
          restore_paths: form.restorePaths.split("\n").map((p) => p.trim()).filter(Boolean),
          destination_path: form.destinationPath,
        },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backup-change-requests"] }),
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
      <h1 className="text-2xl font-bold">Backup & Recovery</h1>

      {/* On-demand backup */}
      <section className="border rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-lg">On-Demand Backup</h2>
        <p className="text-sm text-gray-600">
          Trigger an immediate agent backup using restic. The backup change request will go through
          the standard approval workflow before execution.
        </p>
        <button
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
          disabled={createBackup.isPending}
          onClick={() => createBackup.mutate("placeholder-asset-id")}
        >
          {createBackup.isPending ? "Requesting…" : "Request Backup"}
        </button>
        {createBackup.isError && (
          <p className="text-red-600 text-sm">Failed to create backup request.</p>
        )}
      </section>

      {/* Backup verification status */}
      <section className="border rounded-lg p-4 space-y-3">
        <h2 className="font-semibold text-lg">Recent Backups</h2>
        {isLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b text-gray-500 text-xs uppercase">
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Snapshot</th>
                  <th className="pb-2 pr-4">Restore Time</th>
                  <th className="pb-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {(backups ?? []).map((b) => (
                  <tr key={b.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-4 font-mono text-xs">{b.change_type}</td>
                    <td className="py-2 pr-4">{statusBadge(b.status)}</td>
                    <td className="py-2 pr-4 font-mono text-xs text-gray-600">
                      {b.result?.snapshot_id ?? "—"}
                    </td>
                    <td className="py-2 pr-4 text-xs text-gray-600">
                      {b.result?.restore_time ?? "—"}
                    </td>
                    <td className="py-2 text-xs text-gray-500">
                      {new Date(b.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {(backups ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-gray-400 text-sm">
                      No backups yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Restore request form */}
      <section className="border rounded-lg p-4 space-y-4">
        <h2 className="font-semibold text-lg">Request File Restore</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Asset ID</label>
            <input
              className="w-full border rounded px-3 py-1.5 text-sm"
              placeholder="asset-uuid"
              value={restoreForm.assetId}
              onChange={(e) => setRestoreForm((f) => ({ ...f, assetId: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Snapshot ID</label>
            <input
              className="w-full border rounded px-3 py-1.5 text-sm font-mono"
              placeholder="abc123def456"
              value={restoreForm.snapshotId}
              onChange={(e) => setRestoreForm((f) => ({ ...f, snapshotId: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Paths to Restore (one per line)
            </label>
            <textarea
              className="w-full border rounded px-3 py-1.5 text-sm font-mono"
              rows={3}
              placeholder="/etc/nginx/nginx.conf"
              value={restoreForm.restorePaths}
              onChange={(e) => setRestoreForm((f) => ({ ...f, restorePaths: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Destination Path</label>
            <input
              className="w-full border rounded px-3 py-1.5 text-sm font-mono"
              value={restoreForm.destinationPath}
              onChange={(e) => setRestoreForm((f) => ({ ...f, destinationPath: e.target.value }))}
            />
          </div>
        </div>
        <button
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
          disabled={requestRestore.isPending || !restoreForm.snapshotId || !restoreForm.assetId}
          onClick={() => requestRestore.mutate(restoreForm)}
        >
          {requestRestore.isPending ? "Submitting…" : "Submit Restore Request"}
        </button>
        {requestRestore.isSuccess && (
          <p className="text-green-700 text-sm">Restore request submitted — awaiting approval.</p>
        )}
        {requestRestore.isError && (
          <p className="text-red-600 text-sm">Failed to submit restore request.</p>
        )}
      </section>
    </div>
  );
}
