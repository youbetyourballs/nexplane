import { useState } from "react";

interface StepResult {
  status?: string;
  [key: string]: unknown;
}

interface Props {
  stepResults: Record<string, StepResult> | undefined;
  crStatus: string;
}

const STAGES = [
  { key: "preflight_discovery",   label: "Preflight Discovery",   desc: "Deep host enrichment — connections, env vars, open files, runtime deps" },
  { key: "fleet_cross_reference", label: "Fleet Cross-Reference",  desc: "Maps outbound IPs to Nexplane asset inventory" },
  { key: "ai_analysis",           label: "AI Analysis",           desc: "Classifies workloads as stateless/stateful, monolith/modular" },
  { key: "stateful_gate",         label: "Stateful Gate",         desc: "Human review checkpoint for stateful workloads" },
  { key: "build",                 label: "Build & Push",          desc: "Generates Dockerfile, builds image, pushes to registry" },
  { key: "deploy",                label: "Deploy to Kubernetes",  desc: "Applies manifests: ConfigMap → PVC → Service → Deployment" },
  { key: "soak_verify",           label: "Soak Verification",     desc: "HTTP health probes for configured soak window" },
] as const;

function stageStatus(key: string, stepResults: Record<string, StepResult> | undefined): string | undefined {
  return stepResults?.[key]?.status as string | undefined;
}

function stageIcon(status: string | undefined, isRunning: boolean): string {
  if (status === "completed" || status === "passed") return "✓";
  if (status === "failed") return "✗";
  if (status === "waiting") return "⏸";
  if (isRunning) return "⟳";
  return "○";
}

function stageColor(status: string | undefined, isRunning: boolean): string {
  if (status === "completed" || status === "passed") return "text-green-600";
  if (status === "failed") return "text-red-600";
  if (status === "waiting") return "text-amber-600";
  if (isRunning) return "text-blue-600 animate-spin";
  return "text-slate-400";
}

export function AutoMigrationStepper({ stepResults, crStatus }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const isExecuting = crStatus === "executing" || crStatus === "verifying";

  // Determine which stage is currently running
  const completedCount = STAGES.filter(s => {
    const st = stageStatus(s.key, stepResults);
    return st === "completed" || st === "passed";
  }).length;
  const currentRunningIdx = isExecuting && completedCount < STAGES.length ? completedCount : -1;

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-5 mt-4">
      <h2 className="text-sm font-semibold text-slate-900 mb-4 flex items-center gap-2">
        <span className="text-purple-500">✨</span> Autonomous Migration Progress
      </h2>

      <div className="space-y-0.5">
        {STAGES.map((stage, idx) => {
          const status = stageStatus(stage.key, stepResults);
          const isRunning = idx === currentRunningIdx;
          const isExpanded = expanded === stage.key;
          const result = stepResults?.[stage.key];
          const hasResult = result !== undefined;

          return (
            <div key={stage.key}>
              <button
                onClick={() => hasResult ? setExpanded(isExpanded ? null : stage.key) : undefined}
                className={`w-full flex items-start gap-3 py-2 px-2 rounded text-left transition-colors ${hasResult ? "hover:bg-slate-50 cursor-pointer" : "cursor-default"}`}
              >
                <span className={`text-sm font-mono w-4 flex-shrink-0 mt-0.5 ${stageColor(status, isRunning)}`}>
                  {stageIcon(status, isRunning)}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-sm font-medium ${status ? "text-slate-900" : "text-slate-400"}`}>
                      {stage.label}
                    </span>
                    {status === "waiting" && (
                      <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">
                        Awaiting confirmation
                      </span>
                    )}
                    {status === "failed" && (
                      <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">
                        Failed
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-0.5">{stage.desc}</p>
                </div>
                {hasResult && (
                  <span className="text-xs text-slate-400 flex-shrink-0 mt-1">
                    {isExpanded ? "▲" : "▼"}
                  </span>
                )}
              </button>

              {isExpanded && result && (
                <div className="ml-7 mb-2 bg-slate-50 rounded p-3">
                  {stage.key === "ai_analysis" && Array.isArray((result as Record<string, unknown>).migration_units) ? (
                    <div className="space-y-2">
                      {((result as Record<string, unknown>).migration_units as Array<Record<string, unknown>>).map((unit, i) => (
                        <div key={i} className="border border-slate-200 rounded p-2 text-xs bg-white">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-slate-900">{String(unit.name ?? "")}</span>
                            <span className={`px-1.5 py-0.5 rounded ${unit.stateful ? "bg-amber-50 text-amber-700" : "bg-green-50 text-green-700"}`}>
                              {unit.stateful ? "stateful" : "stateless"}
                            </span>
                            <span className="bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                              {String(unit.pattern ?? "")}
                            </span>
                          </div>
                          {unit.reasoning && (
                            <p className="text-slate-400 italic mt-1">{String(unit.reasoning)}</p>
                          )}
                          {unit.data_risk && (
                            <p className="text-slate-500 mt-1">Data risk: <span className="font-medium">{String(unit.data_risk)}</span></p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <pre className="text-xs text-slate-600 overflow-x-auto whitespace-pre-wrap max-h-48">
                      {JSON.stringify(result, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
