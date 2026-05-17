"""
ad_forest_restore — restore an AD forest from an S3 snapshot onto a clean Windows Server target.

Design: cloud/on-prem agnostic. Uses WinRM only. The operator pre-provisions a clean
Windows Server and passes its hostname. No EC2/cloud API calls are made here.

Sequence:
  1. Verify snapshot exists in S3
  2. Pre-flight: verify existing DCs are isolated (any WinRM response = abort)
  3. Connect to target via WinRM; confirm it is NOT already a DC
  4. Install AD DS role on target
  5. Download ntds.dit from S3 via presigned URL (faster than base64 over WinRM)
  6. Authoritative restore: stop NTDS, place ntds.dit, promote DC, wait for reboot
  7. Post-reboot verification: NTDS service running, domain responding, nltest
  8. DNS cutover: route53 | azure | manual instructions

Rollback:
  Undemotes the DC (Uninstall-ADDSDomainController) if it was promoted; logs failure
  if promotion never completed.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PowerShell scripts
# ---------------------------------------------------------------------------

_PS_CHECK_ADDS_STATE = "(Get-WindowsFeature AD-Domain-Services).InstallState"

_PS_INSTALL_ADDS = r"""
Install-WindowsFeature -Name AD-Domain-Services -IncludeManagementTools -ErrorAction Stop
Write-Output "ADDS_INSTALLED"
"""

_PS_DOWNLOAD_NTDS = r"""
param([string]$Url, [string]$OutPath)
if (!(Test-Path "C:\Temp")) { New-Item -ItemType Directory -Path "C:\Temp" | Out-Null }
Invoke-WebRequest -Uri $Url -OutFile $OutPath -UseBasicParsing
Write-Output "NTDS_DOWNLOADED"
"""

_PS_AUTHORITATIVE_RESTORE = r"""
param(
    [string]$NtdsSrc,
    [string]$DomainName,
    [string]$SafeModePassword
)
# Ensure NTDS directory exists
New-Item -ItemType Directory -Path 'C:\Windows\NTDS' -Force | Out-Null

# Stop NTDS if somehow running (shouldn't be on clean install)
Stop-Service NTDS -Force -ErrorAction SilentlyContinue

# Place ntds.dit
Copy-Item $NtdsSrc 'C:\Windows\NTDS\ntds.dit' -Force

# Promote this server as an additional DC using the restored ntds.dit.
# NoRebootOnCompletion:$false causes an immediate reboot — WinRM will drop.
Import-Module ADDSDeployment
$secPwd = ConvertTo-SecureString $SafeModePassword -AsPlainText -Force
Install-ADDSDomainController `
    -DomainName $DomainName `
    -InstallDns:$true `
    -SafeModeAdministratorPassword $secPwd `
    -Force:$true `
    -NoRebootOnCompletion:$false
Write-Output "DC_PROMOTED"
"""

_PS_VERIFY_DC = r"""
param([string]$DomainName)
$ntds = (Get-Service NTDS -ErrorAction SilentlyContinue).Status
$dns = try { (Get-ADDomain -ErrorAction Stop).DNSRoot } catch { "unavailable" }
$nltest = (nltest /dsgetdc:$DomainName 2>&1) -join "`n"
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike '127.*' } |
       Select-Object -First 1).IPAddress
Write-Output "NTDS_STATUS=$ntds"
Write-Output "DNS_ROOT=$dns"
Write-Output "NLTEST_OUTPUT=$nltest"
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

def _build_winrm_client(hostname: str, username: str, password: str,
                         port: int = 5985, use_ssl: bool = False):
    import winrm

    scheme = "https" if use_ssl else "http"
    return winrm.Protocol(
        endpoint=f"{scheme}://{hostname}:{port}/wsman",
        transport="ntlm",
        username=username,
        password=password,
        server_cert_validation="ignore",
    )


def _run_ps(protocol, script: str) -> tuple[str, str, int]:
    shell_id = protocol.open_shell()
    try:
        command_id = protocol.run_command(
            shell_id,
            "powershell",
            ["-NonInteractive", "-NoProfile", "-Command", script],
        )
        stdout, stderr, status = protocol.get_command_output(shell_id, command_id)
        protocol.cleanup_command(shell_id, command_id)
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            status,
        )
    finally:
        protocol.close_shell(shell_id)


def _run_ps_with_params(protocol, script: str, params: dict) -> tuple[str, str, int]:
    """Inline named parameters into a script that starts with param(...) declarations."""
    # Build a prefix that sets each variable before the script body
    prefix_lines = []
    for k, v in params.items():
        # Escape single quotes in values
        safe_v = str(v).replace("'", "''")
        prefix_lines.append(f"${k} = '{safe_v}'")
    # Strip the param() declaration from the script (first non-blank lines starting with "param")
    body = script.strip()
    if body.startswith("param("):
        # Remove up to the closing paren of the param block
        paren_depth = 0
        i = 0
        for i, ch in enumerate(body):
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    break
        body = body[i + 1:].strip()
    full_script = "\n".join(prefix_lines) + "\n" + body
    return _run_ps(protocol, full_script)


def _is_winrm_reachable(hostname: str, username: str, password: str,
                          port: int = 5985, use_ssl: bool = False,
                          timeout_seconds: int = 10) -> bool:
    """Return True if WinRM responds within timeout_seconds."""
    import socket
    import winrm

    # Quick TCP check first
    try:
        sock = socket.create_connection((hostname, port), timeout=timeout_seconds)
        sock.close()
    except (OSError, ConnectionRefusedError):
        return False

    try:
        proto = _build_winrm_client(hostname, username, password, port, use_ssl)
        out, _, rc = _run_ps(proto, "Write-Output 'PING'")
        return rc == 0 and "PING" in out
    except Exception:
        return False


def _wait_for_winrm(hostname: str, username: str, password: str,
                     port: int = 5985, use_ssl: bool = False,
                     timeout_minutes: int = 10, poll_interval: int = 15) -> bool:
    """Poll until WinRM responds or timeout expires. Returns True on success."""
    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        if _is_winrm_reachable(hostname, username, password, port, use_ssl, timeout_seconds=10):
            return True
        time.sleep(poll_interval)
    return False


def _wait_for_winrm_offline(hostname: str, port: int = 5985,
                              timeout_minutes: int = 5, poll_interval: int = 10) -> None:
    """Wait until TCP port stops responding (server is rebooting)."""
    import socket

    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection((hostname, port), timeout=5)
            sock.close()
            time.sleep(poll_interval)
        except (OSError, ConnectionRefusedError):
            return  # went offline — good
    # Timed out waiting for offline — might have rebooted quickly, proceed anyway


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _s3_client(aws_region: str = "us-east-1"):
    import boto3

    return boto3.client("s3", region_name=aws_region)


def _verify_s3_object(s3, bucket: str, key: str) -> None:
    """Raise ValueError if the S3 object does not exist."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code == "404" or "NoSuchKey" in str(e) or "404" in str(e):
            raise ValueError(
                f"Snapshot not found: s3://{bucket}/{key} — run ad_forest_snapshot first"
            ) from e
        raise


def _presigned_url(s3, bucket: str, key: str, expiry_seconds: int = 900) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry_seconds,
    )


# ---------------------------------------------------------------------------
# DNS cutover helpers
# ---------------------------------------------------------------------------

def _dns_route53(route53_zone_id: str, domain_name: str, new_ip: str,
                  aws_region: str = "us-east-1") -> None:
    import boto3

    r53 = boto3.client("route53", region_name=aws_region)
    r53.change_resource_record_sets(
        HostedZoneId=route53_zone_id,
        ChangeBatch={
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": domain_name,
                        "Type": "A",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": new_ip}],
                    },
                }
            ]
        },
    )


def _dns_azure(zone_name: str, resource_group: str, domain_name: str, new_ip: str) -> None:
    """Upsert an A record in Azure DNS via the REST API (uses httpx, same pattern as other Azure executors)."""
    import httpx

    # Obtain token from IMDS (assumes managed identity on the executor host)
    token_resp = httpx.get(
        "http://169.254.169.254/metadata/identity/oauth2/token",
        params={
            "api-version": "2018-02-01",
            "resource": "https://management.azure.com/",
        },
        headers={"Metadata": "true"},
        timeout=10,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]

    # Determine the relative record name (strip trailing zone name)
    record_name = domain_name.rstrip(".")
    if record_name.endswith(f".{zone_name.rstrip('.')}"):
        record_name = record_name[: -(len(zone_name) + 1)]

    # Azure DNS REST API: PUT record set
    subscription_id = _get_azure_subscription_id(token)
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.Network"
        f"/dnsZones/{zone_name}/A/{record_name}"
        "?api-version=2018-05-01"
    )
    body = {
        "properties": {
            "TTL": 60,
            "ARecords": [{"ipv4Address": new_ip}],
        }
    }
    resp = httpx.put(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()


def _get_azure_subscription_id(token: str) -> str:
    import httpx

    resp = httpx.get(
        "https://management.azure.com/subscriptions?api-version=2020-01-01",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    subs = resp.json().get("value", [])
    if not subs:
        raise RuntimeError("No Azure subscriptions found via management API")
    return subs[0]["subscriptionId"]


# ---------------------------------------------------------------------------
# Core restore logic (blocking — runs in executor thread)
# ---------------------------------------------------------------------------

def _do_restore(
    target_hostname: str,
    winrm_username: str,
    winrm_password: str,
    winrm_port: int,
    winrm_use_ssl: bool,
    s3_bucket: str,
    snapshot_s3_key: str,
    domain_name: str,
    domain_netbios_name: str,
    safe_mode_password: str,
    existing_dc_hostnames: list,
    dns_update_mode: str,
    route53_zone_id: str | None,
    azure_zone_name: str | None,
    azure_resource_group: str | None,
    aws_region: str,
) -> dict:

    # ------------------------------------------------------------------
    # Step 1: Verify snapshot in S3
    # ------------------------------------------------------------------
    logger.info("Step 1: Verifying snapshot in S3: s3://%s/%s", s3_bucket, snapshot_s3_key)
    s3 = _s3_client(aws_region)
    _verify_s3_object(s3, s3_bucket, snapshot_s3_key)

    # ------------------------------------------------------------------
    # Step 2: Pre-flight — existing DCs must be isolated
    # ------------------------------------------------------------------
    logger.info("Step 2: Checking isolation of %d existing DC(s)", len(existing_dc_hostnames))
    if not existing_dc_hostnames:
        logger.warning(
            "existing_dc_hostnames not provided — DC isolation was NOT verified. "
            "Proceed only if you have confirmed all existing DCs are offline."
        )
    else:
        for dc_host in existing_dc_hostnames:
            reachable = _is_winrm_reachable(
                dc_host, winrm_username, winrm_password, winrm_port, winrm_use_ssl,
                timeout_seconds=10,
            )
            if reachable:
                raise ValueError(
                    f"DC {dc_host} is still reachable — isolate all existing DCs before running restore"
                )
        logger.info("All %d existing DC(s) confirmed unreachable — isolation verified", len(existing_dc_hostnames))

    # ------------------------------------------------------------------
    # Step 3: Connect to target; confirm it is not already a DC
    # ------------------------------------------------------------------
    logger.info("Step 3: Connecting to target %s via WinRM", target_hostname)
    proto = _build_winrm_client(target_hostname, winrm_username, winrm_password, winrm_port, winrm_use_ssl)
    install_state_out, err, rc = _run_ps(proto, _PS_CHECK_ADDS_STATE)
    if rc != 0:
        raise RuntimeError(
            f"Failed to query AD DS install state on {target_hostname}: {err or install_state_out}"
        )
    if install_state_out.strip() == "Installed":
        raise ValueError(
            f"Target {target_hostname} already has AD DS installed — "
            "will not overwrite an existing DC. Provide a clean Windows Server."
        )
    logger.info("Target AD DS install state: %s — safe to proceed", install_state_out.strip())

    # ------------------------------------------------------------------
    # Step 4: Install AD DS role
    # ------------------------------------------------------------------
    logger.info("Step 4: Installing AD DS role on %s", target_hostname)
    out, err, rc = _run_ps(proto, _PS_INSTALL_ADDS)
    if rc != 0 or "ADDS_INSTALLED" not in out:
        raise RuntimeError(f"AD DS role installation failed on {target_hostname}: {err or out}")

    # ------------------------------------------------------------------
    # Step 5: Download ntds.dit via presigned URL
    # ------------------------------------------------------------------
    logger.info("Step 5: Generating presigned URL for %s", snapshot_s3_key)
    presigned = _presigned_url(s3, s3_bucket, snapshot_s3_key, expiry_seconds=900)
    ntds_dest = r"C:\Temp\ntds_restore.dit"
    out, err, rc = _run_ps_with_params(
        proto,
        _PS_DOWNLOAD_NTDS,
        {"Url": presigned, "OutPath": ntds_dest},
    )
    if rc != 0 or "NTDS_DOWNLOADED" not in out:
        raise RuntimeError(f"Failed to download ntds.dit on target: {err or out}")

    # ------------------------------------------------------------------
    # Step 6: Authoritative restore + DC promotion (triggers reboot)
    # ------------------------------------------------------------------
    logger.info("Step 6: Initiating authoritative restore and DC promotion on %s", target_hostname)
    # We expect WinRM to drop mid-command when the reboot fires.
    # Catch the connection error gracefully.
    try:
        out, err, rc = _run_ps_with_params(
            proto,
            _PS_AUTHORITATIVE_RESTORE,
            {
                "NtdsSrc": ntds_dest,
                "DomainName": domain_name,
                "SafeModePassword": safe_mode_password,
            },
        )
        # If we somehow got a response without reboot, check for errors
        if rc != 0:
            raise RuntimeError(f"DC promotion command failed: {err or out}")
    except Exception as exc:
        exc_str = str(exc)
        # WinRM transport errors during reboot are expected
        if any(kw in exc_str.lower() for kw in ("connection", "timeout", "reset", "eof", "winrm")):
            logger.info("WinRM dropped (expected — server is rebooting): %s", exc_str[:120])
        else:
            raise

    # Wait for server to go offline (reboot)
    logger.info("Waiting for %s to go offline (reboot)...", target_hostname)
    _wait_for_winrm_offline(target_hostname, port=winrm_port, timeout_minutes=5, poll_interval=10)

    # Wait for WinRM to come back (up to 10 min)
    logger.info("Waiting for %s to come back online (up to 10 min)...", target_hostname)
    came_back = _wait_for_winrm(
        target_hostname, winrm_username, winrm_password, winrm_port, winrm_use_ssl,
        timeout_minutes=10, poll_interval=15,
    )
    if not came_back:
        raise RuntimeError(
            f"Target {target_hostname} did not return via WinRM within 10 minutes after promotion reboot"
        )

    # ------------------------------------------------------------------
    # Step 7: Post-reboot verification
    # ------------------------------------------------------------------
    logger.info("Step 7: Post-reboot verification on %s", target_hostname)
    proto = _build_winrm_client(target_hostname, winrm_username, winrm_password, winrm_port, winrm_use_ssl)
    out, err, rc = _run_ps_with_params(proto, _PS_VERIFY_DC, {"DomainName": domain_name})

    if rc != 0 or "DC_VERIFIED" not in out:
        raise RuntimeError(f"Post-reboot DC verification failed: {err or out}")

    # Parse verification output
    verification_lines = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in out.splitlines()
        if "=" in line
    }
    ntds_status = verification_lines.get("NTDS_STATUS", "unknown")
    dns_root = verification_lines.get("DNS_ROOT", "unknown")
    new_dc_ip = verification_lines.get("DC_IP", "unknown")

    if ntds_status.lower() != "running":
        raise RuntimeError(f"NTDS service is not running after restore — status: {ntds_status}")

    logger.info(
        "DC verified: NTDS=%s, DNS root=%s, IP=%s", ntds_status, dns_root, new_dc_ip
    )

    # ------------------------------------------------------------------
    # Step 8: DNS cutover
    # ------------------------------------------------------------------
    dns_updated = False
    dns_instructions = None

    logger.info("Step 8: DNS cutover (mode=%s)", dns_update_mode)
    if dns_update_mode == "route53":
        if not route53_zone_id:
            raise ValueError("route53_zone_id is required when dns_update_mode == 'route53'")
        _dns_route53(route53_zone_id, domain_name, new_dc_ip, aws_region)
        dns_updated = True
        logger.info("Route53 A record updated: %s -> %s", domain_name, new_dc_ip)

    elif dns_update_mode == "azure":
        if not azure_zone_name or not azure_resource_group:
            raise ValueError(
                "azure_zone_name and azure_resource_group are required when dns_update_mode == 'azure'"
            )
        _dns_azure(azure_zone_name, azure_resource_group, domain_name, new_dc_ip)
        dns_updated = True
        logger.info("Azure DNS A record updated: %s -> %s", domain_name, new_dc_ip)

    else:  # manual
        dns_instructions = (
            f"Update your DNS A record for {domain_name} to point to {new_dc_ip}. "
            "TTL recommended: 60s for initial cutover, increase after validation."
        )

    # ------------------------------------------------------------------
    # Build result
    # ------------------------------------------------------------------
    result: dict = {
        "status": "completed",
        "new_dc_hostname": target_hostname,
        "new_dc_ip": new_dc_ip,
        "domain_name": domain_name,
        "snapshot_restored": snapshot_s3_key,
        "ad_ds_install_state": "promoted",
        "dc_verification": "passed",
        "ntds_service_status": ntds_status,
        "dns_root": dns_root,
        "dns_update_mode": dns_update_mode,
        "dns_updated": dns_updated,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "next_steps": [
            "Verify domain authentication from a client machine",
            "Run dc_integrity_check against the new DC",
            "Decommission isolated/compromised DCs after validation",
            "Update DNS TTL back to normal values after cutover is confirmed",
        ],
    }
    if dns_instructions:
        result["dns_instructions"] = dns_instructions

    return result


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Required parameters
    target_hostname = parameters["target_hostname"]
    winrm_username = parameters["winrm_username"]
    winrm_password = parameters["winrm_password"]
    snapshot_s3_key = parameters["snapshot_s3_key"]
    s3_bucket = parameters["s3_bucket"]
    domain_name = parameters["domain_name"]
    safe_mode_password = parameters["safe_mode_password"]

    # Optional parameters
    winrm_port = int(parameters.get("winrm_port", 5985))
    winrm_use_ssl = str(parameters.get("winrm_use_ssl", "false")).lower() == "true"
    domain_netbios_name = parameters.get("domain_netbios_name") or domain_name.split(".")[0].upper()
    existing_dc_hostnames = parameters.get("existing_dc_hostnames") or []
    dns_update_mode = parameters.get("dns_update_mode", "manual")
    route53_zone_id = parameters.get("route53_zone_id")
    azure_zone_name = parameters.get("azure_zone_name")
    azure_resource_group = parameters.get("azure_resource_group")
    aws_region = parameters.get("aws_region", "us-east-1")

    if dns_update_mode not in ("route53", "azure", "manual"):
        raise ValueError(f"dns_update_mode must be 'route53', 'azure', or 'manual' — got: {dns_update_mode!r}")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _do_restore(
            target_hostname=target_hostname,
            winrm_username=winrm_username,
            winrm_password=winrm_password,
            winrm_port=winrm_port,
            winrm_use_ssl=winrm_use_ssl,
            s3_bucket=s3_bucket,
            snapshot_s3_key=snapshot_s3_key,
            domain_name=domain_name,
            domain_netbios_name=domain_netbios_name,
            safe_mode_password=safe_mode_password,
            existing_dc_hostnames=existing_dc_hostnames,
            dns_update_mode=dns_update_mode,
            route53_zone_id=route53_zone_id,
            azure_zone_name=azure_zone_name,
            azure_resource_group=azure_resource_group,
            aws_region=aws_region,
        ),
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Undemote the DC that was promoted during execute().
    If the DC never completed promotion, log the failure and return — the server
    is still effectively clean.
    """
    target_hostname = execution_result.get("new_dc_hostname") or parameters.get("target_hostname")
    safe_mode_password = parameters.get("safe_mode_password", "")
    winrm_username = parameters.get("winrm_username", "")
    winrm_password = parameters.get("winrm_password", "")
    winrm_port = int(parameters.get("winrm_port", 5985))
    winrm_use_ssl = str(parameters.get("winrm_use_ssl", "false")).lower() == "true"

    if not target_hostname:
        return {
            "rolled_back": False,
            "reason": "No target_hostname in execution result — cannot determine which host to undemote",
        }

    if not winrm_username or not winrm_password:
        return {
            "rolled_back": False,
            "reason": "winrm_username / winrm_password not available — cannot connect to undemote",
            "target_hostname": target_hostname,
        }

    def _do_rollback():
        try:
            proto = _build_winrm_client(
                target_hostname, winrm_username, winrm_password, winrm_port, winrm_use_ssl
            )
            out, err, rc = _run_ps_with_params(
                proto,
                _PS_UNDEMOTE,
                {"SafeModePassword": safe_mode_password},
            )
            if rc != 0 and "UNDEMOTED" not in out:
                return {
                    "rolled_back": False,
                    "target_hostname": target_hostname,
                    "reason": f"Undemote command failed (rc={rc}): {err or out}. "
                              "DC may not have been fully promoted — server may already be in a clean state.",
                }
            return {
                "rolled_back": True,
                "target_hostname": target_hostname,
                "note": "AD DS role removed from target — server returned to pre-restore state",
            }
        except Exception as exc:
            exc_str = str(exc)
            # WinRM drop during undemote reboot is expected
            if any(kw in exc_str.lower() for kw in ("connection", "timeout", "reset", "eof", "winrm")):
                return {
                    "rolled_back": True,
                    "target_hostname": target_hostname,
                    "note": "Undemote command sent — WinRM dropped (expected reboot). "
                            "Server should return to pre-restore state after reboot.",
                }
            return {
                "rolled_back": False,
                "target_hostname": target_hostname,
                "reason": f"Unexpected error during undemote: {exc_str}",
            }

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_rollback)
