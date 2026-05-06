# Smoke Test Runner UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Smoke Tests" page to the Nexplane UI that lets operators trigger, monitor, and review smoke test runs without dropping to a terminal.

**Architecture:** New FastAPI router at `/smoke-tests` with three endpoints (suites, run, logs). Results stored as flat JSON + log files in `/tmp/nexplane-smoke-runs/`. Frontend `SmokeTests.tsx` page polls the log endpoint every 2s during active runs, parses phase pass/fail from log output, and renders suite cards + live log panel. No database needed.

**Tech Stack:** Python 3.12, FastAPI, subprocess, React, TanStack Query, Tailwind CSS

---

## Files

**Create:**
- `backend/app/routers/smoke_tests.py`
- `frontend/src/pages/SmokeTests.tsx`
- `frontend/src/api/smokeTestsApi.ts`

**Modify:**
- `backend/app/main.py` — register smoke_tests router
- `frontend/src/routes/index.tsx` — add /smoke-tests route
- `frontend/src/components/Sidebar.tsx` — add nav link

---

### Task 1: Create the backend smoke_tests router

**Files:**
- Create: `backend/app/routers/smoke_tests.py`

- [ ] **Step 1: Create the router**

```python
"""
Smoke test runner API.

Provides endpoints to list available suites, trigger test runs,
and stream log output. Results are stored as flat files:
  /tmp/nexplane-smoke-runs/<run_id>.log   — full stdout/stderr
  /tmp/nexplane-smoke-runs/last-<suite>.json — last run result per suite
  /tmp/nexplane-smoke-runs/<run_id>.pid  — PID of running process (if active)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.routers import current_user
from app.models.user import User

router = APIRouter(prefix="/smoke-tests", tags=["Smoke Tests"])

RUNS_DIR = Path("/tmp/nexplane-smoke-runs")
SMOKE_DIR = Path("/app/tests/smoke")

SUITES = [
    {
        "id": "aws",
        "name": "AWS",
        "file": "test_aws_live.py",
        "default_phases": "A,B,C,D,E,F,G,H,I,K,P,Q,R,T",
        "slow_phases": "J,S",
        "description": "EC2, IAM, S3, Route53, CloudWatch, RDS, agent lifecycle",
    },
    {
        "id": "gcp",
        "name": "GCP",
        "file": "test_gcp_live.py",
        "default_phases": "L,M,N,O,P,Q,R",
        "slow_phases": "",
        "description": "GCE, firewall, storage, service accounts, Terraform, Ansible",
    },
    {
        "id": "azure",
        "name": "Azure",
        "file": "test_azure_live.py",
        "default_phases": "N,O,P,Q,R,S,T",
        "slow_phases": "",
        "description": "Azure VM, NSG, blob storage, Terraform, Ansible",
    },
    {
        "id": "agent",
        "name": "Agent",
        "file": "test_agent_live.py",
        "default_phases": "linux_patch,ossecurity,linuxauth,crossplatform,compliance,forensics,fleet,backup,reboot,credrotation,iac,linuxupgrade",
        "slow_phases": "",
        "description": "All 47 agent commands × 3 clouds (Linux + Windows)",
    },
    {
        "id": "parallel",
        "name": "Parallel",
        "file": "test_parallel_live.py",
        "default_phases": "",
        "slow_phases": "",
        "description": "AWS + GCP + Azure running concurrently",
    },
]


def _runs_dir() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def _last_run(suite_id: str) -> Optional[dict]:
    path = _runs_dir() / f"last-{suite_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None


def _is_running(run_id: str) -> bool:
    pid_path = _runs_dir() / f"{run_id}.pid"
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)  # check if process exists
        return True
    except (ProcessLookupError, PermissionError, ValueError):
        pid_path.unlink(missing_ok=True)
        return False


def _parse_results(log_path: Path) -> dict:
    """Parse phase pass/fail and overall status from a log file."""
    content = log_path.read_text(errors="replace") if log_path.exists() else ""
    phases_passed = []
    phases_failed = []
    for m in re.finditer(r"\[Phase ([A-Za-z0-9_-]+)\].*?complete", content):
        phases_passed.append(m.group(1))
    for m in re.finditer(r"\[Phase ([A-Za-z0-9_-]+)\].*?fail", content, re.IGNORECASE):
        phases_failed.append(m.group(1))
    # Also catch old-style phase markers
    for m in re.finditer(r"✅ Phase ([A-Z]) complete", content):
        p = m.group(1)
        if p not in phases_passed:
            phases_passed.append(p)
    for m in re.finditer(r"❌ Phase ([A-Z]) (failed|—)", content):
        p = m.group(1)
        if p not in phases_failed:
            phases_failed.append(p)
    overall_passed = "ALL SELECTED PHASES PASSED" in content or "ALL TRACKS PASSED" in content
    overall_failed = "SMOKE TEST FAILED" in content or "ONE OR MORE TRACKS FAILED" in content
    if overall_passed:
        status = "passed"
    elif overall_failed:
        status = "failed"
    else:
        status = "unknown"
    return {
        "phases_passed": phases_passed,
        "phases_failed": phases_failed,
        "status": status,
    }


def _stream_to_log(proc: subprocess.Popen, log_path: Path, run_id: str, suite_id: str) -> None:
    """Stream process output to log file, then write last-result JSON on completion."""
    with open(log_path, "w") as f:
        for line in proc.stdout:
            f.write(line)
            f.flush()
    proc.wait()
    # Clean up PID file
    pid_path = _runs_dir() / f"{run_id}.pid"
    pid_path.unlink(missing_ok=True)
    # Write last-run result
    result = _parse_results(log_path)
    last = {
        "run_id": run_id,
        "status": result["status"],
        "phases_passed": len(result["phases_passed"]),
        "phases_failed": len(result["phases_failed"]),
        "phases_passed_list": result["phases_passed"],
        "phases_failed_list": result["phases_failed"],
        "exit_code": proc.returncode,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (_runs_dir() / f"last-{suite_id}.json").write_text(json.dumps(last))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/suites")
async def list_suites(current_user: User = Depends(current_user)):
    """Return available suites with their last run result."""
    result = []
    for suite in SUITES:
        last = _last_run(suite["id"])
        # Check if currently running
        running_run_id = None
        for pid_file in _runs_dir().glob(f"{suite['id']}-*.pid"):
            run_id = pid_file.stem
            if _is_running(run_id):
                running_run_id = run_id
                break
        result.append({
            **suite,
            "last_run": last,
            "running_run_id": running_run_id,
        })
    return result


@router.post("/run")
async def trigger_run(
    body: dict,
    current_user: User = Depends(current_user),
):
    """
    Trigger a smoke test suite run.

    Body:
        suite: str — suite ID (aws, gcp, azure, agent, parallel)
        phases: str — comma-separated phases (optional, uses suite default)
        tailscale_auth_key: str — optional Tailscale auth key
        gcp_project: str — optional GCP project
        azure_resource_group: str — optional Azure resource group
    """
    suite_id = body.get("suite")
    if not suite_id:
        raise HTTPException(status_code=400, detail="suite is required")

    suite = next((s for s in SUITES if s["id"] == suite_id), None)
    if not suite:
        raise HTTPException(status_code=404, detail=f"Suite '{suite_id}' not found")

    script = SMOKE_DIR / suite["file"]
    if not script.exists():
        raise HTTPException(status_code=503, detail=f"Smoke test file not found: {script}")

    # Check if already running
    for pid_file in _runs_dir().glob(f"{suite_id}-*.pid"):
        if _is_running(pid_file.stem):
            raise HTTPException(status_code=409, detail=f"Suite '{suite_id}' is already running")

    run_id = f"{suite_id}-{int(time.time())}"
    log_path = _runs_dir() / f"{run_id}.log"

    # Build command
    phases = body.get("phases") or suite["default_phases"]
    base_url = "http://localhost:8000"
    cmd = [
        sys.executable, str(script),
        "--base-url", base_url,
        "--email", body.get("email", "admin@acme.example"),
        "--password", body.get("password", "admin123"),
    ]
    if phases:
        cmd += ["--phases", phases]
    if body.get("tailscale_auth_key"):
        cmd += ["--tailscale-auth-key", body["tailscale_auth_key"]]
    if body.get("gcp_project"):
        cmd += ["--gcp-project", body["gcp_project"]]
    if body.get("azure_resource_group"):
        cmd += ["--azure-resource-group", body["azure_resource_group"]]

    # Spawn subprocess
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(SMOKE_DIR),
    )

    # Write PID file
    pid_path = _runs_dir() / f"{run_id}.pid"
    pid_path.write_text(str(proc.pid))

    # Stream output in background thread
    thread = threading.Thread(
        target=_stream_to_log,
        args=(proc, log_path, run_id, suite_id),
        daemon=True,
    )
    thread.start()

    return {"run_id": run_id, "pid": proc.pid, "log_path": str(log_path)}


@router.get("/logs/{run_id}")
async def get_logs(
    run_id: str,
    offset: int = 0,
    current_user: User = Depends(current_user),
):
    """
    Return log content from the given byte offset.
    Frontend polls this every 2s, passing the last offset to get only new content.
    """
    log_path = _runs_dir() / f"{run_id}.log"
    if not log_path.exists():
        return {"content": "", "next_offset": 0, "done": not _is_running(run_id)}

    with open(log_path, "rb") as f:
        f.seek(offset)
        chunk = f.read(65536)  # max 64KB per poll
        next_offset = f.tell()

    running = _is_running(run_id)
    return {
        "content": chunk.decode("utf-8", errors="replace"),
        "next_offset": next_offset,
        "done": not running and next_offset >= log_path.stat().st_size,
    }


@router.delete("/runs/{run_id}")
async def stop_run(run_id: str, current_user: User = Depends(current_user)):
    """Send SIGTERM to a running test process."""
    pid_path = _runs_dir() / f"{run_id}.pid"
    if not pid_path.exists():
        raise HTTPException(status_code=404, detail="Run not found or already completed")
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 15)  # SIGTERM
        return {"stopped": True, "pid": pid}
    except (ProcessLookupError, ValueError):
        raise HTTPException(status_code=404, detail="Process not found")
```

- [ ] **Step 2: Register the router in `backend/app/main.py`**

Add after the existing router imports:
```python
from app.routers import smoke_tests as smoke_tests_router
```

Add after the existing `app.include_router(...)` calls:
```python
app.include_router(smoke_tests_router.router)
```

- [ ] **Step 3: Verify the router registers**

```bash
docker exec nexplane-backend-1 python3 -c "
import httpx
tok = httpx.post('http://localhost:8000/auth/login',json={'email':'admin@acme.example','password':'admin123'}).json()['access_token']
r = httpx.get('http://localhost:8000/smoke-tests/suites', headers={'Authorization': 'Bearer '+tok})
print(r.status_code, len(r.json()), 'suites')
"
```

Expected: `200 5 suites`

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/smoke_tests.py backend/app/main.py
git commit -m "feat(smoke-ui): add /smoke-tests backend router with suites, run, logs, stop endpoints"
```

---

### Task 2: Create `frontend/src/api/smokeTestsApi.ts`

**Files:**
- Create: `frontend/src/api/smokeTestsApi.ts`

- [ ] **Step 1: Create the API module**

```typescript
import { apiClient } from "./client";

export interface SmokeTestLastRun {
  run_id: string;
  status: "passed" | "failed" | "unknown";
  phases_passed: number;
  phases_failed: number;
  phases_passed_list: string[];
  phases_failed_list: string[];
  exit_code: number;
  finished_at: string;
}

export interface SmokeTestSuite {
  id: string;
  name: string;
  file: string;
  default_phases: string;
  slow_phases: string;
  description: string;
  last_run: SmokeTestLastRun | null;
  running_run_id: string | null;
}

export interface RunResult {
  run_id: string;
  pid: number;
  log_path: string;
}

export interface LogChunk {
  content: string;
  next_offset: number;
  done: boolean;
}

export interface RunConfig {
  suite: string;
  phases?: string;
  tailscale_auth_key?: string;
  gcp_project?: string;
  azure_resource_group?: string;
  email?: string;
  password?: string;
}

export const smokeTestsApi = {
  getSuites: () =>
    apiClient.get<SmokeTestSuite[]>("/smoke-tests/suites").then((r) => r.data),

  triggerRun: (config: RunConfig) =>
    apiClient.post<RunResult>("/smoke-tests/run", config).then((r) => r.data),

  getLogs: (runId: string, offset: number = 0) =>
    apiClient
      .get<LogChunk>(`/smoke-tests/logs/${runId}`, { params: { offset } })
      .then((r) => r.data),

  stopRun: (runId: string) =>
    apiClient.delete(`/smoke-tests/runs/${runId}`).then((r) => r.data),
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api/smokeTestsApi.ts
git commit -m "feat(smoke-ui): add smokeTestsApi.ts with typed API client for smoke test endpoints"
```

---

### Task 3: Create `frontend/src/pages/SmokeTests.tsx`

**Files:**
- Create: `frontend/src/pages/SmokeTests.tsx`

- [ ] **Step 1: Create the page**

```tsx
import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Play, Square, Download, CheckCircle2, XCircle, Clock,
  AlertCircle, Loader2, ChevronDown, ChevronUp, Terminal,
} from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { smokeTestsApi, type SmokeTestSuite, type RunConfig } from "../api/smokeTestsApi";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-slate-400 text-xs">Never run</span>;
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
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-navy-light border border-navy-border rounded-xl w-full max-w-lg p-6 space-y-4"
           onClick={(e) => e.stopPropagation()}>
        <h2 className="text-white font-semibold text-lg">Run {suite.name} Smoke Test</h2>
        <p className="text-slate-400 text-sm">{suite.description}</p>

        {suite.id !== "parallel" && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">Phases</label>
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

        {(suite.id === "aws" || suite.id === "agent" || suite.id === "parallel") && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">Tailscale Auth Key</label>
            <input
              className="w-full bg-navy border border-navy-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={tailscaleKey}
              onChange={(e) => setTailscaleKey(e.target.value)}
              placeholder="tskey-auth-..."
            />
          </div>
        )}

        {(suite.id === "gcp" || suite.id === "agent" || suite.id === "parallel") && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">GCP Project ID</label>
            <input
              className="w-full bg-navy border border-navy-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              value={gcpProject}
              onChange={(e) => setGcpProject(e.target.value)}
              placeholder="my-gcp-project"
            />
          </div>
        )}

        {(suite.id === "azure" || suite.id === "agent" || suite.id === "parallel") && (
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1">Azure Resource Group</label>
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
            {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
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

  // Simple ANSI color stripping for display
  const cleanContent = content.replace(/\x1b\[[0-9;]*m/g, "");

  return (
    <div className="mt-6 bg-navy border border-navy-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-navy-border">
        <div className="flex items-center gap-2 text-slate-300 text-sm font-medium">
          <Terminal className="w-4 h-4" />
          Live Output — {runId}
          {!done && <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" />}
          {done && <span className="text-slate-500 text-xs">(completed)</span>}
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
  const isRunning = suite.running_run_id !== null || (activeRunId !== null && activeRunId.startsWith(suite.id + "-"));
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
          {isRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
          Run
        </button>
      </div>

      <div className="flex items-center gap-4 text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-slate-500">Status:</span>
          {isRunning ? <RunningBadge /> : <StatusBadge status={last?.status ?? null} />}
        </div>
        {last && !isRunning && (
          <>
            <div className="text-slate-500">
              {last.phases_passed}/{last.phases_passed + last.phases_failed} phases
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
// Main Page
// ---------------------------------------------------------------------------

export function SmokeTests() {
  const queryClient = useQueryClient();
  const [modalSuite, setModalSuite] = useState<SmokeTestSuite | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

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

  const handleRun = useCallback((config: RunConfig) => {
    runMutation.mutate(config);
  }, [runMutation]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-blue-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto py-8 px-6 space-y-6">
      <PageHeader
        title="Smoke Tests"
        subtitle="Live end-to-end connector verification against real cloud infrastructure"
      />

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
        <LogPanel
          runId={activeRunId}
          onStop={() => stopMutation.mutate()}
        />
      )}

      {modalSuite && (
        <RunModal
          suite={modalSuite}
          onClose={() => setModalSuite(null)}
          onRun={handleRun}
          isRunning={runMutation.isPending}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
docker compose exec frontend npx tsc --noEmit 2>&1 | grep -E "error|SmokeTests" | head -20
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/SmokeTests.tsx frontend/src/api/smokeTestsApi.ts
git commit -m "feat(smoke-ui): add SmokeTests.tsx page with suite cards, run modal, live log panel"
```

---

### Task 4: Wire up routing and navigation

**Files:**
- Modify: `frontend/src/routes/index.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Add route in `routes/index.tsx`**

Add import:
```typescript
import { SmokeTests } from "../pages/SmokeTests";
```

Add route inside `<Route element={<Layout />}>`:
```tsx
<Route path="/smoke-tests" element={<SmokeTests />} />
```

- [ ] **Step 2: Add nav link in `Sidebar.tsx`**

Add import for `FlaskConical` (or `TestTube2` or `Activity`) icon:
```typescript
import { ..., FlaskConical } from "lucide-react";
```

Add nav item to the `navItems` array (after Connectors):
```typescript
  { to: "/smoke-tests", label: "Smoke Tests", icon: FlaskConical },
```

- [ ] **Step 3: Verify the app loads**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait 10s, then check that `http://localhost:5173/smoke-tests` loads without errors. Check browser console for TypeScript/runtime errors.

- [ ] **Step 4: Verify the suites load**

Navigate to `/smoke-tests` in browser. Confirm 5 suite cards appear (AWS, GCP, Azure, Agent, Parallel). Each should show last run status if a run has been done, or "Never run" otherwise.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/index.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(smoke-ui): add /smoke-tests route and Smoke Tests nav link"
```
