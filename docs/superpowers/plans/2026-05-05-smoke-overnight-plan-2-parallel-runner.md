# Smoke Test Parallel Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `test_parallel_live.py` that runs AWS, GCP, and Azure smoke test tracks as concurrent subprocesses, forwarding their output with provider prefixes and printing a consolidated pass/fail summary.

**Architecture:** Python `subprocess.Popen` per provider (true process isolation, separate boto3/GCP/Azure clients). A `threading.Thread` reader per process forwards stdout lines prefixed with `[AWS]`/`[GCP]`/`[Azure]`. Main thread waits for all three to finish, then prints a summary table.

**Tech Stack:** Python 3.12, subprocess, threading

---

## Files

**Create:**
- `backend/tests/smoke/test_parallel_live.py`

---

### Task 1: Create `test_parallel_live.py`

**Files:**
- Create: `backend/tests/smoke/test_parallel_live.py`

- [ ] **Step 1: Create the file**

```python
#!/usr/bin/env python3
"""
Nexplane Multi-Cloud Parallel Smoke Test

Runs AWS, GCP, and Azure smoke test files concurrently as separate subprocesses.
Each provider's output is prefixed and interleaved in real time.

Usage:
    python backend/tests/smoke/test_parallel_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id \\
        --azure-resource-group nexplane-smoke-rg \\
        --aws-phases A,B,C,D,E,F,G,H,I,K,P,Q,R,T \\
        --gcp-phases L,M,N,O,P,Q,R \\
        --azure-phases N,O,P,Q,R,S,T

Requirements:
    All three cloud connectors configured with live credentials.
    Tailscale connector with reusable pre-authorized auth key.
"""
import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SMOKE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Output forwarding
# ---------------------------------------------------------------------------

def _forward(stream, prefix: str, results: dict, key: str) -> None:
    """Read lines from a subprocess stdout stream and print with prefix."""
    passed = None
    for raw in stream:
        line = raw.rstrip("\n")
        print(f"{prefix} {line}", flush=True)
        if "ALL SELECTED PHASES PASSED" in line:
            passed = True
        elif "SMOKE TEST FAILED" in line or "AGENT SMOKE TEST FAILED" in line:
            passed = False
    if passed is None:
        passed = False  # process ended without explicit pass/fail line
    results[key] = passed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_provider(
    name: str,
    script: str,
    args: list[str],
    base_args: list[str],
) -> tuple[subprocess.Popen, dict, threading.Thread]:
    """Spawn a provider subprocess and start a forwarding thread."""
    prefix = {
        "aws":   "\033[32m[AWS  ]\033[0m",
        "gcp":   "\033[34m[GCP  ]\033[0m",
        "azure": "\033[35m[Azure]\033[0m",
        "agent": "\033[33m[Agent]\033[0m",
    }.get(name.lower(), f"[{name.upper()}]")

    cmd = [sys.executable, str(SMOKE_DIR / script)] + base_args + args
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    results: dict = {}
    thread = threading.Thread(
        target=_forward,
        args=(proc.stdout, prefix, results, name),
        daemon=True,
    )
    thread.start()
    return proc, results, thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Nexplane parallel multi-cloud smoke test"
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--tailscale-auth-key", default="")
    parser.add_argument("--gcp-project", default="")
    parser.add_argument("--azure-resource-group", default="")
    parser.add_argument(
        "--aws-phases", default="A,B,C,D,E,F,G,H,I,K,P,Q,R,T",
        help="Phases to run for the AWS track",
    )
    parser.add_argument(
        "--gcp-phases", default="L,M,N,O,P,Q,R",
        help="Phases to run for the GCP track",
    )
    parser.add_argument(
        "--azure-phases", default="N,O,P,Q,R,S,T",
        help="Phases to run for the Azure track",
    )
    parser.add_argument(
        "--skip-aws", action="store_true", help="Skip the AWS track"
    )
    parser.add_argument(
        "--skip-gcp", action="store_true", help="Skip the GCP track"
    )
    parser.add_argument(
        "--skip-azure", action="store_true", help="Skip the Azure track"
    )
    args = parser.parse_args()

    base_args = [
        "--base-url", args.base_url,
        "--email", args.email,
        "--password", args.password,
    ]

    providers: list[tuple[str, subprocess.Popen, dict, threading.Thread]] = []
    start_times: dict[str, float] = {}

    print("=" * 60)
    print("Nexplane Parallel Multi-Cloud Smoke Test")
    print("=" * 60)

    if not args.skip_aws:
        aws_args = ["--phases", args.aws_phases]
        if args.tailscale_auth_key:
            aws_args += ["--tailscale-auth-key", args.tailscale_auth_key]
        proc, results, thread = run_provider("aws", "test_aws_live.py", aws_args, base_args)
        providers.append(("aws", proc, results, thread))
        start_times["aws"] = time.time()
        print("[AWS  ] Starting AWS track...")

    if not args.skip_gcp:
        gcp_args = ["--phases", args.gcp_phases]
        if args.gcp_project:
            gcp_args += ["--gcp-project", args.gcp_project]
        if args.tailscale_auth_key:
            gcp_args += ["--tailscale-auth-key", args.tailscale_auth_key]
        proc, results, thread = run_provider("gcp", "test_gcp_live.py", gcp_args, base_args)
        providers.append(("gcp", proc, results, thread))
        start_times["gcp"] = time.time()
        print("[GCP  ] Starting GCP track...")

    if not args.skip_azure:
        azure_args = ["--phases", args.azure_phases]
        if args.azure_resource_group:
            azure_args += ["--azure-resource-group", args.azure_resource_group]
        if args.tailscale_auth_key:
            azure_args += ["--tailscale-auth-key", args.tailscale_auth_key]
        proc, results, thread = run_provider("azure", "test_azure_live.py", azure_args, base_args)
        providers.append(("azure", proc, results, thread))
        start_times["azure"] = time.time()
        print("[Azure] Starting Azure track...")

    if not providers:
        print("No providers selected — use --skip-* flags to exclude individual tracks.")
        sys.exit(1)

    # Wait for all processes and threads to finish
    for name, proc, results, thread in providers:
        proc.wait()
        thread.join(timeout=10)

    # Print summary
    print("\n" + "=" * 60)
    print("Parallel Smoke Test Summary")
    print("=" * 60)

    all_passed = True
    for name, proc, results, thread in providers:
        elapsed = int(time.time() - start_times[name])
        mins, secs = divmod(elapsed, 60)
        duration = f"{mins}m {secs:02d}s"
        passed = results.get(name, False)
        status = "\033[32m✅ PASSED\033[0m" if passed else "\033[31m❌ FAILED\033[0m"
        exit_code = proc.returncode
        label = name.upper().ljust(5)
        print(f"  {label}  {status}  ({duration})  exit={exit_code}")
        if not passed:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("\033[32m✅ ALL TRACKS PASSED\033[0m")
    else:
        print("\033[31m❌ ONE OR MORE TRACKS FAILED\033[0m")
    print("=" * 60)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_parallel_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Test with stub-only tracks (GCP + Azure, no credentials needed)**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_parallel_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --skip-aws \
  --gcp-phases S,T,U \
  --azure-phases U,V,W \
  --gcp-project nexplane \
  --azure-resource-group nexplane-smoke-rg 2>&1 | tail -15
```

Expected: both GCP and Azure stub phases print STUB messages, summary shows `✅ PASSED` for both.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_parallel_live.py
git commit -m "feat(smoke): add test_parallel_live.py — runs AWS/GCP/Azure tracks concurrently with per-provider output prefixing"
```
