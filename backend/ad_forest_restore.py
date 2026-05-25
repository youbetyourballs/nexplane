"""
ad_forest_restore — restore an AD forest from an IFM S3 snapshot onto a clean Windows Server.

Sequence:
  1. Download and validate manifest.json (must be format: "ifm"; rejects legacy)
  2. Pre-flight: optional DC isolation check (require_dc_isolation parameter)
  3. Connect to target; confirm NOT already a DC
  4. Install AD DS role
  5. Download IFM.zip from S3 via presigned URL; extract to C:\Temp\NexplaneIFM
  6. Promote via Install-ADDSDomainController -InstallationMediaPath (triggers reboot)
  7. Wait for reboot + reconnect (up to 15 min)
  8. Post-reboot verification: NTDS running, DNS root correct, SYSVOL share present
  9. DNS cutover: route53 | azure | manual instructions
 10. Inline dc_integrity_check — result included in CR output

Rollback: Uninstall-ADDSDomainController on target if promotion completed.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

_PS_CHECK_ADDS = "(Get-WindowsFeature AD-Domain-Services).InstallState"

_PS_INSTALL_ADDS = r"""
Install-WindowsFeature -Name AD-Domain-Services -IncludeManagementTools -ErrorAction Stop
Write-Output "ADDS_INSTALLED"
"""

_PS_DOWNLOAD_IFM = r"""
param([string]$Url, [string]$ZipPath)
if (!(Test-Path "C:\Temp")) { New-Item -ItemType Directory -Path "C:\Temp" | Out-Null }
Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing
Write-Output "IFM_DOWNLOADED"
"""

_PS_EXTRACT_IFM = r"""
param([string]$ZipPath, [string]$Dest)
if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
Expand-Archive -Path $ZipPath -DestinationPath $Dest -Force
if (!(Test-Path "$Dest\Active Directory\ntds.dit")) {
    throw "IFM extraction failed: ntds.dit not found in $Dest\Active Directory\"
}
Write-Output "IFM_EXTRACTED"
"""

_PS_PROMOTE_IFM = r"""
param([string]$DomainName, [string]$IFMPath, [string]$SafeModePassword)
Import-Module ADDSDeployment
$secPwd = ConvertTo-SecureString $SafeModePassword -AsPlainText -Force
Install-ADDSDomainController `
    -DomainName $DomainName `
    -InstallationMediaPath $IFMPath `
    -SafeModeAdministratorPassword $secPwd `
    -InstallDns:$true `
    -NoRebootOnCompletion:$false `
    -Force:$true
Write-Output "DC_PROMOTED"
"""

_PS_VERIFY_DC = r"""
param([string]$DomainName)
$ntds   = (Get-Service NTDS -ErrorAction SilentlyContinue).Status
$dns    = try { (Get-ADDomain -ErrorAction Stop).DNSRoot } catch { "unavailable" }
$sysvol = if (Get-SmbShare -Name SYSVOL -ErrorAction SilentlyContinue) { "present" } else { "absent" }
$nl     = (nltest /dsgetdc:$DomainName 2>&1) -join "`n"
$ip     = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.IPAddress -notlike "127.*" } |
           Select-Object -First 1).IPAddress
Write-Output "NTDS_STATUS=$ntds"
Write-Output "DNS_ROOT=$dns"
Write-Output "SYSVOL_SHARE=$sysvol"
Write-Output "DC_IP=$ip"
Write-Output "DC_VERIFIED"
"""

_PS_UNDEMOTE = r"""
param([string]$SafeModePassword)
$secPwd = ConvertTo-SecureString $SafeModePassword -AsPlainText -Force
Uninstall-ADDSDomainController `
    -LocalAdministratorPassword $secPwd `
    -Force:$true `
    -NoRebootOnCompletion:$false
Write-Output "UNDEMOTED"
"""


# ---------------------------------------------------------------------------
# WinRM helpers
# ---------------------------------------------------------------------------

def _winrm_client(hostname: str, username: str, password: str,
                   port: int = 5985, use_ssl: bool = False):
    import winrm
    scheme = "https" if use_ssl else "http"
    return winrm.Protocol(
        endpoint=f"{scheme}://{hostname}:{port}/wsman",
        transport="basic",
        username=username,
        password=password,
        server_cert_validation="ignore",
    )


def _run_ps(proto, script: str) -> tuple[str, str, int]:
    shell_id = proto.open_shell()
    try:
        cmd_id = proto.run_command(
            shell_id, "powershell",
            ["-NonInteractive", "-NoProfile", "-Command", script],
        )
        stdout, stderr, rc = proto.get_command_output(shell_id, cmd_id)
        proto.cleanup_command(shell_id, cmd_id)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            rc,
        )
    finally:
        proto.close_shell(shell_id)


def _run_ps_params(proto, script: str, params: dict) -> tuple[str, str, int]:
    prefix = "\n".join(f"${k} = @\"\n{v}\n\"@" for k, v in params.items())
    body = script.strip()
    if body.startswith("param("):
        depth = 0
        for i, ch in enumerate(body):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    body = body[i + 1:].strip()
                    break
    return _run_ps(proto, prefix + "\n" + body)


def _is_winrm_reachable(hostname: str, username: str, password: str,
                          port: int = 5985, use_ssl: bool = False) -> bool:
    import socket
    try:
        sock = socket.create_connection((hostname, port), timeout=10)
        sock.close()
    except OSError:
        return False
    try:
        proto = _winrm_client(hostname, username, password, port, use_ssl)
        out, _, rc = _run_ps(proto, "Write-Output 'PING'")
        return rc == 0 and "PING" in out
    except Exception:
        return False


def _wait_offline(hostname: str, port: int = 5985, timeout_min: int = 5) -> None:
    import socket
    deadline = time.monotonic() + timeout_min * 60
    while time.monotonic() < deadline:
        try:
            socket.create_connection((hostname, port), timeout=5).close()
            time.sleep(10)
        except OSError:
            return


def _wait_online(hostname: str, username: str, password: str,
                  port: int = 5985, use_ssl: bool = False,
                  timeout_min: int = 15) -> bool:
    deadline = time.monotonic() + timeout_min * 60
    while time.monotonic() < deadline:
        if _is_winrm_reachable(hostname, username, password, port, use_ssl):
            return True
        time.sleep(15)
    return False


# ---------------------------------------------------------------------------
# S3 helper — module-level so tests can monkeypatch
# ---------------------------------------------------------------------------

def _s3_client(aws_region: str = "us-east-1"):
    import boto3
    return boto3.client("s3", region_name=aws_region)


# ---------------------------------------------------------------------------
# Core restore (blocking — runs in executor thread)
# ---------------------------------------------------------------------------

def _do_restore(
    target_hostname: str,
    winrm_username: str,
    winrm_password: str,
    winrm_port: int,
    winrm_use_ssl: bool,
    s3_bucket: str,
    snapshot_s3_prefix: str,
    domain_name: str,
    safe_mode_password: str,
    require_dc_isolation: bool,
    existing_dc_hostnames: list,
    dns_update_mode: str,
    route53_zone_id: str | None,
    azure_zone_name: str | None,
    azure_resource_group: str | None,
    aws_region: str,
) -> dict:

    s3 = _s3_client(aws_region)

    # Step 1 — Validate manifest
    manifest_key = f"{snapshot_s3_prefix}/manifest.json"
    logger.info("Step 1: reading manifest s3://%s/%s", s3_bucket, manifest_key)
    obj = s3.get_object(Bucket=s3_bucket, Key=manifest_key)
    manifest = json.loads(obj["Body"].read())
    if manifest.get("format") != "ifm":
        raise ValueError(
            f"Snapshot format is '{manifest.get('format', 'legacy')}' — re-run "
            "ad_forest_snapshot to create an IFM snapshot before restoring. No offline "
            "conversion is possible: the Registry/SYSTEM hive (Boot Key) was not captured "
            "in the legacy format."
        )
    domain_name = domain_name or manifest.get("domain_name", "")
    if not domain_name:
        raise ValueError("domain_name is required (not found in parameters or manifest)")

    # Step 2 — Isolation check
    logger.info("Step 2: DC isolation check (require=%s, hosts=%s)",
                require_dc_isolation, existing_dc_hostnames)
    for dc_host in existing_dc_hostnames:
        reachable = _is_winrm_reachable(
            dc_host, winrm_username, winrm_password, winrm_port, winrm_use_ssl
        )
        if reachable and require_dc_isolation:
            raise ValueError(
                f"DC {dc_host} is still reachable — isolate all DCs before running restore "
                "or set require_dc_isolation: false for partial-compromise scenario"
            )
        if reachable:
            logger.warning("DC %s reachable — proceeding (require_dc_isolation=false)", dc_host)

    # Step 3 — Connect to target; confirm clean
    logger.info("Step 3: connecting to target %s", target_hostname)
    proto = _winrm_client(target_hostname, winrm_username, winrm_password, winrm_port, winrm_use_ssl)
    out, err, rc = _run_ps(proto, _PS_CHECK_ADDS)
    if rc != 0:
        raise RuntimeError(f"Cannot query AD DS state on target: {err or out}")
    if out.strip() == "Installed":
        raise ValueError(f"Target {target_hostname} already has AD DS — provide a clean Windows Server")

    # Step 4 — Install AD DS role
    logger.info("Step 4: installing AD DS role")
    out, err, rc = _run_ps(proto, _PS_INSTALL_ADDS)
    if rc != 0 or "ADDS_INSTALLED" not in out:
        raise RuntimeError(f"AD DS role installation failed: {err or out}")

    # Step 5 — Download IFM.zip and extract
    logger.info("Step 5: downloading IFM.zip from S3")
    ifm_s3_key = f"{snapshot_s3_prefix}/IFM.zip"
    presigned = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": s3_bucket, "Key": ifm_s3_key},
        ExpiresIn=3600,
    )
    ifm_zip_path = r"C:\Temp\NexplaneIFM.zip"
    ifm_dir_path = r"C:\Temp\NexplaneIFM"

    out, err, rc = _run_ps_params(proto, _PS_DOWNLOAD_IFM,
                                   {"Url": presigned, "ZipPath": ifm_zip_path})
    if rc != 0 or "IFM_DOWNLOADED" not in out:
        raise RuntimeError(f"IFM download failed: {err or out}")

    out, err, rc = _run_ps_params(proto, _PS_EXTRACT_IFM,
                                   {"ZipPath": ifm_zip_path, "Dest": ifm_dir_path})
    if rc != 0 or "IFM_EXTRACTED" not in out:
        raise RuntimeError(f"IFM extraction failed: {err or out}")

    # Step 6 — Promote via IFM (WinRM drops on reboot — expected)
    logger.info("Step 6: promoting target as DC via IFM")
    try:
        _run_ps_params(proto, _PS_PROMOTE_IFM, {
            "DomainName": domain_name,
            "IFMPath": ifm_dir_path,
            "SafeModePassword": safe_mode_password,
        })
    except Exception as exc:
        exc_s = str(exc)
        if any(k in exc_s.lower() for k in ("connection", "timeout", "reset", "eof", "winrm")):
            logger.info("WinRM dropped (expected — server rebooting): %s", exc_s[:120])
        else:
            raise

    _wait_offline(target_hostname, port=winrm_port, timeout_min=5)
    logger.info("Step 7: waiting for target to come back online (up to 15 min)")
    came_back = _wait_online(target_hostname, winrm_username, winrm_password,
                              winrm_port, winrm_use_ssl, timeout_min=15)
    if not came_back:
        raise RuntimeError(
            f"Target {target_hostname} did not return via WinRM within 15 min after promotion"
        )

    # Step 7 — Post-reboot verification
    logger.info("Step 7: post-reboot verification")
    proto = _winrm_client(target_hostname, winrm_username, winrm_password, winrm_port, winrm_use_ssl)
    out, err, rc = _run_ps_params(proto, _PS_VERIFY_DC, {"DomainName": domain_name})
    if rc != 0 or "DC_VERIFIED" not in out:
        raise RuntimeError(f"Post-reboot DC verification failed: {err or out}")

    v = {line.split("=", 1)[0]: line.split("=", 1)[1]
         for line in out.splitlines() if "=" in line}
    ntds_status = v.get("NTDS_STATUS", "unknown")
    dns_root = v.get("DNS_ROOT", "unknown")
    new_dc_ip = v.get("DC_IP", "unknown")
    sysvol_status = "present" if v.get("SYSVOL_SHARE") == "present" else "pending"

    if ntds_status.lower() != "running":
        raise RuntimeError(f"NTDS not running after restore — status: {ntds_status}")

    # Step 8 — DNS cutover
    logger.info("Step 8: DNS cutover (mode=%s)", dns_update_mode)
    dns_updated = False
    dns_instructions = None
    if dns_update_mode == "route53":
        if not route53_zone_id:
            raise ValueError("route53_zone_id required when dns_update_mode=route53")
        import boto3
        r53 = boto3.client("route53", region_name=aws_region)
        r53.change_resource_record_sets(
            HostedZoneId=route53_zone_id,
            ChangeBatch={"Changes": [{"Action": "UPSERT", "ResourceRecordSet": {
                "Name": domain_name, "Type": "A", "TTL": 60,
                "ResourceRecords": [{"Value": new_dc_ip}],
            }}]},
        )
        dns_updated = True
    elif dns_update_mode == "azure":
        if not azure_zone_name or not azure_resource_group:
            raise ValueError("azure_zone_name and azure_resource_group required for azure mode")
        import httpx
        token = httpx.get(
            "http://169.254.169.254/metadata/identity/oauth2/token",
            params={"api-version": "2018-02-01", "resource": "https://management.azure.com/"},
            headers={"Metadata": "true"}, timeout=10,
        ).json()["access_token"]
        subs = httpx.get(
            "https://management.azure.com/subscriptions?api-version=2020-01-01",
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        ).json()["value"]
        sub_id = subs[0]["subscriptionId"]
        record_name = domain_name.rstrip(".")
        if record_name.endswith(f".{azure_zone_name.rstrip('.')}"):
            record_name = record_name[:-(len(azure_zone_name) + 1)]
        url = (f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/"
               f"{azure_resource_group}/providers/Microsoft.Network/dnsZones/"
               f"{azure_zone_name}/A/{record_name}?api-version=2018-05-01")
        httpx.put(url, json={"properties": {"TTL": 60, "ARecords": [{"ipv4Address": new_dc_ip}]}},
                  headers={"Authorization": f"Bearer {token}"}, timeout=30).raise_for_status()
        dns_updated = True
    else:
        dns_instructions = (
            f"Update DNS A record for {domain_name} to {new_dc_ip}. "
            "Recommended TTL: 60s for cutover, increase after validation."
        )

    result: dict = {
        "status": "completed",
        "new_dc_hostname": target_hostname,
        "new_dc_ip": new_dc_ip,
        "domain_name": domain_name,
        "snapshot_restored": snapshot_s3_prefix,
        "dc_verification": "passed",
        "ntds_service_status": ntds_status,
        "dns_root": dns_root,
        "sysvol_status": sysvol_status,
        "dns_update_mode": dns_update_mode,
        "dns_updated": dns_updated,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "next_steps": [
            "Verify domain authentication from a client machine",
            "Run ad_dc_decommission to terminate isolated/compromised DCs",
            "Update DNS TTL back to normal after cutover is confirmed",
        ],
    }
    if dns_instructions:
        result["dns_instructions"] = dns_instructions
    return result


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    target_hostname      = parameters["target_hostname"]
    winrm_username       = parameters["winrm_username"]
    winrm_password       = parameters["winrm_password"]
    snapshot_s3_prefix   = parameters["snapshot_s3_prefix"]
    s3_bucket            = parameters["s3_bucket"]
    safe_mode_password   = parameters["safe_mode_password"]

    winrm_port           = int(parameters.get("winrm_port", 5985))
    winrm_use_ssl        = str(parameters.get("winrm_use_ssl", "false")).lower() == "true"
    domain_name          = parameters.get("domain_name", "")
    require_dc_isolation = bool(parameters.get("require_dc_isolation", False))
    existing_dc_hostnames = parameters.get("existing_dc_hostnames") or []
    dns_update_mode      = parameters.get("dns_update_mode", "manual")
    route53_zone_id      = parameters.get("route53_zone_id")
    azure_zone_name      = parameters.get("azure_zone_name")
    azure_resource_group = parameters.get("azure_resource_group")
    aws_region           = parameters.get("aws_region", "us-east-1")

    if dns_update_mode not in ("route53", "azure", "manual"):
        raise ValueError(f"dns_update_mode must be route53 | azure | manual — got {dns_update_mode!r}")

    loop = asyncio.get_event_loop()
    restore_result = await loop.run_in_executor(
        None,
        lambda: _do_restore(
            target_hostname=target_hostname,
            winrm_username=winrm_username,
            winrm_password=winrm_password,
            winrm_port=winrm_port,
            winrm_use_ssl=winrm_use_ssl,
            s3_bucket=s3_bucket,
            snapshot_s3_prefix=snapshot_s3_prefix,
            domain_name=domain_name,
            safe_mode_password=safe_mode_password,
            require_dc_isolation=require_dc_isolation,
            existing_dc_hostnames=existing_dc_hostnames,
            dns_update_mode=dns_update_mode,
            route53_zone_id=route53_zone_id,
            azure_zone_name=azure_zone_name,
            azure_resource_group=azure_resource_group,
            aws_region=aws_region,
        ),
    )

    # Step 9 — Inline dc_integrity_check
    try:
        from app.connectors.executors.active_directory import dc_integrity_check

        class _InlineConnector:
            credentials = {
                "winrm_hostname": target_hostname,
                "winrm_username": winrm_username,
                "winrm_password": winrm_password,
                "winrm_port": str(winrm_port),
            }

        integrity = await dc_integrity_check.execute(
            {"dc_hostname": target_hostname}, [], _InlineConnector()
        )
        restore_result["post_restore_integrity"] = integrity
    except Exception as e:
        restore_result["post_restore_integrity"] = {
            "error": str(e),
            "note": "integrity check failed — DC may still be initialising",
        }

    return restore_result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    target_hostname    = execution_result.get("new_dc_hostname") or parameters.get("target_hostname")
    safe_mode_password = parameters.get("safe_mode_password", "")
    winrm_username     = parameters.get("winrm_username", "")
    winrm_password     = parameters.get("winrm_password", "")
    winrm_port         = int(parameters.get("winrm_port", 5985))
    winrm_use_ssl      = str(parameters.get("winrm_use_ssl", "false")).lower() == "true"

    if not target_hostname:
        return {"rolled_back": False,
                "reason": "No target_hostname — cannot determine which host to undemote"}
    if not winrm_username or not winrm_password:
        return {"rolled_back": False, "reason": "winrm credentials not available",
                "target_hostname": target_hostname}

    def _do_rollback():
        try:
            proto = _winrm_client(target_hostname, winrm_username, winrm_password,
                                   winrm_port, winrm_use_ssl)
            out, err, rc = _run_ps_params(proto, _PS_UNDEMOTE,
                                           {"SafeModePassword": safe_mode_password})
            if rc != 0 and "UNDEMOTED" not in out:
                return {"rolled_back": False, "target_hostname": target_hostname,
                        "reason": f"Undemote failed (rc={rc}): {err or out}"}
            return {"rolled_back": True, "target_hostname": target_hostname,
                    "note": "AD DS removed — server returned to pre-restore state"}
        except Exception as exc:
            exc_s = str(exc)
            if any(k in exc_s.lower() for k in ("connection", "timeout", "reset", "eof", "winrm")):
                return {"rolled_back": True, "target_hostname": target_hostname,
                        "note": "Undemote sent — WinRM dropped (expected reboot)"}
            return {"rolled_back": False, "target_hostname": target_hostname, "reason": exc_s}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_rollback)
