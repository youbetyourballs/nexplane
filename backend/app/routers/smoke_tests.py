"""
Smoke test runner API.

Provides endpoints to list available suites, trigger test runs,
and stream log output. Results are stored as flat files:
  /tmp/nexplane-smoke-runs/<run_id>.log   — full stdout/stderr
  /tmp/nexplane-smoke-runs/last-<suite>.json -- last run result per suite
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.routers import current_user, require_roles
from app.models.user import User, UserRole

router = APIRouter(prefix="/smoke-tests", tags=["Smoke Tests"])

RUNS_DIR = Path("/tmp/nexplane-smoke-runs")
SMOKE_DIR = Path("/app/tests/smoke")

SUITES = [
    {
        "id": "aws",
        "name": "AWS",
        "file": "test_aws_live.py",
        "default_phases": "A,B,C,D,E,F,G,H,I,K,P,Q,R,T,U,V,W",
        "slow_phases": "J,S",
        "description": "EC2, IAM, S3, Route53, CloudWatch, RDS, ALB, agent lifecycle",
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
        "default_phases": "N,O,P,Q,R,S,T,U,V,W,X,Y,Z",
        "slow_phases": "",
        "description": "Azure VM, NSG, blob storage, managed identity, RBAC, VNet, DNS, SQL, Monitor alerts, Terraform, Ansible",
    },
    {
        "id": "agent",
        "name": "Agent",
        "file": "test_agent_live.py",
        "default_phases": "linux_patch,ossecurity,linuxauth,crossplatform,compliance,forensics,fleet,backup,reboot,credrotation,iac,linuxupgrade",
        "slow_phases": "",
        "description": "All agent commands x 3 clouds (Linux + Windows)",
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
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError):
        pid_path.unlink(missing_ok=True)
        return False


def _parse_results(log_path: Path) -> dict:
    content = log_path.read_text(errors="replace") if log_path.exists() else ""
    phases_passed = []
    phases_failed = []
    for m in re.finditer(r"\[Phase ([A-Za-z0-9_-]+)\].*?complete", content):
        phases_passed.append(m.group(1))
    for m in re.finditer(r"\[Phase ([A-Za-z0-9_-]+)\].*?fail", content, re.IGNORECASE):
        phases_failed.append(m.group(1))
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
    with open(log_path, "w") as f:
        for line in proc.stdout:
            f.write(line)
            f.flush()
    proc.wait()
    pid_path = _runs_dir() / f"{run_id}.pid"
    pid_path.unlink(missing_ok=True)
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


@router.get("/suites")
async def list_suites(current_user: User = Depends(current_user)):
    result = []
    for suite in SUITES:
        last = _last_run(suite["id"])
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
    suite_id = body.get("suite")
    if not suite_id:
        raise HTTPException(status_code=400, detail="suite is required")

    suite = next((s for s in SUITES if s["id"] == suite_id), None)
    if not suite:
        raise HTTPException(status_code=404, detail=f"Suite '{suite_id}' not found")

    script = SMOKE_DIR / suite["file"]
    if not script.exists():
        raise HTTPException(status_code=503, detail=f"Smoke test file not found: {script}")

    for pid_file in _runs_dir().glob(f"{suite_id}-*.pid"):
        if _is_running(pid_file.stem):
            raise HTTPException(status_code=409, detail=f"Suite '{suite_id}' is already running")

    run_id = f"{suite_id}-{int(time.time())}"
    log_path = _runs_dir() / f"{run_id}.log"

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

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(SMOKE_DIR),
    )

    pid_path = _runs_dir() / f"{run_id}.pid"
    pid_path.write_text(str(proc.pid))

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
    log_path = _runs_dir() / f"{run_id}.log"
    if not log_path.exists():
        return {"content": "", "next_offset": 0, "done": not _is_running(run_id)}

    with open(log_path, "rb") as f:
        f.seek(offset)
        chunk = f.read(65536)
        next_offset = f.tell()

    running = _is_running(run_id)
    return {
        "content": chunk.decode("utf-8", errors="replace"),
        "next_offset": next_offset,
        "done": not running and next_offset >= log_path.stat().st_size,
    }


@router.delete("/runs/{run_id}")
async def stop_run(run_id: str, current_user: User = Depends(current_user)):
    pid_path = _runs_dir() / f"{run_id}.pid"
    if not pid_path.exists():
        raise HTTPException(status_code=404, detail="Run not found or already completed")
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 15)
        return {"stopped": True, "pid": pid}
    except (ProcessLookupError, ValueError):
        raise HTTPException(status_code=404, detail="Process not found")


@router.get("/cleanup-preview")
async def cleanup_preview(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all org assets whose name contains 'nexplane-smoke' (no deletions)."""
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.name.ilike("%nexplane-smoke%"),
        )
    )
    assets = result.scalars().all()
    return {
        "count": len(assets),
        "assets": [
            {
                "id": str(a.id),
                "name": a.name,
                "asset_type": a.asset_type.value,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in assets
        ],
    }


@router.delete("/cleanup-inventory")
async def cleanup_inventory(
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-delete all org assets whose name contains 'nexplane-smoke'."""
    from sqlalchemy import delete as sa_delete
    result = await db.execute(
        sa_delete(Asset)
        .where(
            Asset.organization_id == user.organization_id,
            Asset.name.ilike("%nexplane-smoke%"),
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return {"deleted": result.rowcount}
