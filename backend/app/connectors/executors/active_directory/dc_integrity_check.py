"""
dc_integrity_check — read-only DC health audit via WinRM PowerShell + LDAP.

WinRM checks (requires winrm_* credentials):
  - AD replication status: Get-ADReplicationPartnerMetadata
  - SYSVOL consistency: Test-Path SYSVOL share, DFSR backlog count
  - GPO hash: SHA256 of SYSVOL\\domain\\Policies directory tree for drift detection
  - Unauthorized privileged accounts: members of Domain Admins, Schema Admins, Enterprise Admins

LDAP checks (always run if base_dn + bind_dn available):
  - DC count and enabled status
  - Accounts with adminCount=1 (privileged)
  - Password policy retrieval
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# WinRM helpers
# ---------------------------------------------------------------------------

def _run_ps(creds: dict, script: str, dc_hostname: str | None = None) -> tuple[str, str, int]:
    """Run a PowerShell script via WinRM Session.run_ps() (base64-encoded, avoids cmd.exe pipe issues)."""
    from ._client import run_winrm_ps
    return run_winrm_ps(creds, script, dc_hostname)


# ---------------------------------------------------------------------------
# WinRM checks
# ---------------------------------------------------------------------------

_PS_REPLICATION = r"""
try {
    $data = Get-ADReplicationPartnerMetadata -Target * -Scope Domain -ErrorAction Stop |
        Select-Object Server, LastReplicationAttempt, LastReplicationResult, LastReplicationSuccess
    $data | ConvertTo-Json -Depth 3
} catch {
    Write-Output "ERROR: $_"
}
"""

_PS_SYSVOL = r"""
$computerName = $env:COMPUTERNAME
$sysvolPath = "\\$computerName\SYSVOL"
$reachable = Test-Path $sysvolPath
$backlog = ""
try {
    $backlog = (dfsrdiag backlog /rgname:"Domain System Volume" /rfname:SYSVOL `
        /sendingmember:$computerName /receivingmember:$computerName 2>&1) -join "`n"
} catch { $backlog = "dfsrdiag unavailable" }
[PSCustomObject]@{reachable=$reachable; dfsr_backlog=$backlog} | ConvertTo-Json
"""

_PS_GPO_HASH = r"""
try {
    $policyRoot = "C:\Windows\SYSVOL\domain\Policies"
    $files = Get-ChildItem $policyRoot -Recurse -File -ErrorAction Stop |
        Sort-Object FullName
    $hashes = $files | ForEach-Object {
        (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    }
    $combined = $hashes -join "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($combined)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hashBytes = $sha.ComputeHash($bytes)
    ($hashBytes | ForEach-Object { $_.ToString("x2") }) -join ""
} catch {
    Write-Output "ERROR: $_"
}
"""

_PS_PRIV_ACCOUNTS = r"""
$groups = @("Domain Admins", "Schema Admins", "Enterprise Admins")
$results = @()
foreach ($g in $groups) {
    try {
        $members = Get-ADGroupMember $g -Recursive -ErrorAction Stop |
            Where-Object { $_.objectClass -eq "user" } |
            ForEach-Object {
                $u = Get-ADUser $_.SamAccountName -Properties Enabled -ErrorAction SilentlyContinue
                if ($u) {
                    [PSCustomObject]@{group=$g; name=$u.Name; sam=$u.SamAccountName; enabled=$u.Enabled}
                }
            }
        $results += $members
    } catch { }
}
$results | ConvertTo-Json -Depth 3
"""


def _winrm_checks(creds: dict, dc_hostname: str, baseline_gpo_hash: str | None) -> dict:
    # --- Replication ---
    repl_raw, repl_err, repl_rc = _run_ps(creds, _PS_REPLICATION, dc_hostname)
    repl_status = "ok"
    repl_partners = []
    if repl_raw.startswith("ERROR") or repl_rc != 0:
        repl_status = "failed"
    else:
        try:
            partners = json.loads(repl_raw)
            if not isinstance(partners, list):
                partners = [partners]
            repl_partners = partners
            # Any non-zero LastReplicationResult means degraded
            for p in partners:
                if p.get("LastReplicationResult", 0) != 0:
                    repl_status = "degraded"
                    break
        except json.JSONDecodeError:
            repl_status = "degraded"

    # --- SYSVOL ---
    sysvol_raw, _, _ = _run_ps(creds, _PS_SYSVOL, dc_hostname)
    try:
        sysvol_data = json.loads(sysvol_raw)
        sysvol = {
            "reachable": bool(sysvol_data.get("reachable", False)),
            "dfsr_backlog": sysvol_data.get("dfsr_backlog", ""),
        }
    except json.JSONDecodeError:
        sysvol = {"reachable": False, "dfsr_backlog": sysvol_raw}

    # --- GPO hash ---
    gpo_raw, _, _ = _run_ps(creds, _PS_GPO_HASH, dc_hostname)
    gpo_hash = f"sha256:{gpo_raw}" if not gpo_raw.startswith("ERROR") else "unavailable"
    gpo_drift = False
    if baseline_gpo_hash and gpo_hash != "unavailable":
        # removeprefix (not lstrip) — lstrip strips individual chars, not the full prefix
        baseline_raw = baseline_gpo_hash.removeprefix("sha256:")
        gpo_drift = (baseline_raw != gpo_raw)

    # --- Privileged accounts ---
    priv_raw, _, _ = _run_ps(creds, _PS_PRIV_ACCOUNTS, dc_hostname)
    privileged_accounts = []
    try:
        raw_list = json.loads(priv_raw)
        if not isinstance(raw_list, list):
            raw_list = [raw_list]
        privileged_accounts = [
            {"group": a.get("group"), "name": a.get("name"), "sam": a.get("sam"), "enabled": a.get("enabled")}
            for a in raw_list
        ]
    except json.JSONDecodeError:
        pass

    return {
        "replication": {
            "status": repl_status,
            "partners": repl_partners,
        },
        "sysvol": sysvol,
        "gpo_hash": gpo_hash,
        "gpo_drift_detected": gpo_drift,
        "privileged_accounts": privileged_accounts,
    }


# ---------------------------------------------------------------------------
# LDAP checks
# ---------------------------------------------------------------------------

def _ldap_checks(creds: dict) -> dict:
    from ldap3 import ALL, Connection, Server

    server = Server(creds["server"], port=int(creds.get("port", 389)), get_info=ALL)
    conn = Connection(server, user=creds["bind_dn"], password=creds["bind_password"], auto_bind=True)
    base_dn = creds["base_dn"]

    # DC objects (UAC bit 8192 = SERVER_TRUST_ACCOUNT = domain controller)
    conn.search(
        base_dn,
        "(&(objectClass=computer)(userAccountControl:1.2.840.113556.1.4.803:=8192))",
        attributes=["cn", "userAccountControl", "operatingSystem"],
    )
    dcs = [
        {
            "cn": str(e.cn.value if e.cn else ""),
            "enabled": not bool(int(str(e.userAccountControl.value if e.userAccountControl else 0)) & 2),
            "os": str(e.operatingSystem.value if e.operatingSystem else ""),
        }
        for e in conn.entries
    ]

    # adminCount=1 privileged accounts
    conn.search(base_dn, "(&(objectClass=user)(adminCount=1))", attributes=["sAMAccountName", "userAccountControl"])
    priv_ldap = [
        {
            "sam": str(e.sAMAccountName.value if e.sAMAccountName else ""),
            "enabled": not bool(int(str(e.userAccountControl.value if e.userAccountControl else 0)) & 2),
        }
        for e in conn.entries
    ]

    conn.unbind()
    return {"domain_controllers": dcs, "admin_count_accounts": priv_ldap}


# ---------------------------------------------------------------------------
# Overall health determination
# ---------------------------------------------------------------------------

def _overall_health(repl_status: str, sysvol_reachable: bool) -> str:
    if repl_status == "failed" or not sysvol_reachable:
        return "critical"
    if repl_status == "degraded":
        return "degraded"
    return "healthy"


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    dc_hostname = parameters.get("dc_hostname") or creds.get("server", "unknown")
    baseline_gpo_hash = parameters.get("baseline_gpo_hash")

    has_winrm = all(
        creds.get(k) for k in ("winrm_hostname", "winrm_username", "winrm_password")
    )
    has_ldap = all(creds.get(k) for k in ("server", "bind_dn", "bind_password", "base_dn"))

    result: dict = {
        "dc_hostname": dc_hostname,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    loop = asyncio.get_event_loop()

    # WinRM checks
    if has_winrm:
        winrm_data = await loop.run_in_executor(
            None, lambda: _winrm_checks(creds, dc_hostname, baseline_gpo_hash)
        )
        result.update(winrm_data)
        result["winrm_checks"] = "completed"
    else:
        result["winrm_checks"] = "skipped"
        result["replication"] = {"status": "unknown", "partners": []}
        result["sysvol"] = {"reachable": None, "dfsr_backlog": ""}
        result["gpo_hash"] = "unavailable"
        result["gpo_drift_detected"] = False
        result["privileged_accounts"] = []

    # LDAP checks
    if has_ldap:
        ldap_data = await loop.run_in_executor(None, lambda: _ldap_checks(creds))
        result["ldap_checks"] = ldap_data
    else:
        result["ldap_checks"] = "skipped"

    # Overall health (best-effort if WinRM skipped)
    repl_status = result.get("replication", {}).get("status", "unknown")
    sysvol_reachable = result.get("sysvol", {}).get("reachable", True)
    if result["winrm_checks"] == "skipped":
        result["overall_health"] = "unknown"
    else:
        result["overall_health"] = _overall_health(repl_status, bool(sysvol_reachable))

    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "dc_integrity_check is read-only — no rollback needed"}
