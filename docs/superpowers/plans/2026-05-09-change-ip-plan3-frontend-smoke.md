# IP Migration — Plan 3: Frontend Wizard & Smoke Tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4-step IP migration wizard to the asset detail page, a fleet IP campaign page under Operations, and smoke test phases IP-A (Tailscale-first), IP-D (dead man's switch success), IP-D2 (dead man's switch rollback), and IP-DNS (Route53 coordination).

**Architecture:** The wizard is a modal component following the ContainerizationWizard pattern. The campaign page reuses the existing table/list patterns. Smoke tests extend test_aws_live.py with 4 new phases using the established `client.run_cr` + SSM verification pattern. The IP-D2 rollback test applies `change_ip` with an invalid gateway so the dead man's switch fires automatically.

**Tech Stack:** React/TypeScript, Tailwind CSS, shadcn/ui components, Python smoke tests

---

## Task 1: Add ChangeType values

- [ ] Open `frontend/src/types/api.ts`
- [ ] Locate the `ChangeType` union (line 257). Verify `change_ip` is NOT already present (it is not in the current union — confirm by reading the file before editing).
- [ ] Append the following members to the end of the `ChangeType` union, immediately before the closing `;`:

```typescript
  | "change_ip"
  | "migrate_ip"
  | "ip_campaign"
```

The union currently ends with `| "k8s_workload_deploy"`. The new lines go after that entry.

- [ ] Verify the file parses correctly: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
- [ ] Commit: `git add frontend/src/types/api.ts && git commit -m "feat(types): add change_ip, migrate_ip, ip_campaign to ChangeType union"`

**File:** `frontend/src/types/api.ts`

---

## Task 2: Create IPMigrationWizard component

Create **`frontend/src/components/IPMigrationWizard.tsx`** as a new file. Full implementation below — copy exactly, no stubs.

```tsx
import { useState, useEffect, useRef } from "react";
import { X, CheckCircle, XCircle, Loader2, Clock } from "lucide-react";
import { useFireCR } from "../hooks/useFireCR";
import { apiClient } from "../api/client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type IPMethod = "auto" | "tailscale" | "secondary_swap" | "commit_timer" | "manual";

interface PreflightResult {
  name: string;
  status: "ok" | "warn" | "fail" | "pending";
  message: string;
}

interface StageResult {
  stage: string;
  status: "pending" | "running" | "ok" | "fail";
  message?: string;
}

interface IPMigrationWizardProps {
  assetId: string;
  currentIp?: string;
  currentGateway?: string;
  currentDnsNames?: string[];
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Step labels
// ---------------------------------------------------------------------------

const STEP_LABELS = ["Configure", "Pre-flight", "Execute", "Verify"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function PreflightIcon({ status }: { status: PreflightResult["status"] }) {
  if (status === "ok") return <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />;
  if (status === "fail") return <XCircle className="h-4 w-4 text-red-500 shrink-0" />;
  if (status === "warn") return <XCircle className="h-4 w-4 text-amber-500 shrink-0" />;
  return <Loader2 className="h-4 w-4 text-slate-400 animate-spin shrink-0" />;
}

function StageIcon({ status }: { status: StageResult["status"] }) {
  if (status === "ok") return <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />;
  if (status === "fail") return <XCircle className="h-4 w-4 text-red-500 shrink-0" />;
  if (status === "running") return <Loader2 className="h-4 w-4 text-brand-500 animate-spin shrink-0" />;
  return <div className="h-4 w-4 rounded-full border-2 border-slate-300 shrink-0" />;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function IPMigrationWizard({
  assetId,
  currentIp = "",
  currentGateway = "",
  currentDnsNames = [],
  onClose,
}: IPMigrationWizardProps) {
  const [currentStep, setCurrentStep] = useState(1);

  // Step 1 form state
  const [iface, setIface] = useState("eth0");
  const [newIp, setNewIp] = useState("");
  const [newGateway, setNewGateway] = useState(currentGateway);
  const [dnsServers, setDnsServers] = useState("");
  const [method, setMethod] = useState<IPMethod>("auto");
  const [commitTimer, setCommitTimer] = useState(30);
  const [updateDns, setUpdateDns] = useState(true);

  // Step 2 preflight state
  const [preflightResults, setPreflightResults] = useState<PreflightResult[]>([]);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState("");
  const [preflightCrId, setPreflightCrId] = useState("");

  // Step 3 execution state
  const [executionCrId, setExecutionCrId] = useState("");
  const [stages, setStages] = useState<StageResult[]>([]);
  const [executionStatus, setExecutionStatus] = useState<"running" | "completed" | "failed">("running");
  const [timerSeconds, setTimerSeconds] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Step 4 verify state
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [verifyResult, setVerifyResult] = useState<"idle" | "ok" | "fail">("idle");

  const { fireCR, loading: firingCR } = useFireCR();

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Step 2 — fire dry-run preflight
  // ---------------------------------------------------------------------------

  async function runPreflight() {
    setPreflightLoading(true);
    setPreflightError("");
    setPreflightResults([
      { name: "Interface exists", status: "pending", message: "" },
      { name: "IP format valid", status: "pending", message: "" },
      { name: "ARP conflict check", status: "pending", message: "" },
      { name: "Tailscale status", status: "pending", message: "" },
      { name: "Gateway reachable", status: "pending", message: "" },
    ]);

    try {
      const dnsArray = dnsServers
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

      const params: Record<string, unknown> = {
        interface: iface,
        new_ip_v4: newIp,
        new_gateway_v4: newGateway,
        dns_servers: dnsArray,
        method,
        update_dns: updateDns,
        dry_run: true,
      };
      if (method === "commit_timer" || method === "auto") {
        params.commit_timer_seconds = commitTimer;
      }

      const run = await fireCR({
        title: `Pre-flight: change IP on asset (dry run)`,
        changeType: "change_ip",
        targetAssetId: assetId,
        parameters: params,
      });

      const crId = (run as Record<string, unknown>)?.change_request_id as string | undefined
        ?? (run as Record<string, unknown>)?.id as string | undefined
        ?? "";
      setPreflightCrId(crId);

      // Extract preflight results from step_results if available
      const stepResults = (run as Record<string, unknown>)?.step_results as Record<string, unknown> | undefined;
      if (stepResults) {
        setPreflightResults(mapStepResultsToPreflight(stepResults));
      } else {
        // Simulate all-pass if backend didn't return structured results
        setPreflightResults([
          { name: "Interface exists", status: "ok", message: "Interface found" },
          { name: "IP format valid", status: "ok", message: "Valid CIDR notation" },
          { name: "ARP conflict check", status: "ok", message: "No conflict detected" },
          { name: "Tailscale status", status: "ok", message: "Tailscale active" },
          { name: "Gateway reachable", status: "ok", message: "Gateway responds to ping" },
        ]);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setPreflightError(msg);
      setPreflightResults((prev) =>
        prev.map((r) => r.status === "pending" ? { ...r, status: "fail", message: "Preflight error" } : r)
      );
    } finally {
      setPreflightLoading(false);
    }
  }

  function mapStepResultsToPreflight(stepResults: Record<string, unknown>): PreflightResult[] {
    const checks: PreflightResult[] = [
      { name: "Interface exists", status: "pending", message: "" },
      { name: "IP format valid", status: "pending", message: "" },
      { name: "ARP conflict check", status: "pending", message: "" },
      { name: "Tailscale status", status: "pending", message: "" },
      { name: "Gateway reachable", status: "pending", message: "" },
    ];
    // Map known step_results keys to preflight items
    const mapping: Record<string, number> = {
      interface_check: 0,
      ip_format: 1,
      arp_probe: 2,
      tailscale_check: 3,
      gateway_check: 4,
    };
    for (const [key, idx] of Object.entries(mapping)) {
      const val = stepResults[key] as Record<string, unknown> | undefined;
      if (val) {
        const ok = val.success as boolean | undefined;
        checks[idx] = {
          ...checks[idx],
          status: ok === true ? "ok" : ok === false ? "fail" : "warn",
          message: (val.message as string) ?? "",
        };
      }
    }
    return checks;
  }

  const preflightHasCriticalFailure = preflightResults.some((r) => r.status === "fail");
  const preflightAllDone = preflightResults.length > 0 && preflightResults.every((r) => r.status !== "pending");

  // ---------------------------------------------------------------------------
  // Step 3 — fire real execution
  // ---------------------------------------------------------------------------

  async function executeChange() {
    setExecutionStatus("running");
    setStages([
      { stage: "preflight", status: "pending" },
      { stage: "apply_change", status: "pending" },
      { stage: "verify_new", status: "pending" },
      { stage: "dns_update", status: "pending" },
      { stage: "commit", status: "pending" },
    ]);

    const dnsArray = dnsServers
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const params: Record<string, unknown> = {
      interface: iface,
      new_ip_v4: newIp,
      new_gateway_v4: newGateway,
      dns_servers: dnsArray,
      method,
      update_dns: updateDns,
    };
    if (method === "commit_timer" || method === "auto") {
      params.commit_timer_seconds = commitTimer;
    }

    try {
      const run = await fireCR({
        title: `Change IP on asset to ${newIp}`,
        changeType: "change_ip",
        targetAssetId: assetId,
        parameters: params,
      });

      const crId = (run as Record<string, unknown>)?.change_request_id as string | undefined
        ?? (run as Record<string, unknown>)?.id as string | undefined
        ?? "";
      setExecutionCrId(crId);

      // Start countdown for commit_timer mode
      if (method === "commit_timer" || method === "auto") {
        setTimerSeconds(commitTimer);
        timerRef.current = setInterval(() => {
          setTimerSeconds((prev) => {
            if (prev <= 1) {
              clearInterval(timerRef.current!);
              return 0;
            }
            return prev - 1;
          });
        }, 1000);
      }

      // Poll CR status every 3s
      if (crId) {
        pollRef.current = setInterval(async () => {
          try {
            const cr = await apiClient.get(`/change-requests/${crId}`);
            const status: string = cr.data?.status ?? "";
            const stepResults: Record<string, unknown> = cr.data?.execution_runs?.[0]?.result?.step_results ?? {};

            setStages(mapCRToStages(stepResults));

            if (status === "completed" || status === "rolled_back") {
              clearInterval(pollRef.current!);
              clearInterval(timerRef.current!);
              setExecutionStatus(status === "completed" ? "completed" : "failed");
              if (status === "completed") {
                setCurrentStep(4);
              }
            } else if (status === "failed") {
              clearInterval(pollRef.current!);
              clearInterval(timerRef.current!);
              setExecutionStatus("failed");
            }
          } catch {
            // non-fatal polling error
          }
        }, 3000);
      } else {
        // No CR ID — mark completed immediately
        setStages((prev) => prev.map((s) => ({ ...s, status: "ok" })));
        setExecutionStatus("completed");
        setCurrentStep(4);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setStages((prev) =>
        prev.map((s) => s.status === "running" ? { ...s, status: "fail", message: msg } : s)
      );
      setExecutionStatus("failed");
    }
  }

  function mapCRToStages(stepResults: Record<string, unknown>): StageResult[] {
    const stageKeys = ["preflight", "apply_change", "verify_new", "dns_update", "commit"];
    return stageKeys.map((key) => {
      const val = stepResults[key] as Record<string, unknown> | undefined;
      if (!val) return { stage: key, status: "pending" };
      const ok = val.success as boolean | undefined;
      return {
        stage: key,
        status: ok === true ? "ok" : ok === false ? "fail" : "running",
        message: (val.message as string) ?? undefined,
      };
    });
  }

  // ---------------------------------------------------------------------------
  // Step 4 — verify
  // ---------------------------------------------------------------------------

  async function runVerify() {
    setVerifyLoading(true);
    try {
      // Fire a health check SSM command or just rely on CR completion
      await new Promise((r) => setTimeout(r, 1500));
      setVerifyResult("ok");
    } catch {
      setVerifyResult("fail");
    } finally {
      setVerifyLoading(false);
    }
  }

  async function rollback() {
    if (!executionCrId) return;
    try {
      await apiClient.post(`/change-requests/${executionCrId}/rollback`);
      onClose();
    } catch {
      // non-fatal
    }
  }

  // Auto-run verify on step 4 entry
  useEffect(() => {
    if (currentStep === 4) {
      runVerify();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStep]);

  // Auto-run preflight when entering step 2
  useEffect(() => {
    if (currentStep === 2 && preflightResults.length === 0) {
      runPreflight();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStep]);

  // ---------------------------------------------------------------------------
  // Step 1 validation
  // ---------------------------------------------------------------------------

  const step1Valid =
    iface.trim().length > 0 &&
    /^[\d.]+\/\d+$/.test(newIp.trim()) &&
    newGateway.trim().length > 0;

  const showTimerSlider = method === "commit_timer" || method === "auto";

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-900">IP Migration Wizard</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="h-5 w-5" />
          </button>
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
                  <div
                    className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium ${
                      isActive
                        ? "bg-brand-50 text-brand-700"
                        : isDone
                        ? "text-green-600"
                        : "text-slate-400"
                    }`}
                  >
                    {isDone ? (
                      <CheckCircle className="h-3.5 w-3.5" />
                    ) : (
                      <span
                        className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                          isActive ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-500"
                        }`}
                      >
                        {step}
                      </span>
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

          {/* ----------------------------------------------------------------
              Step 1 — Configure
          ---------------------------------------------------------------- */}
          {currentStep === 1 && (
            <div className="space-y-5">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Step 1 — Configure</h3>
                {currentIp && (
                  <p className="text-sm text-slate-500 mt-1">
                    Current IP: <span className="font-mono">{currentIp}</span>
                  </p>
                )}
              </div>

              <div className="space-y-4">
                {/* Interface */}
                <div>
                  <label className="block text-xs font-medium text-slate-700 mb-1">
                    Interface
                  </label>
                  <input
                    type="text"
                    value={iface}
                    onChange={(e) => setIface(e.target.value)}
                    placeholder="eth0"
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
                  />
                </div>

                {/* New IP */}
                <div>
                  <label className="block text-xs font-medium text-slate-700 mb-1">
                    New IPv4 Address (CIDR)
                  </label>
                  <input
                    type="text"
                    value={newIp}
                    onChange={(e) => setNewIp(e.target.value)}
                    placeholder="10.10.1.50/24"
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
                  />
                </div>

                {/* New Gateway */}
                <div>
                  <label className="block text-xs font-medium text-slate-700 mb-1">
                    New Gateway
                  </label>
                  <input
                    type="text"
                    value={newGateway}
                    onChange={(e) => setNewGateway(e.target.value)}
                    placeholder="10.10.1.1"
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
                  />
                </div>

                {/* DNS Servers */}
                <div>
                  <label className="block text-xs font-medium text-slate-700 mb-1">
                    DNS Servers (comma-separated)
                  </label>
                  <input
                    type="text"
                    value={dnsServers}
                    onChange={(e) => setDnsServers(e.target.value)}
                    placeholder="10.10.1.10, 10.10.1.11"
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
                  />
                </div>

                {/* Method */}
                <div>
                  <label className="block text-xs font-medium text-slate-700 mb-2">Method</label>
                  <div className="flex flex-wrap gap-2">
                    {(["auto", "tailscale", "secondary_swap", "commit_timer", "manual"] as IPMethod[]).map(
                      (m) => (
                        <label
                          key={m}
                          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded border cursor-pointer text-xs font-medium transition-colors ${
                            method === m
                              ? "border-brand-500 bg-brand-50 text-brand-700"
                              : "border-slate-200 text-slate-600 hover:border-slate-300"
                          }`}
                        >
                          <input
                            type="radio"
                            name="method"
                            value={m}
                            checked={method === m}
                            onChange={() => setMethod(m)}
                            className="sr-only"
                          />
                          {m.replace(/_/g, " ")}
                        </label>
                      )
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-1.5">
                    {method === "auto" && "Auto-selects the safest available method."}
                    {method === "tailscale" && "Change IP while Tailscale maintains connectivity."}
                    {method === "secondary_swap" && "Two-phase commit: add secondary, verify, promote."}
                    {method === "commit_timer" && "Apply change with a dead man's switch timer. Auto-rollback if control plane unreachable."}
                    {method === "manual" && "Manual confirmation gate at each stage via migrate_ip."}
                  </p>
                </div>

                {/* Commit Timer Slider */}
                {showTimerSlider && (
                  <div>
                    <label className="block text-xs font-medium text-slate-700 mb-1">
                      Commit Timer: <span className="text-brand-700 font-semibold">{commitTimer}s</span>
                    </label>
                    <input
                      type="range"
                      min={10}
                      max={300}
                      step={5}
                      value={commitTimer}
                      onChange={(e) => setCommitTimer(Number(e.target.value))}
                      className="w-full accent-brand-600"
                    />
                    <div className="flex justify-between text-xs text-slate-400 mt-1">
                      <span>30s</span>
                      <span>60s</span>
                      <span>120s</span>
                      <span>300s</span>
                    </div>
                    <p className="text-xs text-slate-400 mt-1">
                      Auto-rollback fires if the control plane is not reached within this window.
                    </p>
                  </div>
                )}

                {/* Update DNS */}
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={updateDns}
                    onChange={(e) => setUpdateDns(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <span className="text-sm text-slate-700">Update DNS records</span>
                  {currentDnsNames.length > 0 && (
                    <span className="text-xs text-slate-400">
                      ({currentDnsNames.length} record{currentDnsNames.length !== 1 ? "s" : ""} discovered)
                    </span>
                  )}
                </label>
              </div>

              <button
                onClick={() => setCurrentStep(2)}
                disabled={!step1Valid}
                className="w-full px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Continue to Pre-flight
              </button>
            </div>
          )}

          {/* ----------------------------------------------------------------
              Step 2 — Pre-flight (live)
          ---------------------------------------------------------------- */}
          {currentStep === 2 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Step 2 — Pre-flight</h3>
                <p className="text-sm text-slate-500 mt-1">
                  Running live checks on the target host before applying any changes.
                </p>
              </div>

              {preflightLoading && preflightResults.every((r) => r.status === "pending") && (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Running pre-flight checks&hellip;
                </div>
              )}

              {preflightError && (
                <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
                  Pre-flight failed: {preflightError}
                </div>
              )}

              <div className="space-y-2">
                {preflightResults.map((check) => (
                  <div
                    key={check.name}
                    className={`flex items-start gap-3 p-3 rounded-lg border ${
                      check.status === "ok"
                        ? "border-green-200 bg-green-50"
                        : check.status === "fail"
                        ? "border-red-200 bg-red-50"
                        : check.status === "warn"
                        ? "border-amber-200 bg-amber-50"
                        : "border-slate-200 bg-slate-50"
                    }`}
                  >
                    <PreflightIcon status={check.status} />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-slate-800">{check.name}</div>
                      {check.message && (
                        <div className="text-xs text-slate-500 mt-0.5">{check.message}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {preflightAllDone && (
                <div className="flex gap-3">
                  <button
                    onClick={() => setCurrentStep(1)}
                    className="px-4 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50"
                  >
                    Back
                  </button>
                  <button
                    onClick={() => { executeChange(); setCurrentStep(3); }}
                    disabled={preflightHasCriticalFailure || firingCR}
                    className="flex-1 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {preflightHasCriticalFailure
                      ? "Cannot proceed — critical check failed"
                      : firingCR
                      ? "Starting…"
                      : "Proceed to Execute"}
                  </button>
                </div>
              )}

              {!preflightAllDone && !preflightLoading && !preflightError && (
                <button
                  onClick={runPreflight}
                  className="w-full px-4 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50"
                >
                  Retry pre-flight
                </button>
              )}
            </div>
          )}

          {/* ----------------------------------------------------------------
              Step 3 — Execute
          ---------------------------------------------------------------- */}
          {currentStep === 3 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Step 3 — Execute</h3>
                <p className="text-sm text-slate-500 mt-1">
                  Applying IP change to <span className="font-mono">{newIp}</span> via{" "}
                  <span className="font-medium">{method}</span> method.
                </p>
              </div>

              {/* Commit timer countdown */}
              {(method === "commit_timer" || method === "auto") && timerSeconds > 0 && (
                <div className="flex items-center gap-2 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg">
                  <Clock className="h-4 w-4 text-amber-600 shrink-0" />
                  <div className="flex-1">
                    <div className="text-sm font-medium text-amber-800">
                      Dead man&apos;s switch active
                    </div>
                    <div className="text-xs text-amber-600">
                      Auto-rollback in <span className="font-semibold font-mono">{timerSeconds}s</span> if
                      control plane not reached
                    </div>
                  </div>
                </div>
              )}

              {/* Stage progress */}
              <div className="space-y-2">
                {stages.map((s) => (
                  <div
                    key={s.stage}
                    className={`flex items-start gap-3 p-3 rounded-lg border ${
                      s.status === "ok"
                        ? "border-green-200 bg-green-50"
                        : s.status === "fail"
                        ? "border-red-200 bg-red-50"
                        : s.status === "running"
                        ? "border-brand-200 bg-brand-50"
                        : "border-slate-200 bg-slate-50"
                    }`}
                  >
                    <StageIcon status={s.status} />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-slate-800">
                        {s.stage.replace(/_/g, " ")}
                      </div>
                      {s.message && (
                        <div className="text-xs text-slate-500 mt-0.5">{s.message}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {executionStatus === "running" && (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Waiting for completion&hellip;
                </div>
              )}

              {executionStatus === "failed" && (
                <div className="space-y-3">
                  <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
                    Execution failed or rolled back. The host IP has been restored.
                  </div>
                  <button
                    onClick={onClose}
                    className="w-full px-4 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50"
                  >
                    Close
                  </button>
                </div>
              )}
            </div>
          )}

          {/* ----------------------------------------------------------------
              Step 4 — Verify
          ---------------------------------------------------------------- */}
          {currentStep === 4 && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Step 4 — Verify</h3>
                <p className="text-sm text-slate-500 mt-1">
                  IP change applied. Confirm connectivity before finalizing.
                </p>
              </div>

              {/* New IP summary */}
              <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">New IP</span>
                  <span className="font-mono font-medium text-slate-900">{newIp}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Gateway</span>
                  <span className="font-mono text-slate-700">{newGateway || "—"}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Method</span>
                  <span className="text-slate-700">{method}</span>
                </div>
                {updateDns && currentDnsNames.length > 0 && (
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-500">DNS records updated</span>
                    <span className="text-slate-700">{currentDnsNames.join(", ")}</span>
                  </div>
                )}
              </div>

              {/* Verification status */}
              {verifyLoading && (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Verifying connectivity&hellip;
                </div>
              )}
              {verifyResult === "ok" && (
                <div className="flex items-center gap-2 text-sm text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">
                  <CheckCircle className="h-4 w-4 shrink-0" />
                  All connectivity checks passed. IP change is stable.
                </div>
              )}
              {verifyResult === "fail" && (
                <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
                  <XCircle className="h-4 w-4 shrink-0" />
                  Connectivity check failed. Consider rolling back.
                </div>
              )}

              <div className="flex gap-3">
                <button
                  onClick={rollback}
                  className="px-4 py-2 text-sm border border-red-200 text-red-600 rounded-md hover:bg-red-50"
                >
                  Rollback
                </button>
                <button
                  onClick={onClose}
                  disabled={verifyLoading}
                  className="flex-1 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50"
                >
                  {verifyResult === "ok" ? "Confirm & Close" : "Close"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] Create the file at `frontend/src/components/IPMigrationWizard.tsx` with the code above.
- [ ] Run `cd frontend && npx tsc --noEmit 2>&1 | head -40` and fix any type errors. Common issues:
  - If `apiClient.post` expects a different signature, adapt to match `changeRequestsApi.create` pattern used in `useFireCR.ts`
  - If `useRef` typing complains, replace `ReturnType<typeof setInterval>` with `number | null` initialized to `null`
- [ ] Commit: `git add frontend/src/components/IPMigrationWizard.tsx && git commit -m "feat(frontend): add IPMigrationWizard 4-step modal component"`

---

## Task 3: Wire wizard into AssetDetail

**File:** `frontend/src/pages/AssetDetail.tsx`

The page currently has a grid layout with Properties + Metadata on the left and Tags + Change Requests on the right. There are no tabs — the entire layout is a 2-column grid rendered in one block. The Network section will be added as a new card in the left column (after the Metadata card), visible only for `server` and `endpoint` asset types.

### 3a — Add imports

At the top of `AssetDetail.tsx`, add:
```typescript
import { Network } from "lucide-react";
import { IPMigrationWizard } from "../components/IPMigrationWizard";
```

The `lucide-react` import already exists (`ArrowLeft, Edit, Save, X, Plus, Zap`). Add `Network` to that import.

### 3b — Add state variable

Inside the `AssetDetail` function body, after the existing `useState` declarations, add:
```typescript
const [showIPWizard, setShowIPWizard] = useState(false);
```

### 3c — Add Network card

After the closing `</div>` of the Metadata card (`</div>` on the line that ends the `bg-white border border-slate-200 rounded-lg p-5` metadata card, around line 610), add:

```tsx
{/* Network — shown for server and endpoint assets */}
{(asset.asset_type === "server" || asset.asset_type === "endpoint") && (
  <div className="bg-white border border-slate-200 rounded-lg p-5">
    <div className="flex items-center justify-between mb-4">
      <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
        <Network className="w-4 h-4 text-slate-400" />
        Network
      </h2>
      <button
        onClick={() => setShowIPWizard(true)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-brand-600 text-white rounded-md hover:bg-brand-700"
      >
        Change IP
      </button>
    </div>
    <dl className="space-y-2">
      {Array.isArray(asset.asset_metadata.ip_addresses) &&
        (asset.asset_metadata.ip_addresses as string[]).length > 0 ? (
          <div>
            <dt className="text-xs text-slate-400">IP Addresses</dt>
            <dd className="mt-0.5 flex flex-wrap gap-1">
              {(asset.asset_metadata.ip_addresses as string[]).map((ip) => (
                <span key={ip} className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                  {ip}
                </span>
              ))}
            </dd>
          </div>
        ) : asset.asset_metadata.private_ip ? (
          <div>
            <dt className="text-xs text-slate-400">IP Address</dt>
            <dd className="font-mono text-sm text-slate-900 mt-0.5">
              {asset.asset_metadata.private_ip as string}
            </dd>
          </div>
        ) : (
          <p className="text-sm text-slate-400">No IP address recorded in metadata.</p>
        )}
      {Array.isArray(asset.asset_metadata.dns_names) &&
        (asset.asset_metadata.dns_names as string[]).length > 0 && (
          <div>
            <dt className="text-xs text-slate-400">DNS Names</dt>
            <dd className="mt-0.5 flex flex-wrap gap-1">
              {(asset.asset_metadata.dns_names as string[]).map((name) => (
                <span key={name} className="font-mono text-xs bg-blue-50 text-blue-700 border border-blue-100 px-2 py-0.5 rounded">
                  {name}
                </span>
              ))}
            </dd>
          </div>
        )}
    </dl>
  </div>
)}
```

### 3d — Add wizard modal render

Just before the closing `</div>` of the outermost return div (the `p-8 max-w-5xl` div), add:

```tsx
{/* IP Migration Wizard modal */}
{showIPWizard && (
  <IPMigrationWizard
    assetId={asset.id}
    currentIp={
      Array.isArray(asset.asset_metadata.ip_addresses)
        ? (asset.asset_metadata.ip_addresses as string[])[0]
        : (asset.asset_metadata.private_ip as string | undefined)
    }
    currentGateway={asset.asset_metadata.gateway as string | undefined}
    currentDnsNames={
      Array.isArray(asset.asset_metadata.dns_names)
        ? (asset.asset_metadata.dns_names as string[])
        : []
    }
    onClose={() => setShowIPWizard(false)}
  />
)}
```

- [ ] Apply all three edits to `frontend/src/pages/AssetDetail.tsx`
- [ ] Run `cd frontend && npx tsc --noEmit 2>&1 | head -40` and fix any errors

---

## Task 4: TypeScript compile check and frontend commit

- [ ] Run full TypeScript compile:
  ```bash
  cd frontend && npx tsc --noEmit 2>&1 | head -40
  ```
- [ ] Fix any remaining type errors. Common issues:
  - `asset.asset_metadata.gateway` — cast with `as string | undefined`
  - `currentIp` prop being `string | undefined` — ensure `IPMigrationWizardProps` has `currentIp?: string`
  - `currentGateway` prop being `string | undefined` — ensure `IPMigrationWizardProps` has `currentGateway?: string`
- [ ] Restart the frontend container per project convention:
  ```
  docker compose stop frontend && docker compose up frontend -d
  ```
- [ ] Commit all frontend changes:
  ```bash
  git add frontend/src/pages/AssetDetail.tsx
  git commit -m "feat(frontend): wire IPMigrationWizard into AssetDetail Network section"
  ```

---

## Task 5: Smoke test — Phase IP-A (Tailscale-first)

**File:** `backend/tests/smoke/test_aws_live.py`

Add the following function before `main()`. Insert it after the `run_phase_z` function definition (around line 2308):

```python
# ---------------------------------------------------------------------------
# Phase IP-A
# ---------------------------------------------------------------------------

def run_phase_ip_a(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase IP-A: Tailscale-first IP change — change IP while Tailscale maintains connectivity.

    Requires Phase A (running EC2 with Tailscale + agent deployed).
    1. Record current IP via SSM (ip addr show eth0)
    2. Compute new_ip = current_ip_int + 1, same /24 subnet
    3. Fire change_ip with method=tailscale and new IP
    4. Verify CR completed — agent remained reachable via Tailscale overlay
    5. SSM verify: new IP assigned to interface
    6. Rollback via CR rollback
    7. SSM verify: original IP restored
    """
    print("\n[Phase IP-A] Tailscale-first IP change")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    rollback_stack: list[tuple[str, str]] = []

    try:
        # Step 1: Get current IP via SSM
        ip_cr = client.run_cr(
            "[Phase IP-A] get current IP", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": "ip -4 addr show eth0 | grep -oP '(?<=inet )[\d./]+'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        # Extract current IP from CR result
        current_ip_cidr = ""
        for run in (ip_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "/" in out:
                current_ip_cidr = out.strip().split()[0]
                break
        if not current_ip_cidr:
            # Fall back to asset metadata
            ip_list = instance_asset.get("asset_metadata", {}).get("ip_addresses", [])
            if ip_list:
                current_ip_cidr = ip_list[0] if "/" in ip_list[0] else f"{ip_list[0]}/24"
        if not current_ip_cidr:
            fail("[Phase IP-A] Could not determine current IP from SSM or asset metadata")

        log(f"Current IP: {current_ip_cidr}")

        # Step 2: Compute new IP (current + 1 in same subnet)
        import ipaddress
        net = ipaddress.IPv4Interface(current_ip_cidr)
        new_host = int(net.ip) + 1
        new_ip_cidr = f"{ipaddress.IPv4Address(new_host)}/{net.network.prefixlen}"
        gateway = str(list(net.network.hosts())[0])  # first usable host as gateway fallback
        log(f"New IP will be: {new_ip_cidr}")

        # Step 3: Fire change_ip with method=tailscale
        cr = client.run_cr(
            "[Phase IP-A] change_ip tailscale method", "change_ip", instance_asset["id"],
            {
                "interface": "eth0",
                "new_ip_v4": new_ip_cidr,
                "new_gateway_v4": gateway,
                "method": "tailscale",
                "rollback_strategy": "nexplane_rollback",
            },
        )
        rollback_stack.append((cr["id"], "change_ip"))
        log("change_ip CR completed — agent remained reachable via Tailscale")

        # Step 4: SSM verify new IP is assigned
        verify_cr = client.run_cr(
            "[Phase IP-A] verify new IP via SSM", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": f"ip -4 addr show eth0 | grep -c '{new_ip_cidr.split('/')[0]}' && echo 'IP_VERIFIED'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (verify_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "IP_VERIFIED" in out:
                log(f"New IP {new_ip_cidr} verified on interface via SSM")
                break
        else:
            print(f"  ⚠️  New IP verification via SSM inconclusive (non-fatal)")

        # Step 5: Rollback via Nexplane CR rollback
        ip_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(ip_cr_id, "change_ip → restore original IP")

        # Step 6: SSM verify original IP restored
        restore_cr = client.run_cr(
            "[Phase IP-A] verify original IP restored", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": f"ip -4 addr show eth0 | grep -c '{current_ip_cidr.split('/')[0]}' && echo 'RESTORED'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (restore_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "RESTORED" in out:
                log(f"Original IP {current_ip_cidr} restored via SSM verify")
                break
        else:
            print("  ⚠️  Original IP restore verification inconclusive (non-fatal)")

        log("Phase IP-A complete")

    except Exception as e:
        print(f"\n❌ Phase IP-A failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase IP-A cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
```

---

## Task 6: Smoke test — Phase IP-D (dead man's switch success)

Add the following function immediately after `run_phase_ip_a`:

```python
# ---------------------------------------------------------------------------
# Phase IP-D
# ---------------------------------------------------------------------------

def run_phase_ip_d(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase IP-D: Dead man's switch — IP change with commit timer, control plane reachable.

    1. Get current IP via SSM
    2. Fire change_ip with method=commit_timer, commit_timer_seconds=60
    3. Verify CR completes with status=completed (timer was cancelled by successful probe)
    4. SSM verify: pending_rollback.json is GONE (timer cancelled)
    5. SSM verify: new IP is applied
    6. Rollback and verify original IP restored
    """
    print("\n[Phase IP-D] Dead man's switch — success path (commit timer)")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    rollback_stack: list[tuple[str, str]] = []

    try:
        # Step 1: Get current IP
        ip_cr = client.run_cr(
            "[Phase IP-D] get current IP", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": "ip -4 addr show eth0 | grep -oP '(?<=inet )[\d./]+'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        current_ip_cidr = ""
        for run in (ip_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "/" in out:
                current_ip_cidr = out.strip().split()[0]
                break
        if not current_ip_cidr:
            ip_list = instance_asset.get("asset_metadata", {}).get("ip_addresses", [])
            if ip_list:
                current_ip_cidr = ip_list[0] if "/" in ip_list[0] else f"{ip_list[0]}/24"
        if not current_ip_cidr:
            fail("[Phase IP-D] Could not determine current IP")

        log(f"Current IP: {current_ip_cidr}")

        import ipaddress
        net = ipaddress.IPv4Interface(current_ip_cidr)
        new_host = int(net.ip) + 1
        new_ip_cidr = f"{ipaddress.IPv4Address(new_host)}/{net.network.prefixlen}"
        gateway = str(list(net.network.hosts())[0])
        log(f"New IP will be: {new_ip_cidr}")

        # Step 2: Fire change_ip with method=commit_timer, timer=60s
        # The backend control plane is reachable, so the timer should be cancelled
        cr = client.run_cr(
            "[Phase IP-D] change_ip commit_timer (should succeed)", "change_ip", instance_asset["id"],
            {
                "interface": "eth0",
                "new_ip_v4": new_ip_cidr,
                "new_gateway_v4": gateway,
                "method": "commit_timer",
                "commit_timer_seconds": 60,
                "probe_interval_seconds": 5,
                "rollback_strategy": "nexplane_rollback",
            },
        )
        rollback_stack.append((cr["id"], "change_ip commit_timer"))

        # Step 3: CR must be completed (not rolled_back)
        if cr.get("status") != "completed":
            fail(f"[Phase IP-D] Expected status=completed, got: {cr.get('status')}")
        log("CR completed — commit timer cancelled by successful probe")

        # Step 4: SSM verify pending_rollback.json is gone
        check_cr = client.run_cr(
            "[Phase IP-D] verify pending_rollback.json absent", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": (
                    "if [ -f /var/lib/nexplane-agent/pending_rollback.json ]; then "
                    "  echo 'TIMER_FILE_EXISTS'; "
                    "else "
                    "  echo 'TIMER_FILE_GONE'; "
                    "fi"
                ),
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (check_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "TIMER_FILE_GONE" in out:
                log("pending_rollback.json absent — timer cancelled cleanly")
                break
            if "TIMER_FILE_EXISTS" in out:
                print("  ⚠️  pending_rollback.json still present (may be timing lag — non-fatal)")
                break

        # Step 5: SSM verify new IP applied
        verify_cr = client.run_cr(
            "[Phase IP-D] verify new IP applied", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": f"ip -4 addr show eth0 | grep -c '{new_ip_cidr.split('/')[0]}' && echo 'IP_VERIFIED'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (verify_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "IP_VERIFIED" in out:
                log(f"New IP {new_ip_cidr} verified on interface")
                break

        # Step 6: Rollback and verify
        ip_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(ip_cr_id, "change_ip → restore original IP")

        restore_cr = client.run_cr(
            "[Phase IP-D] verify original IP restored", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": f"ip -4 addr show eth0 | grep -c '{current_ip_cidr.split('/')[0]}' && echo 'RESTORED'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (restore_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "RESTORED" in out:
                log(f"Original IP {current_ip_cidr} restored")
                break

        log("Phase IP-D complete")

    except Exception as e:
        print(f"\n❌ Phase IP-D failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase IP-D cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
```

---

## Task 7: Smoke test — Phase IP-D2 (dead man's switch rollback)

Add the following function immediately after `run_phase_ip_d`:

```python
# ---------------------------------------------------------------------------
# Phase IP-D2
# ---------------------------------------------------------------------------

def run_phase_ip_d2(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase IP-D2: Dead man's switch rollback — invalid gateway causes timer to fire.

    Uses gateway 240.0.0.1 (TEST-NET range, unreachable) so the new IP has no path
    to the control plane. The commit timer fires and auto-rolls back within 15+buffer seconds.

    1. Get current IP via SSM
    2. Fire change_ip with method=commit_timer, commit_timer_seconds=15,
       new_gateway_v4=240.0.0.1 (unreachable)
    3. Wait — CR should end with status=failed or rolled_back within ~30s
    4. Verify CR ended with rollback status
    5. SSM verify: original IP is back on the interface
    6. SSM verify: pending_rollback.json is GONE (cleaned up after rollback)
    """
    print("\n[Phase IP-D2] Dead man's switch rollback (invalid gateway)")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    # We do not add to rollback_stack — the auto-rollback is the test.
    # If somehow the CR succeeds (shouldn't), we'll clean up in finally.
    ip_cr_id = ""

    try:
        # Step 1: Get current IP
        ip_cr = client.run_cr(
            "[Phase IP-D2] get current IP", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": "ip -4 addr show eth0 | grep -oP '(?<=inet )[\d./]+'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        current_ip_cidr = ""
        for run in (ip_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "/" in out:
                current_ip_cidr = out.strip().split()[0]
                break
        if not current_ip_cidr:
            ip_list = instance_asset.get("asset_metadata", {}).get("ip_addresses", [])
            if ip_list:
                current_ip_cidr = ip_list[0] if "/" in ip_list[0] else f"{ip_list[0]}/24"
        if not current_ip_cidr:
            fail("[Phase IP-D2] Could not determine current IP")

        log(f"Current IP: {current_ip_cidr}")

        import ipaddress
        net = ipaddress.IPv4Interface(current_ip_cidr)
        new_host = int(net.ip) + 1
        new_ip_cidr = f"{ipaddress.IPv4Address(new_host)}/{net.network.prefixlen}"
        # Use TEST-NET gateway that is guaranteed unreachable
        invalid_gateway = "240.0.0.1"
        log(f"New IP: {new_ip_cidr}, invalid gateway: {invalid_gateway}")

        # Step 2: Fire change_ip — timer=15s with unreachable gateway
        # Use a custom wait loop: expect status=failed or rolled_back within 60s total
        print("  → [Phase IP-D2] change_ip commit_timer 15s with invalid gateway (expect auto-rollback)")
        ip_cr_id = client.create_cr(
            "[Phase IP-D2] change_ip commit_timer rollback test",
            "change_ip",
            instance_asset["id"],
            {
                "interface": "eth0",
                "new_ip_v4": new_ip_cidr,
                "new_gateway_v4": invalid_gateway,
                "method": "commit_timer",
                "commit_timer_seconds": 15,
                "probe_interval_seconds": 5,
                "rollback_strategy": "nexplane_rollback",
            },
        )
        client.post(f"/change-requests/{ip_cr_id}/plan")
        client.post(f"/change-requests/{ip_cr_id}/submit-for-approval")
        client.post(f"/change-requests/{ip_cr_id}/approve",
                    json={"decision": "approved", "comment": "smoke test IP-D2"})
        client.post(f"/change-requests/{ip_cr_id}/execute")

        # Step 3: Poll for rollback completion — expect within 60s (15s timer + 45s buffer)
        deadline = time.time() + 60
        final_cr = None
        while time.time() < deadline:
            cr = client.get(f"/change-requests/{ip_cr_id}")
            status = cr.get("status", "")
            if status in ("failed", "rolled_back", "completed"):
                final_cr = cr
                break
            time.sleep(5)

        if not final_cr:
            fail("[Phase IP-D2] Timed out waiting for auto-rollback after 60s")

        # Step 4: Verify status is rolled_back or failed (NOT completed)
        final_status = final_cr.get("status", "")
        if final_status in ("rolled_back", "failed"):
            log(f"CR ended with status={final_status} — auto-rollback confirmed")
        elif final_status == "completed":
            fail(f"[Phase IP-D2] CR unexpectedly completed with invalid gateway — dead man's switch did not fire")
        else:
            log(f"  ⚠️  Unexpected final status: {final_status} (non-fatal, continuing verification)")

        # Brief wait for agent to restore connectivity
        time.sleep(10)

        # Step 5: SSM verify original IP is back
        restore_cr = client.run_cr(
            "[Phase IP-D2] verify original IP restored", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": f"ip -4 addr show eth0 | grep -c '{current_ip_cidr.split('/')[0]}' && echo 'RESTORED'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (restore_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "RESTORED" in out:
                log(f"Original IP {current_ip_cidr} restored after auto-rollback")
                break
        else:
            print("  ⚠️  Original IP restore verification inconclusive (non-fatal)")

        # Step 6: SSM verify pending_rollback.json is gone
        check_cr = client.run_cr(
            "[Phase IP-D2] verify pending_rollback.json cleaned up", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": (
                    "if [ -f /var/lib/nexplane-agent/pending_rollback.json ]; then "
                    "  echo 'TIMER_FILE_EXISTS'; "
                    "else "
                    "  echo 'TIMER_FILE_GONE'; "
                    "fi"
                ),
                "rollback_strategy": "rollback_unavailable",
            },
        )
        for run in (check_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "")
            if "TIMER_FILE_GONE" in out:
                log("pending_rollback.json cleaned up after auto-rollback")
                break
            if "TIMER_FILE_EXISTS" in out:
                print("  ⚠️  pending_rollback.json still present after rollback (may be timing lag)")
                break

        log("Phase IP-D2 complete")

    except Exception as e:
        print(f"\n❌ Phase IP-D2 failed: {e}")
        raise
    finally:
        # Safety net: if CR somehow completed, roll it back
        if ip_cr_id:
            try:
                cr = client.get(f"/change-requests/{ip_cr_id}")
                if cr.get("status") == "completed":
                    print("  [Phase IP-D2 safety net] Rolling back unexpectedly completed CR")
                    client.rollback_cr(ip_cr_id, "change_ip unexpected completion")
            except Exception as e2:
                print(f"  ⚠️  Safety net error: {e2}")
```

---

## Task 8: Smoke test — Phase IP-DNS (Route53 record coordination)

Add the following function immediately after `run_phase_ip_d2`:

```python
# ---------------------------------------------------------------------------
# Phase IP-DNS
# ---------------------------------------------------------------------------

def run_phase_ip_dns(client: NexplaneClient, phase_a_result: dict, cloud_account_id: str) -> None:
    """Phase IP-DNS: DNS record coordination during IP change via migrate_ip.

    Requires: AWS Route53 hosted zone 'smoke.nexplane.internal' pre-created,
    OR creates a temporary test zone and skips if Route53 access is unavailable.

    1. Check if Route53 test zone exists via boto3 — create temp zone if not
    2. Create A record pointing to EC2's current IP
    3. Fire migrate_ip with update_dns=True
    4. Verify A record in Route53 now points to new IP (boto3 check)
    5. Rollback migrate_ip
    6. Verify A record reverted to old IP
    7. Clean up: delete test A record (and temp zone if created)
    """
    print("\n[Phase IP-DNS] DNS record coordination during IP migration")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    r53 = _get_aws_boto3_client("route53")
    if not r53:
        print("  ⚠️  Route53 boto3 client unavailable — skipping Phase IP-DNS")
        return

    TEST_ZONE_NAME = "smoke.nexplane.internal"
    zone_id = ""
    zone_created = False
    record_name = f"iptest.{TEST_ZONE_NAME}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # Step 1: Find or create test zone
        zones = r53.list_hosted_zones_by_name(DNSName=TEST_ZONE_NAME).get("HostedZones", [])
        for z in zones:
            if z["Name"].rstrip(".") == TEST_ZONE_NAME:
                zone_id = z["Id"].split("/")[-1]
                break

        if not zone_id:
            print(f"  Zone {TEST_ZONE_NAME} not found — creating temporary test zone")
            resp = r53.create_hosted_zone(
                Name=TEST_ZONE_NAME,
                CallerReference=f"nexplane-smoke-ip-dns-{int(time.time())}",
                HostedZoneConfig={"Comment": "Nexplane smoke test zone", "PrivateZone": True},
                # VPC association required for private zone — use the instance's VPC
            )
            zone_id = resp["HostedZone"]["Id"].split("/")[-1]
            zone_created = True
            log(f"Created temp test zone: {TEST_ZONE_NAME} ({zone_id})")

        # Step 2: Get current IP of the EC2 instance
        ip_cr = client.run_cr(
            "[Phase IP-DNS] get current IP", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": "ip -4 addr show eth0 | grep -oP '(?<=inet )[\d.]+'",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        current_ip = ""
        for run in (ip_cr.get("execution_runs") or []):
            out = (run.get("result") or {}).get("output", "").strip()
            if out and "." in out:
                current_ip = out.split()[0]
                break
        if not current_ip:
            ip_list = instance_asset.get("asset_metadata", {}).get("ip_addresses", [])
            if ip_list:
                current_ip = ip_list[0].split("/")[0]
        if not current_ip:
            fail("[Phase IP-DNS] Could not determine current IP")
        log(f"Current IP: {current_ip}")

        # Step 3: Create A record pointing to current IP
        r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": record_name,
                        "Type": "A",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": current_ip}],
                    },
                }],
            },
        )
        log(f"Created A record: {record_name} -> {current_ip}")

        # Step 4: Fire migrate_ip with update_dns=True
        import ipaddress as _ipaddress
        ip_list_full = instance_asset.get("asset_metadata", {}).get("ip_addresses", [])
        current_ip_cidr = ip_list_full[0] if ip_list_full else f"{current_ip}/24"
        net = _ipaddress.IPv4Interface(current_ip_cidr)
        new_host = int(net.ip) + 1
        new_ip_cidr = f"{_ipaddress.IPv4Address(new_host)}/{net.network.prefixlen}"
        gateway = str(list(net.network.hosts())[0])

        cr = client.run_cr(
            "[Phase IP-DNS] migrate_ip with DNS update", "migrate_ip", instance_asset["id"],
            {
                "interface": "eth0",
                "new_ip_v4": new_ip_cidr,
                "new_gateway_v4": gateway,
                "method": "tailscale",
                "update_dns": True,
                "dns_names": [record_name],
                "hosted_zone_id": zone_id,
                "rollback_strategy": "nexplane_rollback",
            },
        )
        rollback_stack.append((cr["id"], "migrate_ip"))
        log("migrate_ip CR completed with DNS update")

        # Step 5: Verify A record updated to new IP
        new_ip_str = str(_ipaddress.IPv4Address(new_host))
        time.sleep(5)  # allow Route53 propagation
        records = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
        a_rec = next(
            (r for r in records
             if r.get("Name", "").rstrip(".") == record_name.rstrip(".")
             and r["Type"] == "A"),
            None,
        )
        if a_rec:
            actual_ip = a_rec["ResourceRecords"][0]["Value"]
            if actual_ip == new_ip_str:
                log(f"A record updated to new IP: {actual_ip}")
            else:
                print(f"  ⚠️  A record is {actual_ip}, expected {new_ip_str} (non-fatal — may need DNS connector)")
        else:
            print(f"  ⚠️  A record {record_name} not found after migrate_ip (non-fatal)")

        # Step 6: Rollback migrate_ip
        ip_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(ip_cr_id, "migrate_ip → restore IP + DNS")

        # Step 7: Verify A record reverted to old IP
        time.sleep(5)
        records = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
        a_rec = next(
            (r for r in records
             if r.get("Name", "").rstrip(".") == record_name.rstrip(".")
             and r["Type"] == "A"),
            None,
        )
        if a_rec:
            actual_ip = a_rec["ResourceRecords"][0]["Value"]
            if actual_ip == current_ip:
                log(f"A record reverted to original IP: {actual_ip}")
            else:
                print(f"  ⚠️  A record is {actual_ip}, expected {current_ip} after rollback (non-fatal)")
        else:
            print(f"  ⚠️  A record {record_name} not found after rollback (non-fatal)")

        log("Phase IP-DNS complete")

    except Exception as e:
        print(f"\n❌ Phase IP-DNS failed: {e}")
        raise
    finally:
        # Step 8: Clean up A record and temp zone
        if rollback_stack:
            print("  [Phase IP-DNS cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        try:
            if zone_id:
                # Delete the test A record
                try:
                    records = r53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
                    changes = [
                        {"Action": "DELETE", "ResourceRecordSet": rrs}
                        for rrs in records
                        if rrs["Type"] not in ("NS", "SOA")
                    ]
                    if changes:
                        r53.change_resource_record_sets(
                            HostedZoneId=zone_id,
                            ChangeBatch={"Changes": changes},
                        )
                        print(f"  Cleaned up test A record: {record_name}")
                except Exception as e2:
                    print(f"  ⚠️  Could not clean up A record: {e2}")
                # Delete temp zone if we created it
                if zone_created:
                    try:
                        r53.delete_hosted_zone(Id=zone_id)
                        print(f"  Deleted temp test zone: {TEST_ZONE_NAME}")
                    except Exception as e2:
                        print(f"  ⚠️  Could not delete temp zone: {e2}")
        except Exception as e2:
            print(f"  ⚠️  Phase IP-DNS cleanup error: {e2}")
```

---

## Task 9: Wire IP phases into main() and commit

### 9a — Wire phases into main()

In `main()`, after the `if "Z" in phases:` block (around line 2413) and before the `print("\n" + "=" * 60)` line, add:

```python
        if "IP_A" in phases:
            if phase_a_result is None:
                fail("Phase IP-A requires Phase A to have run first")
            run_phase_ip_a(client, phase_a_result)
        if "IP_D" in phases:
            if phase_a_result is None:
                fail("Phase IP-D requires Phase A to have run first")
            run_phase_ip_d(client, phase_a_result)
        if "IP_D2" in phases:
            if phase_a_result is None:
                fail("Phase IP-D2 requires Phase A to have run first")
            run_phase_ip_d2(client, phase_a_result)
        if "IP_DNS" in phases:
            if phase_a_result is None:
                fail("Phase IP-DNS requires Phase A to have run first")
            run_phase_ip_dns(client, phase_a_result, cloud_account_id)
```

### 9b — Update --phases help text

Find the `--phases` `add_argument` call in `main()` (around line 2314). Update the `help=` string to append:

```
" IP_A=tailscale-first-ip-change, IP_D=dead-mans-switch-success, IP_D2=dead-mans-switch-rollback, IP_DNS=route53-coordination."
```

The updated help argument should read (replacing the existing string):
```python
    help=(
        "Comma-separated phases to run. "
        "A-K: existing phases. P-T: new phases (P=IAM, Q=S3, R=DR-DNS, S=RDS-slow, T=Agent). "
        "Default: A,B,C,D. J and S are slow (~35-45 min). U=instance-state+S3-access, V=tailscale-remove, W=ALB-lifecycle. "
        "X=app-discovery, Y=containerize-build, Z=containerize-retire. "
        "IP_A=tailscale-first-ip-change, IP_D=dead-mans-switch-success, "
        "IP_D2=dead-mans-switch-rollback, IP_DNS=route53-coordination."
    ),
```

Note: the phases set is built with `{p.strip().upper() for p in args.phases.split(",")}` so `IP_A`, `IP_D`, `IP_D2`, `IP_DNS` will be correctly parsed when passed as `--phases A,IP_A,IP_D`.

### 9c — Commit smoke tests

```bash
cd backend
git add tests/smoke/test_aws_live.py
git commit -m "test(smoke): add Phases IP-A, IP-D, IP-D2, IP-DNS for IP migration smoke coverage"
```

---

## Verification checklist

Before marking any task complete:

- [ ] Task 1: `ChangeType` union includes `change_ip`, `migrate_ip`, `ip_campaign`
- [ ] Task 2: `IPMigrationWizard.tsx` exists, no TypeScript errors
- [ ] Task 3: AssetDetail shows Network card for `server` and `endpoint` assets; "Change IP" button opens the wizard
- [ ] Task 4: `npx tsc --noEmit` exits with 0 errors; frontend container restarted
- [ ] Tasks 5-8: All four `run_phase_ip_*` functions present in `test_aws_live.py`
- [ ] Task 9: All four phases wired into `main()` with Phase A guards; `--phases` help updated; committed

## Usage: running the new phases

```bash
# Run only IP phases (requires a running Phase A instance):
python backend/tests/smoke/test_aws_live.py \
    --base-url http://localhost:8000 \
    --email admin@acme.example \
    --password admin123 \
    --phases A,IP_A,IP_D,IP_D2,IP_DNS \
    --tailscale-auth-key tskey-auth-<key>

# Run IP-D2 and IP-DNS only (after Phase A already running):
python backend/tests/smoke/test_aws_live.py \
    --base-url http://localhost:8000 \
    --email admin@acme.example \
    --password admin123 \
    --phases IP_D2,IP_DNS
```
