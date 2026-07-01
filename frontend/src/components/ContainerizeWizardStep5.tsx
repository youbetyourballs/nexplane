// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { AlertTriangle, CheckCircle, Loader2 } from "lucide-react";
import { useFireCR } from "../hooks/useFireCR";

interface ContainerizeWizardStep5Props {
  assetId: string;
  apps: Array<{ name: string; systemd_unit?: string }>;
  onComplete: () => void;
}

export function ContainerizeWizardStep5({ assetId, apps, onComplete }: ContainerizeWizardStep5Props) {
  const [status, setStatus] = useState<"idle" | "retiring" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const { fireCR } = useFireCR();

  const handleRetire = async () => {
    setStatus("retiring"); setError(null);
    for (const app of apps) {
      const unit = app.systemd_unit || `${app.name}.service`;
      try {
        await fireCR({
          title: `[Containerize] Retire legacy ${app.name}`,
          changeType: "agent_containerize_retire",
          targetAssetId: assetId,
          parameters: { systemd_unit: unit, dry_run: false },
        });
      } catch (e) {
        setError(`Failed to retire ${app.name}: ${e instanceof Error ? e.message : String(e)}`);
        setStatus("error"); return;
      }
    }
    setStatus("done"); onComplete();
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-slate-900">Step 5 — Retire Legacy Service</h3>
        <p className="text-sm text-slate-500 mt-1">Stop and disable the legacy systemd services. This action can be rolled back.</p>
      </div>

      <div className="border border-amber-200 bg-amber-50 rounded-lg p-4 space-y-2">
        <p className="text-sm font-semibold text-amber-800 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" />What will happen
        </p>
        {apps.map((app) => (
          <div key={app.name} className="text-sm text-amber-700">
            <code className="font-mono">{app.systemd_unit || `${app.name}.service`}</code> will be stopped and disabled
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      {status === "done" ? (
        <div className="text-sm text-green-600 flex items-center gap-2">
          <CheckCircle className="h-4 w-4" />Legacy services retired. Migration complete.
        </div>
      ) : (
        <button
          onClick={handleRetire}
          disabled={status === "retiring"}
          className="w-full px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {status === "retiring" ? (
            <><Loader2 className="h-4 w-4 animate-spin" />Retiring&hellip;</>
          ) : "Retire Legacy Service"}
        </button>
      )}
    </div>
  );
}
