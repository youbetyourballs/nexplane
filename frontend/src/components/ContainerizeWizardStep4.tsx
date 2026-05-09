import { useState, useEffect } from "react";
import { CheckCircle, XCircle, Loader2, Clock } from "lucide-react";
import { apiClient } from "../api/client";

interface HealthCheck { name: string; status: "pass" | "fail" | "pending"; detail?: string; }

interface ContainerizeWizardStep4Props {
  assetId: string;
  onApprove: () => void;
}

export function ContainerizeWizardStep4({ assetId, onApprove }: ContainerizeWizardStep4Props) {
  const [checks, setChecks] = useState<HealthCheck[]>([
    { name: "Pod status: Running", status: "pending" },
    { name: "Service reachable", status: "pending" },
    { name: "HTTP probe: 200 OK", status: "pending" },
  ]);
  const [allPassed, setAllPassed] = useState(false);
  const [polling, setPolling] = useState(true);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await apiClient.get(`/assets/${assetId}`);
        const asset = res.data;
        const healthChecks = (asset?.asset_metadata?.health_checks as Record<string, unknown>) ?? {};
        const updated: HealthCheck[] = [
          {
            name: "Pod status: Running",
            status: healthChecks.pod_running === true ? "pass" : healthChecks.pod_running === false ? "fail" : "pending",
            detail: healthChecks.pod_detail as string | undefined,
          },
          {
            name: "Service reachable",
            status: healthChecks.service_reachable === true ? "pass" : healthChecks.service_reachable === false ? "fail" : "pending",
          },
          {
            name: "HTTP probe: 200 OK",
            status: healthChecks.http_probe_ok === true ? "pass" : healthChecks.http_probe_ok === false ? "fail" : "pending",
            detail: healthChecks.http_detail as string | undefined,
          },
        ];
        setChecks(updated);
        const passed = updated.every((c) => c.status === "pass");
        setAllPassed(passed);
        if (passed) setPolling(false);
      } catch { /* ignore */ }
    }, 10_000);
    return () => clearInterval(interval);
  }, [assetId]);

  const statusIcon = (status: HealthCheck["status"]) => {
    if (status === "pass") return <CheckCircle className="h-4 w-4 text-green-500" />;
    if (status === "fail") return <XCircle className="h-4 w-4 text-red-500" />;
    return <Loader2 className="h-4 w-4 animate-spin text-slate-400" />;
  };

  const badgeClass = (status: HealthCheck["status"]) => {
    if (status === "pass") return "bg-green-100 text-green-700";
    if (status === "fail") return "bg-red-100 text-red-700";
    return "bg-slate-100 text-slate-500";
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-slate-900">Step 4 — Smoke Test</h3>
        <p className="text-sm text-slate-500 mt-1">Verify the containerized application is healthy before retiring the legacy service.</p>
      </div>

      <div className="space-y-2">
        {checks.map((check) => (
          <div key={check.name} className="flex items-center justify-between p-3 border border-slate-200 rounded">
            <div className="flex items-center gap-3">
              {statusIcon(check.status)}
              <span className="text-sm text-slate-800">{check.name}</span>
            </div>
            <div className="flex items-center gap-2">
              {check.detail && <span className="text-xs text-slate-400">{check.detail}</span>}
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${badgeClass(check.status)}`}>
                {check.status}
              </span>
            </div>
          </div>
        ))}
      </div>

      {polling && !allPassed && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Clock className="h-4 w-4" />Polling health checks every 10 seconds&hellip;
        </div>
      )}

      <button
        onClick={onApprove}
        disabled={!allPassed}
        className={`w-full px-4 py-2 text-sm font-medium rounded-md flex items-center justify-center gap-2 ${
          allPassed
            ? "bg-brand-600 text-white hover:bg-brand-700"
            : "border border-slate-200 text-slate-400 cursor-not-allowed"
        }`}
      >
        {allPassed ? "Approve & Proceed to Retire" : "Waiting for all checks to pass…"}
      </button>
    </div>
  );
}
