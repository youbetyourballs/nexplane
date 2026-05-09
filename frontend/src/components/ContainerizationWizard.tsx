import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, CheckCircle, Loader2 } from "lucide-react";
import { apiClient } from "../api/client";
import { ContainerizeWizardStep2 } from "./ContainerizeWizardStep2";
import { ContainerizeWizardStep3 } from "./ContainerizeWizardStep3";
import { ContainerizeWizardStep4 } from "./ContainerizeWizardStep4";
import { ContainerizeWizardStep5 } from "./ContainerizeWizardStep5";

interface App {
  name: string;
  binary: string;
  listening_ports: { port: number; protocol: string }[];
  data_directories: string[];
  env_vars: string[];
  containerization_status: string;
  image_digest?: string;
  systemd_unit?: string;
}

interface ContainerizationWizardProps {
  assetId: string;
  onClose?: () => void;
}

const STEP_LABELS = [
  "Discover",
  "Build & Push",
  "Deploy",
  "Smoke Test",
  "Retire",
];

export function ContainerizationWizard({ assetId, onClose }: ContainerizationWizardProps) {
  const [currentStep, setCurrentStep] = useState(1);
  const [selectedApps, setSelectedApps] = useState<App[]>([]);
  const [buildResults, setBuildResults] = useState<Record<string, { imageDigest: string; manifests: Record<string, string>; dockerfile?: string }>>({});
  const [deployResults, setDeployResults] = useState<Record<string, { workloadAssetId: string }>>({});

  // Step 1: discover apps on the asset
  const { data: discoveredApps, isLoading: discovering, error: discoverError } = useQuery<App[]>({
    queryKey: ["containerize-discover", assetId],
    queryFn: async () => {
      const res = await apiClient.get(`/assets/${assetId}/containerize/discover`);
      return res.data ?? [];
    },
    enabled: currentStep === 1,
  });

  const toggleApp = (app: App) => {
    setSelectedApps((prev) =>
      prev.some((a) => a.name === app.name)
        ? prev.filter((a) => a.name !== app.name)
        : [...prev, app]
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-900">Containerization Wizard</h2>
          {onClose && (
            <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
              <X className="h-5 w-5" />
            </button>
          )}
        </div>

        {/* Step indicator */}
        <div className="px-6 py-3 border-b border-slate-100">
          <div className="flex items-center gap-1">
            {STEP_LABELS.map((label, idx) => {
              const step = idx + 1;
              const isActive = currentStep === step;
              const isDone = currentStep > step;
              return (
                <div key={step} className="flex items-center">
                  <div className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium ${
                    isActive ? "bg-brand-50 text-brand-700" :
                    isDone ? "text-green-600" : "text-slate-400"
                  }`}>
                    {isDone ? <CheckCircle className="h-3.5 w-3.5" /> : (
                      <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                        isActive ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-500"
                      }`}>{step}</span>
                    )}
                    {label}
                  </div>
                  {idx < STEP_LABELS.length - 1 && (
                    <div className="w-4 h-px bg-slate-200 mx-1" />
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* Step 1 — Discover */}
          {currentStep === 1 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Step 1 — Discover Apps</h3>
                <p className="text-sm text-slate-500 mt-1">
                  Select the applications found on this host to containerize.
                </p>
              </div>
              {discovering && (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />Scanning host for running services&hellip;
                </div>
              )}
              {discoverError && (
                <p className="text-sm text-red-600">Failed to discover apps. Check the host connection.</p>
              )}
              {!discovering && !discoverError && discoveredApps && (
                <>
                  {discoveredApps.length === 0 ? (
                    <p className="text-sm text-slate-500">No containerizable applications found on this host.</p>
                  ) : (
                    <div className="space-y-2">
                      {discoveredApps.map((app) => {
                        const selected = selectedApps.some((a) => a.name === app.name);
                        return (
                          <label
                            key={app.name}
                            className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                              selected ? "border-brand-500 bg-brand-50" : "border-slate-200 hover:border-slate-300"
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={() => toggleApp(app)}
                              className="mt-0.5"
                            />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-sm font-medium text-slate-800">{app.name}</span>
                                {app.data_directories.length > 0 && (
                                  <span className="text-xs border border-slate-300 rounded px-1.5 py-0.5 text-slate-500">stateful</span>
                                )}
                                <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                                  app.containerization_status === "not_containerized"
                                    ? "bg-amber-100 text-amber-700"
                                    : "bg-green-100 text-green-700"
                                }`}>{app.containerization_status.replace(/_/g, " ")}</span>
                              </div>
                              <div className="text-xs text-slate-400 mt-0.5">
                                {app.binary && <span>{app.binary} · </span>}
                                Ports: {app.listening_ports.map((p) => `${p.port}/${p.protocol}`).join(", ") || "none"}
                                {app.env_vars.length > 0 && ` · ${app.env_vars.length} env vars`}
                              </div>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  )}
                  <button
                    onClick={() => setCurrentStep(2)}
                    disabled={selectedApps.length === 0}
                    className="w-full px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Continue with {selectedApps.length} app{selectedApps.length !== 1 ? "s" : ""}
                  </button>
                </>
              )}
            </div>
          )}

          {/* Step 2 — Build & Push */}
          {currentStep === 2 && (
            <ContainerizeWizardStep2
              assetId={assetId}
              apps={selectedApps}
              onComplete={(r) => { setBuildResults(r); setCurrentStep(3); }}
            />
          )}

          {/* Step 3 — Deploy */}
          {currentStep === 3 && (
            <ContainerizeWizardStep3
              assetId={assetId}
              apps={selectedApps}
              buildResults={buildResults}
              onComplete={(r) => { setDeployResults(r); setCurrentStep(4); }}
            />
          )}

          {/* Step 4 — Smoke Test */}
          {currentStep === 4 && (
            <ContainerizeWizardStep4
              assetId={assetId}
              onApprove={() => setCurrentStep(5)}
            />
          )}

          {/* Step 5 — Retire */}
          {currentStep === 5 && (
            <ContainerizeWizardStep5
              assetId={assetId}
              apps={selectedApps}
              onComplete={() => { onClose?.(); }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
