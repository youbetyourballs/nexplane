import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Play,
  Square,
  Download,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Loader2,
  Terminal,
  Clock,
} from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import {
  smokeTestsApi,
  type SmokeTestSuite,
  type RunConfig,
  type CleanupAsset,
} from "../api/smokeTestsApi";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string | null }) {
  if (!status)
    return <span className="text-slate-400 text-xs">Never run</span>;
  if (status === "passed")
    return (
      <span className="inline-flex items-center gap-1 text-emerald-400 text-xs font-medium">
        <CheckCircle2 className="w-3.5 h-3.5" /> Passed
      </span>
    );
  if (status === "failed")
    return (
      <span className="inline-flex items-center gap-1 text-red-400 text-xs font-medium">
        <XCircle className="w-3.5 h-3.5" /> Failed
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-yellow-400 text-xs font-medium">
      <AlertCircle className="w-3.5 h-3.5" /> Unknown
    </span>
  );
}

function RunningBadge() {
  return (
    <span className="inline-flex items-center gap-1 text-blue-400 text-xs font-medium animate-pulse">
      <Loader2 className="w-3.5 h-3.5 animate-spin" /> Running
    </span>
  );
}

// ---------------------------------------------------------------------------
// Run Config Modal
// ---------------------------------------------------------------------------

interface RunModalProps {
  suite: SmokeTestSuite;
  onClose: () => void;
  onRun: (config: RunConfig) => void;
  isRunning: boolean;
}

function RunModal({ suite, onClose, onRun, isRunning }: RunModalProps) {
  const [phases, setPhases] = useState(suite.default_phases);
  const [tailscaleKey, setTailscaleKey] = useState("");
  const [gcpProject, setGcpProject] = useState("");
  const [azureRg, setAzureRg] = useState("");

  const handleRun = () => {
    onRun({
      suite: suite.id,
      phases: phases || undefined,
      tailscale_auth_key: tailscaleKey || undefined,
      gcp_project: gcpProject || undefined,
      azure_resource_group: azureRg || undefined,
    });
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-navy-light border border-navy-border rounded-xl w-full max-w-lg p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-white font-semibold text-lg">
          Run {suite.name} Smoke Test
        </h2>
        <p className="text-slate-400 text-sm">{suite.description}</p>

        {suite.id !== "parallel" && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">
              Phases
            </label>
            <input
              className="w-full bg-navy border border-navy-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={phases}
              onChange={(e) => setPhases(e.target.value)}
              placeholder={suite.default_phases || "e.g. A,B,C,D"}
            />
            {suite.slow_phases && (
              <p className="text-slate-500 text-xs mt-1">
                Slow phases (excluded by default): {suite.slow_phases}
              </p>
            )}
          </div>
        )}

        {(suite.id === "aws" ||
          suite.id === "agent" ||
          suite.id === "parallel") && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">
              Tailscale Auth Key
            </label>
            <input
              className="w-full bg-navy border border-navy-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={tailscaleKey}
              onChange={(e) => setTailscaleKey(e.target.value)}
              placeholder="tskey-auth-..."
            />
          </div>
        )}

        {(suite.id === "gcp" ||
          suite.id === "agent" ||
          suite.id === "parallel") && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">
              GCP Project ID
            </label>
            <input
              className="w-full bg-navy border border-navy-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={gcpProject}
              onChange={(e) => setGcpProject(e.target.value)}
              placeholder="my-gcp-project"
            />
          </div>
        )}

        {(suite.id === "azure" ||
          suite.id === "agent" ||
          suite.id === "parallel") && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">
              Azure Resource Group
            </label>
            <input
              className="w-full bg-navy border border-navy-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={azureRg}
              onChange={(e) => setAzureRg(e.target.value)}
              placeholder="nexplane-smoke-rg"
            />
          </div>
        )}

        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-slate-400 hover:text-white text-sm transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleRun}
            disabled={isRunning}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {isRunning ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4" />
            )}
            Start Run
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Log Panel
// ---------------------------------------------------------------------------

interface LogPanelProps {
  runId: string;
  onStop: () => void;
}

function LogPanel({ runId, onStop }: LogPanelProps) {
  const [content, setContent] = useState("");
  const [done, setDone] = useState(false);
  const offsetRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (done) return;
    const poll = setInterval(async () => {
      try {
        const chunk = await smokeTestsApi.getLogs(runId, offsetRef.current);
        if (chunk.content) {
          setContent((prev) => prev + chunk.content);
          offsetRef.current = chunk.next_offset;
          bottomRef.current?.scrollIntoView({ behavior: "smooth" });
        }
        if (chunk.done) {
          setDone(true);
          clearInterval(poll);
        }
      } catch {
        // ignore transient errors
      }
    }, 2000);
    return () => clearInterval(poll);
  }, [runId, done]);

  const downloadLog = () => {
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${runId}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const cleanContent = content.replace(/\x1b\[[0-9;]*m/g, "");

  return (
    <div className="mt-6 bg-navy border border-navy-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-navy-border">
        <div className="flex items-center gap-2 text-slate-300 text-sm font-medium">
          <Terminal className="w-4 h-4" />
          Live Output — {runId}
          {!done && (
            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" />
          )}
          {done && (
            <span className="text-slate-500 text-xs">(completed)</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={downloadLog}
            className="inline-flex items-center gap-1 text-slate-400 hover:text-white text-xs transition-colors"
          >
            <Download className="w-3.5 h-3.5" /> Download
          </button>
          {!done && (
            <button
              onClick={onStop}
              className="inline-flex items-center gap-1 text-red-400 hover:text-red-300 text-xs transition-colors"
            >
              <Square className="w-3.5 h-3.5" /> Stop
            </button>
          )}
        </div>
      </div>
      <pre className="p-4 text-xs text-slate-300 font-mono overflow-auto max-h-96 whitespace-pre-wrap leading-relaxed">
        {cleanContent || "Waiting for output..."}
        <div ref={bottomRef} />
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Suite Card
// ---------------------------------------------------------------------------

interface SuiteCardProps {
  suite: SmokeTestSuite;
  onRun: (suite: SmokeTestSuite) => void;
  activeRunId: string | null;
}

function SuiteCard({ suite, onRun, activeRunId }: SuiteCardProps) {
  const isRunning =
    suite.running_run_id !== null ||
    (activeRunId !== null && activeRunId.startsWith(suite.id + "-"));
  const last = suite.last_run;

  return (
    <div className="bg-navy-light border border-navy-border rounded-xl p-5 flex flex-col gap-3">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-white font-semibold text-base">{suite.name}</h3>
          <p className="text-slate-400 text-xs mt-0.5">{suite.description}</p>
        </div>
        <button
          onClick={() => onRun(suite)}
          disabled={isRunning}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-medium rounded-lg transition-colors shrink-0"
        >
          {isRunning ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
          Run
        </button>
      </div>

      <div className="flex items-center gap-4 text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-slate-500">Status:</span>
          {isRunning ? (
            <RunningBadge />
          ) : (
            <StatusBadge status={last?.status ?? null} />
          )}
        </div>
        {last && !isRunning && (
          <>
            <div className="text-slate-500">
              {last.phases_passed}/{last.phases_passed + last.phases_failed}{" "}
              phases
            </div>
            <div className="flex items-center gap-1 text-slate-500">
              <Clock className="w-3 h-3" />
              {new Date(last.finished_at).toLocaleTimeString()}
            </div>
          </>
        )}
      </div>

      {last && last.phases_failed_list.length > 0 && !isRunning && (
        <div className="text-xs text-red-400">
          Failed: {last.phases_failed_list.join(", ")}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cleanup Modal
// ---------------------------------------------------------------------------

function CleanupModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();

  const { data: preview, isLoading: isLoadingPreview, isError: isPreviewError } = useQuery({
    queryKey: ["smoke-cleanup-preview"],
    queryFn: smokeTestsApi.getCleanupPreview,
  });

  const deleteMutation = useMutation({
    mutationFn: smokeTestsApi.executeCleanup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["smoke-cleanup-preview"] });
    },
  });

  const isEmpty = !isLoadingPreview && !isPreviewError && preview?.count === 0;
  const isDone = deleteMutation.isSuccess;

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-navy-light border border-navy-border rounded-xl w-full max-w-lg mx-4 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-white font-semibold text-base">Clean Up Inventory</h2>
          <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-white text-xl leading-none">&times;</button>
        </div>

        {isLoadingPreview && (
          <div className="flex items-center gap-2 text-slate-400 text-sm py-4">
            <Loader2 className="w-4 h-4 animate-spin" />
            Scanning inventory…
          </div>
        )}

        {isPreviewError && (
          <p className="text-red-400 text-sm">Failed to load preview — please try again.</p>
        )}

        {isEmpty && (
          <p className="text-slate-400 text-sm py-2">
            No smoke test assets found — inventory is clean.
          </p>
        )}

        {isDone && (
          <p className="text-green-400 text-sm py-2">
            Deleted {deleteMutation.data?.deleted ?? 0} asset{deleteMutation.data?.deleted !== 1 ? "s" : ""}.
          </p>
        )}

        {!isLoadingPreview && !isPreviewError && !isEmpty && !isDone && preview && (
          <div className="space-y-1 max-h-64 overflow-y-auto">
            <p className="text-slate-400 text-xs mb-2">
              {preview.count} asset{preview.count !== 1 ? "s" : ""} will be removed from the inventory:
            </p>
            {preview.assets.map((a: CleanupAsset) => (
              <div key={a.id} className="flex items-center justify-between px-3 py-1.5 bg-navy rounded-lg text-xs">
                <span className="text-white font-mono">{a.name}</span>
                <span className="text-slate-400">{a.asset_type}</span>
              </div>
            ))}
          </div>
        )}

        {deleteMutation.isError && (
          <p className="text-red-400 text-sm">Deletion failed — please try again.</p>
        )}

        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-300 hover:text-white transition-colors"
          >
            {isDone ? "Close" : "Cancel"}
          </button>
          {!isEmpty && !isDone && (
            <button
              onClick={() => deleteMutation.mutate()}
              disabled={isLoadingPreview || deleteMutation.isPending || !preview}
              className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : null}
              Delete {preview?.count ?? "…"} asset{(preview?.count ?? 2) !== 1 ? "s" : ""}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export function SmokeTests() {
  const queryClient = useQueryClient();
  const [modalSuite, setModalSuite] = useState<SmokeTestSuite | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [showCleanupModal, setShowCleanupModal] = useState(false);

  const { data: suites, isLoading } = useQuery({
    queryKey: ["smoke-tests-suites"],
    queryFn: smokeTestsApi.getSuites,
    refetchInterval: activeRunId ? 5000 : 30000,
  });

  const runMutation = useMutation({
    mutationFn: smokeTestsApi.triggerRun,
    onSuccess: (data) => {
      setActiveRunId(data.run_id);
      setModalSuite(null);
      queryClient.invalidateQueries({ queryKey: ["smoke-tests-suites"] });
    },
  });

  const stopMutation = useMutation({
    mutationFn: () => smokeTestsApi.stopRun(activeRunId!),
    onSuccess: () => {
      setActiveRunId(null);
      queryClient.invalidateQueries({ queryKey: ["smoke-tests-suites"] });
    },
  });

  const handleRun = useCallback(
    (config: RunConfig) => {
      runMutation.mutate(config);
    },
    [runMutation]
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-blue-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto py-8 px-6 space-y-6">
      <div className="flex items-start justify-between">
        <PageHeader
          title="Smoke Tests"
          subtitle="Live end-to-end connector verification against real cloud infrastructure"
        />
        <button
          onClick={() => setShowCleanupModal(true)}
          className="shrink-0 mt-1 inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-600 hover:border-slate-400 text-slate-300 hover:text-white text-xs font-medium rounded-lg transition-colors"
        >
          Clean Up Inventory
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {(suites ?? []).map((suite) => (
          <SuiteCard
            key={suite.id}
            suite={suite}
            onRun={setModalSuite}
            activeRunId={activeRunId}
          />
        ))}
      </div>

      {activeRunId && (
        <LogPanel runId={activeRunId} onStop={() => stopMutation.mutate()} />
      )}

      {modalSuite && (
        <RunModal
          suite={modalSuite}
          onClose={() => setModalSuite(null)}
          onRun={handleRun}
          isRunning={runMutation.isPending}
        />
      )}
      {showCleanupModal && (
        <CleanupModal onClose={() => setShowCleanupModal(false)} />
      )}
    </div>
  );
}
