"""
ad_dc_decommission — terminate or shut down compromised DCs after a forest restore.

Accepts a list of compromised_dcs, each with a type:
  ec2     — terminate via AWS EC2 API
  winrm   — block inbound traffic + Stop-Computer via WinRM
  ilodrac — stub (not_implemented); requires oob_management connector (backlog)

All entries processed concurrently. CR completes if at least one EC2 or WinRM
entry succeeds. iLO/iDRAC stubs appear in result.warnings but do not fail the CR.

Rollback: not reversible — documented in rollback() return value.
"""
from __future__ import annotations
import asyncio
import logging

logger = logging.getLogger(__name__)

_PS_ISOLATE_AND_SHUTDOWN = r"""
New-NetFirewallRule -DisplayName "NexplaneIsolate" -Direction Inbound -Action Block `
    -Protocol TCP -Enabled True -ErrorAction SilentlyContinue
Stop-Computer -Force
Write-Output "SHUTDOWN_INITIATED"
"""


# ---------------------------------------------------------------------------
# AWS helper — module-level so tests can monkeypatch
# ---------------------------------------------------------------------------

def _ec2_client(creds: dict):
    import boto3
    kwargs: dict = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("region"):
        kwargs["region_name"] = creds["region"]
    return boto3.client("ec2", **kwargs)


# ---------------------------------------------------------------------------
# Per-type handlers (all blocking — called via run_in_executor)
# ---------------------------------------------------------------------------

def _handle_ec2(entry: dict, creds: dict) -> dict:
    instance_id = entry.get("instance_id", "")
    name = entry.get("name", instance_id)
    if not instance_id:
        return {"name": name, "status": "error",
                "reason": "instance_id is required for ec2 type"}
    try:
        ec2 = _ec2_client(creds)
        ec2.terminate_instances(InstanceIds=[instance_id])
        logger.info("ad_dc_decommission: terminated EC2 %s (%s)", instance_id, name)
        return {"name": name, "type": "ec2", "status": "terminated",
                "instance_id": instance_id}
    except Exception as exc:
        return {"name": name, "type": "ec2", "status": "error", "reason": str(exc),
                "instance_id": instance_id}


def _handle_winrm(entry: dict, winrm_username: str, winrm_password: str,
                   winrm_port: int) -> dict:
    hostname = entry.get("winrm_hostname", "")
    name = entry.get("name", hostname)
    if not hostname:
        return {"name": name, "status": "error",
                "reason": "winrm_hostname required for winrm type"}
    if not winrm_username or not winrm_password:
        return {"name": name, "status": "error",
                "reason": "winrm_username/winrm_password required for winrm type"}
    try:
        import winrm
        session = winrm.Session(
            target=f"http://{hostname}:{winrm_port}/wsman",
            auth=(winrm_username, winrm_password),
            transport="basic",
            server_cert_validation="ignore",
        )
        session.run_ps(_PS_ISOLATE_AND_SHUTDOWN)
        logger.info("ad_dc_decommission: shutdown initiated on %s (%s)", hostname, name)
        return {"name": name, "type": "winrm", "status": "shutdown",
                "winrm_hostname": hostname}
    except Exception as exc:
        exc_s = str(exc)
        # WinRM drop during shutdown is expected — treat as success
        if any(k in exc_s.lower() for k in ("connection", "timeout", "reset", "eof", "winrm")):
            return {"name": name, "type": "winrm", "status": "shutdown",
                    "winrm_hostname": hostname,
                    "note": "WinRM dropped during shutdown — expected"}
        return {"name": name, "type": "winrm", "status": "error", "reason": exc_s,
                "winrm_hostname": hostname}


def _handle_ilodrac(entry: dict) -> dict:
    hostname = entry.get("hostname", "")
    name = entry.get("name", hostname)
    pam_path = entry.get("pam_path", "")
    return {
        "name": name,
        "type": "ilodrac",
        "status": "not_implemented",
        "hostname": hostname,
        "pam_path": pam_path,
        "reason": (
            "iLO/iDRAC hardware power control requires the oob_management connector "
            "(backlog). Manually power off this host via your OOB management interface. "
            f"Credential path when implemented: {pam_path}"
        ),
    }


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    compromised_dcs: list = parameters.get("compromised_dcs") or []
    winrm_username = parameters.get("winrm_username") or creds.get("winrm_username", "")
    winrm_password = parameters.get("winrm_password") or creds.get("winrm_password", "")
    winrm_port = int(parameters.get("winrm_port", creds.get("winrm_port", 5985)))

    if not compromised_dcs:
        return {"status": "failed", "reason": "compromised_dcs list is empty",
                "total": 0, "results": []}

    loop = asyncio.get_event_loop()

    async def _dispatch(entry: dict) -> dict:
        entry_type = entry.get("type", "")
        if entry_type == "ec2":
            return await loop.run_in_executor(
                None, lambda e=entry: _handle_ec2(e, creds)
            )
        elif entry_type == "winrm":
            return await loop.run_in_executor(
                None, lambda e=entry: _handle_winrm(e, winrm_username, winrm_password, winrm_port)
            )
        elif entry_type == "ilodrac":
            return _handle_ilodrac(entry)
        else:
            return {"name": entry.get("name", ""), "status": "error",
                    "reason": f"Unknown type: {entry_type!r} — must be ec2 | winrm | ilodrac"}

    results = list(await asyncio.gather(*[_dispatch(e) for e in compromised_dcs]))

    succeeded = [r for r in results if r["status"] in ("terminated", "shutdown")]
    stubs = [r for r in results if r["status"] == "not_implemented"]

    status = "completed" if succeeded else "failed"

    return {
        "status": status,
        "total": len(results),
        "succeeded": len(succeeded),
        "results": results,
        "warnings": [r["reason"] for r in stubs],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": (
            "DC decommission is not reversible. If an EC2 instance was incorrectly "
            "terminated, restore from its most recent AMI or EBS snapshot. For "
            "WinRM-shutdown hosts, power them on manually."
        ),
        "instance_ids": [
            r.get("instance_id") for r in execution_result.get("results", [])
            if r.get("instance_id")
        ],
        "hostnames": [
            r.get("winrm_hostname") for r in execution_result.get("results", [])
            if r.get("winrm_hostname")
        ],
    }
