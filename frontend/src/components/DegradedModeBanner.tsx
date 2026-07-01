// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useEffect, useState } from "react";

interface UpgradeSentinel {
  state: string;
  previous_version?: string;
  target_version?: string;
  cr_id?: string;
  snapshot_path?: string;
}

interface VersionCheckResponse {
  available: boolean;
  manifest: unknown | null;
  degraded: UpgradeSentinel | null;
}

export default function DegradedModeBanner({ isAdmin }: { isAdmin: boolean }) {
  const [degraded, setDegraded] = useState<UpgradeSentinel | null>(null);
  const [action, setAction] = useState<"idle" | "marking" | "rolling_back">("idle");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    fetch("/version/check")
      .then((r) => r.json())
      .then((data: VersionCheckResponse) => {
        if (data.degraded) setDegraded(data.degraded);
      })
      .catch(() => {});
  }, [isAdmin]);

  if (!isAdmin || !degraded) return null;

  async function handleMarkComplete() {
    if (!degraded?.cr_id) return;
    setAction("marking");
    try {
      const resp = await fetch(`/change-requests/${degraded.cr_id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "completed" }),
      });
      if (resp.ok) {
        setMessage("Upgrade marked as complete. Refresh to continue.");
        setDegraded(null);
      } else {
        setMessage("Failed to mark complete — check logs.");
      }
    } catch {
      setMessage("Network error.");
    } finally {
      setAction("idle");
    }
  }

  async function handleRollback() {
    if (!degraded?.cr_id) return;
    setAction("rolling_back");
    try {
      const resp = await fetch(`/change-requests/${degraded.cr_id}/rollback`, {
        method: "POST",
      });
      if (resp.ok) {
        setMessage("Rollback initiated — watchdog will restore previous version.");
      } else {
        setMessage("Rollback request failed — use install.sh --recover if the instance is unreachable.");
      }
    } catch {
      setMessage("Network error — use install.sh --recover.");
    } finally {
      setAction("idle");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full p-8">
        <h2 className="text-2xl font-bold text-red-600 mb-2">Upgrade Incomplete</h2>
        <p className="text-gray-700 mb-1">
          A platform upgrade is in an unresolved state:{" "}
          <code className="bg-gray-100 px-1 rounded">{degraded.state}</code>
        </p>
        {degraded.previous_version && (
          <p className="text-gray-600 text-sm mb-1">
            Previous version: <strong>{degraded.previous_version}</strong>
          </p>
        )}
        {degraded.target_version && (
          <p className="text-gray-600 text-sm mb-4">
            Target version: <strong>{degraded.target_version}</strong>
          </p>
        )}
        {message && (
          <p className="bg-yellow-50 border border-yellow-200 text-yellow-800 rounded p-3 text-sm mb-4">
            {message}
          </p>
        )}
        <div className="flex gap-3 flex-wrap">
          <button
            onClick={handleRollback}
            disabled={action !== "idle"}
            className="bg-red-600 text-white px-4 py-2 rounded font-semibold hover:bg-red-700 disabled:opacity-50"
          >
            {action === "rolling_back" ? "Rolling back…" : `Roll back to v${degraded.previous_version ?? "previous"}`}
          </button>
          <button
            onClick={handleMarkComplete}
            disabled={action !== "idle"}
            className="bg-gray-200 text-gray-800 px-4 py-2 rounded font-semibold hover:bg-gray-300 disabled:opacity-50"
          >
            {action === "marking" ? "Marking…" : "Mark as complete"}
          </button>
        </div>
        <p className="text-xs text-gray-400 mt-4">
          If this instance is unreachable, run{" "}
          <code className="bg-gray-100 px-1 rounded">install.sh --recover</code> on the host.
        </p>
      </div>
    </div>
  );
}
