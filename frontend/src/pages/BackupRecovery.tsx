// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { formatDistanceToNow, parseISO } from "date-fns";
import {
  CheckCircle2, AlertTriangle, XCircle, RotateCcw, Clock, Database, Edit2, Plus,
} from "lucide-react";
import { backupApi, BackupTarget, BackupHistoryItem } from "../api/endpoints";
import { BackupTargetForm } from "../components/BackupTargetForm";

// ─── Coverage Cards ───────────────────────────────────────────────────────────

function statusConfig(status: BackupTarget["status"]) {
  return {
    healthy: { icon: CheckCircle2, label: "Healthy", cls: "text-emerald-600", border: "border-emerald-200 bg-emerald-50" },
    overdue: { icon: AlertTriangle, label: "Overdue", cls: "text-amber-600", border: "border-amber-200 bg-amber-50" },
    unprotected: { icon: XCircle, label: "Unprotected", cls: "text-red-500", border: "border-red-200 bg-red-50" },
  }[status];
}

function CoverageCard({
  target,
  onRestore,
  onEdit,
}: {
  target: BackupTarget;
  onRestore: (t: BackupTarget) => void;
  onEdit: (t: BackupTarget) => void;
}) {
  const cfg = statusConfig(target.status);
  const Icon = cfg.icon;
  return (
    <div className={`rounded-lg border p-4 ${cfg.border}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-slate-500 flex-shrink-0 mt-0.5" />
          <span className="text-sm font-medium text-slate-900">{target.target_description}</span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={() => onEdit(target)}
            className="text-slate-400 hover:text-slate-600"
            title="Edit backup target"
          >
            <Edit2 className="w-3.5 h-3.5" />
          </button>
          <span className={`flex items-center gap-1 text-xs font-medium ${cfg.cls}`}>
            <Icon className="w-3.5 h-3.5" />
            {cfg.label}
          </span>
        </div>
      </div>
      {target.last_successful_at && (
        <p className="text-xs text-slate-500 mb-1">
          Last backup: {formatDistanceToNow(parseISO(target.last_successful_at), { addSuffix: true })}
        </p>
      )}
      {!target.last_successful_at && target.status !== "unprotected" && (
        <p className="text-xs text-slate-500 mb-1">No successful backup yet</p>
      )}
      <p className="text-xs text-slate-400 mb-3">
        Every{" "}
        {target.expected_cadence_hours === 24
          ? "day"
          : target.expected_cadence_hours === 168
          ? "week"
          : `${target.expected_cadence_hours}h`}
      </p>
      <div className="flex gap-2">
        {target.last_successful_backup_cr_id && (
          <button
            onClick={() => onRestore(target)}
            className="flex items-center gap-1 px-2 py-1 bg-white border border-slate-300 text-slate-600 text-xs rounded hover:bg-slate-50"
          >
            <RotateCcw className="w-3 h-3" />
            Restore
          </button>
        )}
        {target.last_successful_backup_cr_id && (
          <Link
            to={`/change-requests/${target.last_successful_backup_cr_id}`}
            className="px-2 py-1 text-xs text-brand-600 hover:underline"
          >
            View backup CR
          </Link>
        )}
      </div>
    </div>
  );
}

// ─── Restore Drawer ───────────────────────────────────────────────────────────

function RestoreDrawer({
  target,
  history,
  onClose,
}: {
  target: BackupTarget;
  history: BackupHistoryItem[];
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const relevantHistory = history.filter((h) => h.artifact_refs !== null);
  const [selectedCrId, setSelectedCrId] = useState(
    target.last_successful_backup_cr_id ?? relevantHistory[0]?.id ?? ""
  );
  const [notes, setNotes] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      backupApi.createRestoreCr({
        source_cr_id: selectedCrId,
        target_description: target.target_description,
        restore_type: "full",
        notes,
      }),
    onSuccess: (data) => {
      onClose();
      navigate(`/change-requests/${data.id}`);
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[480px] bg-white shadow-xl flex flex-col h-full overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">
            Restore: {target.target_description}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">
            ✕
          </button>
        </div>
        <div className="flex-1 px-6 py-5 space-y-5">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Restore point</label>
            <select
              value={selectedCrId}
              onChange={(e) => setSelectedCrId(e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              {relevantHistory.map((h) => (
                <option key={h.id} value={h.id}>
                  {formatDistanceToNow(parseISO(h.created_at), { addSuffix: true })} — {h.title}
                </option>
              ))}
              {relevantHistory.length === 0 && (
                <option value="">No backup artifacts available</option>
              )}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="Reason for restore..."
            />
          </div>
          <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs text-amber-800">
            The restore CR requires human approval before execution.
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => mutation.mutate()}
              disabled={!selectedCrId || mutation.isPending}
              className="flex-1 bg-brand-600 text-white py-2 rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
            >
              {mutation.isPending ? "Creating..." : "Create Restore CR"}
            </button>
            <button
              onClick={onClose}
              className="px-4 py-2 border border-slate-300 text-slate-600 text-sm rounded hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
          {mutation.isError && (
            <p className="text-xs text-red-600">Failed to create restore CR.</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Backup History Table ─────────────────────────────────────────────────────

function BackupHistoryTable({
  history,
  onRestore,
}: {
  history: BackupHistoryItem[];
  onRestore: (item: BackupHistoryItem) => void;
}) {
  if (history.length === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
        <Clock className="w-8 h-8 text-slate-300 mx-auto mb-2" />
        <p className="text-sm text-slate-500">No completed backups with artifact references yet.</p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <table className="w-full">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Target</th>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Artifact</th>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Completed</th>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Actions</th>
          </tr>
        </thead>
        <tbody>
          {history.map((item) => (
            <tr key={item.id} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="px-4 py-3 text-sm text-slate-900">{item.title}</td>
              <td className="px-4 py-3 text-xs font-mono text-slate-500">
                {(item.artifact_refs?.key as string) ??
                  (item.artifact_refs?.snapshot_id as string) ??
                  "—"}
              </td>
              <td className="px-4 py-3 text-xs text-slate-500">
                {formatDistanceToNow(parseISO(item.created_at), { addSuffix: true })}
              </td>
              <td className="px-4 py-3">
                <div className="flex gap-2">
                  <button
                    onClick={() => onRestore(item)}
                    className="flex items-center gap-1 px-2 py-1 text-xs text-brand-600 border border-brand-200 rounded hover:bg-brand-50"
                  >
                    <RotateCcw className="w-3 h-3" />
                    Restore
                  </button>
                  <Link
                    to={`/change-requests/${item.id}`}
                    className="px-2 py-1 text-xs text-slate-500 hover:text-slate-700"
                  >
                    View CR →
                  </Link>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export function BackupRecovery() {
  const [restoreTarget, setRestoreTarget] = useState<BackupTarget | null>(null);
  const [formTarget, setFormTarget] = useState<BackupTarget | null | undefined>(undefined);
  // undefined = closed, null = create mode, BackupTarget = edit mode

  const { data: targets = [], isLoading: targetsLoading, refetch: refetchTargets } = useQuery({
    queryKey: ["backup-targets"],
    queryFn: () => backupApi.listTargets(),
    refetchInterval: 60_000,
  });

  const { data: history = [], isLoading: historyLoading } = useQuery({
    queryKey: ["backup-history"],
    queryFn: () => backupApi.listHistory(),
    refetchInterval: 60_000,
  });

  const healthy = targets.filter((t) => t.status === "healthy").length;
  const overdue = targets.filter((t) => t.status === "overdue").length;
  const unprotected = targets.filter((t) => t.status === "unprotected").length;

  function handleHistoryRestore(item: BackupHistoryItem) {
    const target = targets.find((t) => t.last_successful_backup_cr_id === item.id);
    if (target) {
      setRestoreTarget(target);
    } else {
      setRestoreTarget({
        id: "",
        organization_id: "",
        recurring_job_id: null,
        asset_id: null,
        target_description: item.title,
        expected_cadence_hours: 24,
        last_successful_backup_cr_id: item.id,
        last_successful_at: item.created_at,
        status: "healthy",
        created_at: item.created_at,
        backup_tier: "machine",
        capture_strategy: "ebs_snapshot",
        storage_id: null,
      });
    }
  }

  if (targetsLoading || historyLoading) {
    return <div className="p-8 text-sm text-slate-500">Loading...</div>;
  }

  return (
    <div className="p-8 max-w-6xl space-y-8">
      {restoreTarget && (
        <RestoreDrawer
          target={restoreTarget}
          history={history}
          onClose={() => setRestoreTarget(null)}
        />
      )}
      <div className="flex items-center justify-between mb-6">
        <div>
        <h1 className="text-xl font-semibold text-slate-900">Backup &amp; Recovery</h1>
        <p className="text-sm text-slate-500 mt-1">
          {targets.length} targets &middot; {healthy} healthy &middot; {overdue} overdue &middot;{" "}
          {unprotected} unprotected
        </p>
        </div>
        <button
          onClick={() => setFormTarget(null)}
          className="flex items-center gap-2 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
        >
          <Plus className="w-4 h-4" />
          Add backup target
        </button>
      </div>
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Coverage</h2>
        {targets.length === 0 ? (
          <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
            <Database className="w-8 h-8 text-slate-300 mx-auto mb-2" />
            <p className="text-sm text-slate-500">
              No backup targets yet. Create a backup recurring job to start tracking coverage.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {targets.map((target) => (
              <CoverageCard key={target.id} target={target} onRestore={setRestoreTarget} onEdit={(t) => setFormTarget(t)} />
            ))}
          </div>
        )}
      </section>
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Backup History</h2>
        <BackupHistoryTable history={history} onRestore={handleHistoryRestore} />
      </section>
      {formTarget !== undefined && (
        <BackupTargetForm
          target={formTarget}
          onClose={() => setFormTarget(undefined)}
          onSaved={() => refetchTargets()}
        />
      )}
    </div>
  );
}

export default BackupRecovery;
