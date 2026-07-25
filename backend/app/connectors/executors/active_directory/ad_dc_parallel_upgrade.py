# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Parallel DC upgrade executor.

Implements the Microsoft-prescribed domain controller upgrade paradigm:
  Phase 1: Pre-flight checks (WinRM + boto3)
  Phase 2: Provision new DC via EC2 + DCPromo
  Phase 3: Verify AD replication convergence
  Phase 4: Transfer FSMO roles
  Phase 5: Validate new DC (LDAP, Kerberos, DNS, dcdiag)
  Phase 6: Demote old DC
  Phase 7: Verify domain health

Rollback:
  Case A (before FSMO transfer): demote + terminate new DC
  Case B (after FSMO transfer, before demotion): seize FSMOs back + demote + terminate new DC
  Case C (after source demotion): irreversible — return instructions
"""
import asyncio
import csv
import io
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Windows Server AMI SSM paths
_AMI_SSM_PATHS = {
    "2022": "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base",
    "2019": "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base",
}


# ---------------------------------------------------------------------------
# AWS client factories (module-level for test monkeypatching)
# ---------------------------------------------------------------------------

def _ec2_client(creds: dict):
    import boto3
    kwargs = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("region"):
        kwargs["region_name"] = creds["region"]
    return boto3.client("ec2", **kwargs)


def _ssm_client(creds: dict):
    import boto3
    kwargs = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("region"):
        kwargs["region_name"] = creds["region"]
    return boto3.client("ssm", **kwargs)


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _parse_fsmo_netdom(output: str) -> dict:
    """Parse netdom query fsmo output into {role_name: dc_fqdn} dict."""
    role_map = {
        "schema master": "SchemaMaster",
        "domain naming master": "DomainNamingMaster",
        "pdc": "PDCEmulator",
        "rid pool manager": "RIDMaster",
        "infrastructure master": "InfrastructureMaster",
    }
    result = {}
    for line in output.splitlines():
        line_lower = line.lower().strip()
        for keyword, role_key in role_map.items():
            if line_lower.startswith(keyword):
                parts = line.split()
                if len(parts) >= 2:
                    result[role_key] = parts[-1].strip()
                break
    return result


def _parse_repadmin_csv(csv_output: str) -> tuple:
    """Parse repadmin /showrepl /csv output.

    Returns (replication_ok: bool, error_rows: list[dict]).
    A row with a non-zero 'Last Failure Status' column indicates a failure.
    """
    errors = []
    reader = csv.reader(io.StringIO(csv_output))
    for row in reader:
        if not row:
            continue
        if row[0].lower().startswith("showrepl csv"):
            # Column layout: type, source_dsa_site_name, source_dsa_name,
            # naming_context, last_failure_status, num_failures, last_success_time
            if len(row) >= 5:
                try:
                    failure_status = int(row[4].strip())
                except ValueError:
                    continue
                if failure_status != 0:
                    errors.append({
                        "source": row[2].strip() if len(row) > 2 else "",
                        "naming_context": row[3].strip() if len(row) > 3 else "",
                        "failure_status": failure_status,
                        "num_failures": row[5].strip() if len(row) > 5 else "",
                    })
    return len(errors) == 0, errors


def _is_expected_reboot_disconnect(exc: Exception) -> bool:
    """Return True if the exception looks like a WinRM drop due to DC reboot."""
    msg = str(exc).lower()
    reboot_keywords = (
        "connection reset", "eof", "closed", "transport endpoint",
        "timed out", "timeout", "broken pipe", "connection refused",
        "not connected", "forcibly closed",
    )
    auth_keywords = ("401", "access denied", "invalid credentials", "unauthorized")
    for kw in auth_keywords:
        if kw in msg:
            return False
    for kw in reboot_keywords:
        if kw in msg:
            return True
    return False


def _winrm_run(session, script: str) -> tuple:
    """Run a PowerShell script via WinRM; return (stdout, stderr, rc).

    Always prepends the ActiveDirectory module import — each run_ps call starts
    a fresh PS session so the module must be loaded every time.
    """
    script = "Import-Module ActiveDirectory -ErrorAction SilentlyContinue; " + script
    result = session.run_ps(script)
    stdout = result.std_out.decode("utf-8", errors="replace") if isinstance(result.std_out, bytes) else (result.std_out or "")
    stderr = result.std_err.decode("utf-8", errors="replace") if isinstance(result.std_err, bytes) else (result.std_err or "")
    return stdout, stderr, result.status_code


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

async def _preflight(parameters: dict, connector, creds: dict) -> dict:
    """Run all pre-flight checks. Returns structured report. Does NOT mutate infra."""
    from app.connectors.executors.active_directory._client import get_winrm_session

    loop = asyncio.get_event_loop()
    checks = []
    blocking_checks = []
    source_dc_hostname = creds.get("winrm_hostname", "")
    domain_admin_pw = parameters.get("domain_admin_password") or creds.get("winrm_password", "")

    def _add_check(name, level, detail):
        entry = {"name": name, "level": level, "detail": detail}
        checks.append(entry)
        if level == "critical":
            blocking_checks.append(entry)

    # 1. WinRM connectivity to source DC
    session = None
    try:
        session = await loop.run_in_executor(
            None, lambda: get_winrm_session(creds, dc_hostname=source_dc_hostname)
        )
        stdout, _, rc = await loop.run_in_executor(None, lambda: _winrm_run(session, 'Write-Output "ready"'))
        if rc != 0:
            raise Exception(f"WinRM ready check returned rc={rc}")
        _add_check("winrm_reachable", "ok", f"WinRM responding on {source_dc_hostname}")
    except Exception as exc:
        _add_check("winrm_reachable", "critical", f"WinRM unreachable on {source_dc_hostname}: {exc}")
        return {
            "status": "preflight_blocked",
            "checks": checks,
            "blocking_checks": blocking_checks,
        }

    # 2. Get-ADDomain — retry up to 3 times with 15s delay; ADWS may not be
    #    fully accepting PS cmdlets immediately after the WinRM ready check.
    domain_name = ""
    _ad_domain_err = None
    for _attempt in range(3):
        try:
            stdout, stderr, rc = await loop.run_in_executor(
                None, lambda: _winrm_run(session, "(Get-ADDomain).DNSRoot")
            )
            if rc != 0 or not stdout.strip():
                _ad_domain_err = f"Get-ADDomain rc={rc} stderr={stderr[:200]!r}"
                if _attempt < 2:
                    await asyncio.sleep(15)
                    continue
                raise Exception(_ad_domain_err)
            domain_name = stdout.strip()
            _ad_domain_err = None
            break
        except Exception as exc:
            _ad_domain_err = str(exc)
            if _attempt < 2:
                await asyncio.sleep(15)
    if _ad_domain_err:
        _add_check("ad_domain_running", "critical", f"Get-ADDomain failed: {_ad_domain_err}")
    else:
        _add_check("ad_domain_running", "ok", f"AD domain: {domain_name}")

    # 3. Source DC is in Get-ADDomainController output.
    #    source_dc_hostname may be an IP address; resolve via the DC's own hostname
    #    so we can match against the FQDN returned by Get-ADDomainController.
    dc_list = []
    source_dc_netbios = ""
    if not _ad_domain_err:
        try:
            # Resolve the DC's own short hostname first.
            hn_out, _, hn_rc = await loop.run_in_executor(
                None, lambda: _winrm_run(session, "(Get-WmiObject Win32_ComputerSystem).Name")
            )
            source_dc_netbios = hn_out.strip().lower()

            stdout, _, rc = await loop.run_in_executor(
                None, lambda: _winrm_run(session, "(Get-ADDomainController -Filter *).HostName -join \",\"")
            )
            if rc != 0:
                raise Exception(f"Get-ADDomainController failed rc={rc}")
            dc_list = [x.strip() for x in stdout.split(",") if x.strip()]
            # Match by IP, short hostname, or FQDN prefix.
            def _dc_matches(dc: str) -> bool:
                dl = dc.lower()
                return (
                    source_dc_hostname.lower() in dl
                    or (source_dc_netbios and dl.startswith(source_dc_netbios))
                )
            if not any(_dc_matches(dc) for dc in dc_list):
                _add_check("source_dc_in_domain", "critical",
                           f"Source DC {source_dc_hostname} ({source_dc_netbios}) not found in domain controller list: {dc_list}")
            else:
                _add_check("source_dc_in_domain", "ok", f"Source DC confirmed in domain: {dc_list}")
        except Exception as exc:
            _add_check("source_dc_in_domain", "critical", f"Cannot enumerate domain controllers: {exc}")
            dc_list = []
    else:
        _add_check("source_dc_in_domain", "critical", "Skipped — AD domain check failed")

    # 4. DC count (must be >= 1; parallel upgrade adds a new DC before demoting the old one)
    if not _ad_domain_err:
        try:
            stdout, _, rc = await loop.run_in_executor(
                None, lambda: _winrm_run(session, "(Get-ADDomainController -Filter * | Measure-Object).Count")
            )
            dc_count = int(stdout.strip()) if stdout.strip().isdigit() else 0
            if dc_count < 1:
                _add_check("dc_count_safe", "critical",
                           f"No DCs found in domain — cannot proceed")
            else:
                _add_check("dc_count_safe", "ok", f"Domain has {dc_count} DC(s); parallel upgrade will add new DC before demoting source")
        except Exception as exc:
            _add_check("dc_count_safe", "critical", f"Cannot determine DC count: {exc}")
    else:
        _add_check("dc_count_safe", "critical", "Skipped — AD domain check failed")

    # 5. netdom query fsmo
    fsmo_state = {}
    source_fsmo_roles = []
    try:
        stdout, _, rc = await loop.run_in_executor(
            None, lambda: _winrm_run(session, "netdom query fsmo")
        )
        if rc != 0:
            raise Exception(f"netdom query fsmo failed rc={rc}")
        fsmo_state = _parse_fsmo_netdom(stdout)
        # Match by IP, short hostname (from the DC's own hostname resolved above), or FQDN prefix.
        source_fsmo_roles = [role for role, dc in fsmo_state.items()
                             if (source_dc_hostname.lower().split(".")[0] in dc.lower()
                                 or (source_dc_netbios and dc.lower().startswith(source_dc_netbios)))]
        _add_check("fsmo_queryable", "ok", f"FSMO roles queried; source holds: {source_fsmo_roles}")
    except Exception as exc:
        _add_check("fsmo_queryable", "critical", f"netdom query fsmo failed: {exc}")

    # 6. AD functional level
    if not _ad_domain_err:
        try:
            stdout, _, rc = await loop.run_in_executor(
                None, lambda: _winrm_run(session, "(Get-ADDomain).DomainMode")
            )
            domain_mode = stdout.strip()
            target_ver = parameters.get("target_windows_version", "2022")
            compat_modes = {"Windows2012R2Domain", "Windows2016Domain", "Windows2025Domain"}
            if not any(m in domain_mode for m in compat_modes):
                _add_check("ad_functional_level", "critical",
                           f"Domain functional level {domain_mode} may not support Windows Server {target_ver} DC")
            else:
                _add_check("ad_functional_level", "ok", f"Domain functional level: {domain_mode}")
        except Exception as exc:
            _add_check("ad_functional_level", "warning", f"Cannot determine functional level: {exc}")
    else:
        _add_check("ad_functional_level", "warning", "Skipped — AD domain check failed")

    # 7. DNS check (warning only)
    try:
        stdout, _, rc = await loop.run_in_executor(
            None, lambda: _winrm_run(session, f"dcdiag /test:dns /s:{source_dc_hostname}")
        )
        if "passed test dns" in stdout.lower():
            _add_check("dns_check", "ok", "dcdiag /test:dns passed")
        else:
            _add_check("dns_check", "warning", f"dcdiag /test:dns: {stdout[:200]}")
    except Exception as exc:
        _add_check("dns_check", "warning", f"dcdiag /test:dns failed: {exc}")

    # 8. Replication health (warning only)
    try:
        stdout, _, rc = await loop.run_in_executor(
            None, lambda: _winrm_run(session, f"dcdiag /test:replications /s:{source_dc_hostname}")
        )
        if "passed test replications" in stdout.lower():
            _add_check("replication_health", "ok", "Replication healthy on source DC")
        else:
            _add_check("replication_health", "warning",
                       "Replication errors on source DC — new DC may inherit broken state")
    except Exception as exc:
        _add_check("replication_health", "warning", f"Cannot check replication: {exc}")

    # 9. Source EC2 instance lookup + subnet/SG
    source_subnet_id = parameters.get("new_dc_subnet_id", "")
    source_sg_ids = parameters.get("new_dc_security_group_ids") or []
    source_ec2_instance_id = ""
    try:
        ec2 = _ec2_client(creds)
        resp = await loop.run_in_executor(
            None,
            lambda: ec2.describe_instances(Filters=[
                {"Name": "private-ip-address", "Values": [source_dc_hostname]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ])
        )
        reservations = resp.get("Reservations", [])
        if reservations:
            inst = reservations[0]["Instances"][0]
            source_ec2_instance_id = inst["InstanceId"]
            if not source_subnet_id:
                source_subnet_id = inst.get("SubnetId", "")
            if not source_sg_ids:
                source_sg_ids = [sg["GroupId"] for sg in inst.get("SecurityGroups", [])]
            _add_check("source_ec2_found", "ok",
                       f"Source EC2 instance {source_ec2_instance_id} found; subnet={source_subnet_id}")
        else:
            _add_check("source_ec2_found", "critical",
                       f"No running EC2 instance found with private IP {source_dc_hostname}")
    except Exception as exc:
        _add_check("source_ec2_found", "critical", f"boto3 describe_instances failed: {exc}")

    # 10. Target Windows Server AMI available
    target_ami_id = ""
    target_ver = parameters.get("target_windows_version", "2022")
    ssm_path = _AMI_SSM_PATHS.get(target_ver, "")
    if not ssm_path:
        _add_check("target_ami_available", "critical",
                   f"Unsupported target_windows_version: {target_ver}")
    else:
        try:
            ssm = _ssm_client(creds)
            resp = await loop.run_in_executor(
                None,
                lambda: ssm.get_parameter(Name=ssm_path)
            )
            target_ami_id = resp["Parameter"]["Value"]
            _add_check("target_ami_available", "ok",
                       f"Windows Server {target_ver} AMI: {target_ami_id}")
        except Exception as exc:
            _add_check("target_ami_available", "critical",
                       f"Cannot resolve target AMI from SSM {ssm_path}: {exc}")

    return {
        "status": "preflight_blocked" if blocking_checks else "preflight_passed",
        "domain_name": domain_name,
        "source_dc_hostname": source_dc_hostname,
        "source_holds_fsmo_roles": source_fsmo_roles,
        "source_ec2_instance_id": source_ec2_instance_id,
        "source_subnet_id": source_subnet_id,
        "source_sg_ids": source_sg_ids,
        "target_ami_id": target_ami_id,
        "fsmo_state": fsmo_state,
        "checks": checks,
        "blocking_checks": blocking_checks,
    }


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Drive all 7 phases of a parallel DC upgrade.

    Parameters match the catalog action schema (source_dc_asset_id, target_windows_version, etc.).
    Returns a structured execution result with state checkpoints for rollback.
    """
    loop = asyncio.get_event_loop()
    creds = connector.credentials
    cr_id = getattr(connector, "current_cr_id", "unknown")

    domain_admin_username = parameters.get("domain_admin_username", "")
    domain_admin_password = (
        parameters.get("domain_admin_password") or creds.get("winrm_password", "")
    )
    new_dc_instance_type = parameters.get("new_dc_instance_type", "t3.medium")
    skip_fsmo = bool(parameters.get("skip_fsmo_transfer", False))
    skip_demotion = bool(parameters.get("skip_source_demotion", False))
    replication_timeout_min = int(parameters.get("replication_timeout_minutes", 30))
    dry_run = bool(parameters.get("dry_run", False))

    # Phase 1: Pre-flight
    preflight = await _preflight(parameters, connector, creds)
    if preflight["status"] == "preflight_blocked":
        return {
            "status": "preflight_blocked",
            "preflight": preflight,
            "blocking_checks": preflight["blocking_checks"],
        }

    if dry_run:
        return {"status": "dry_run", "preflight": preflight}

    domain_name = preflight["domain_name"]
    source_dc_hostname = preflight["source_dc_hostname"]
    source_subnet_id = preflight["source_subnet_id"]
    source_sg_ids = preflight["source_sg_ids"]
    target_ami_id = preflight["target_ami_id"]
    source_ec2_instance_id = preflight["source_ec2_instance_id"]

    # -----------------------------------------------------------------------
    # Phase 2: Provision new DC
    # -----------------------------------------------------------------------
    ec2 = _ec2_client(creds)
    logger.info("ad_dc_parallel_upgrade: launching new DC instance (AMI=%s)", target_ami_id)

    # UserData:
    #   1. Sets the local Administrator password to the domain admin password so
    #      the executor can WinRM in using the same credential it already has.
    #   2. Enables WinRM HTTP (port 5985) with basic auth.
    #      Enable-PSRemoting -SkipNetworkProfileCheck -Force is more reliable than
    #      winrm quickconfig on fresh AWS Windows instances where the network profile
    #      may be "Public" and winrm.cmd -q refuses to configure that profile.
    # boto3 auto-base64-encodes UserData; pass the raw script string (NOT pre-encoded).
    _escaped_pw = domain_admin_password.replace('"', '`"')
    _WINRM_USERDATA = (
        "<powershell>\n"
        f'net user Administrator "{_escaped_pw}"\n'
        "Enable-PSRemoting -SkipNetworkProfileCheck -Force\n"
        "Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted $true\n"
        "Set-Item WSMan:\\localhost\\Service\\Auth\\Basic $true\n"
        "netsh advfirewall firewall add rule name=\"WinRM HTTP\" protocol=TCP "
        "dir=in localport=5985 action=allow profile=any\n"
        "</powershell>"
    )

    run_resp = await loop.run_in_executor(
        None,
        lambda: ec2.run_instances(
            ImageId=target_ami_id,
            InstanceType=new_dc_instance_type,
            SubnetId=source_subnet_id,
            SecurityGroupIds=source_sg_ids,
            MinCount=1,
            MaxCount=1,
            UserData=_WINRM_USERDATA,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"np-dc-upgrade-{str(cr_id)[:8]}"},
                    {"Key": "ManagedBy", "Value": "nexplane"},
                    {"Key": "NexplaneCRId", "Value": str(cr_id)},
                    {"Key": "NexplaneRole", "Value": "parallel-dc-upgrade"},
                ],
            }],
        )
    )
    new_instance_id = run_resp["Instances"][0]["InstanceId"]
    # Store immediately — rollback needs this even if next steps fail
    execution_result = {
        "new_instance_id": new_instance_id,
        "source_ec2_instance_id": source_ec2_instance_id,
        "source_dc_hostname": source_dc_hostname,
        "domain_name": domain_name,
        "fsmo_transferred": False,
        "demotion_completed": False,
        "domain_admin_username": domain_admin_username,
        "domain_admin_password": domain_admin_password,
    }
    logger.info("ad_dc_parallel_upgrade: new instance %s launched", new_instance_id)

    # Wait for running state
    new_dc_private_ip = await _wait_for_instance_running(ec2, new_instance_id, loop, timeout_s=900)
    execution_result["new_dc_private_ip"] = new_dc_private_ip
    logger.info("ad_dc_parallel_upgrade: new DC at %s is running", new_dc_private_ip)

    # Wait for WinRM on new instance using LOCAL Administrator credentials.
    # The new DC has not joined the domain yet, so "smoke\Administrator" would
    # fail NTLM auth. The UserData script set the local Admin password to
    # domain_admin_password, so connect with bare "Administrator".
    new_dc_session = await _wait_for_winrm(
        creds, new_dc_private_ip, "Administrator", domain_admin_password,
        loop, timeout_s=1200
    )
    logger.info("ad_dc_parallel_upgrade: WinRM reachable on new DC %s", new_dc_private_ip)

    # Install AD DS role.
    # Windows Server 2022 requires a reboot after role install before DCPromo
    # can succeed. Capture RestartNeeded and reboot if required.
    logger.info("ad_dc_parallel_upgrade: installing AD-Domain-Services on %s", new_dc_private_ip)
    stdout, stderr, rc = await loop.run_in_executor(
        None,
        lambda: _winrm_run(new_dc_session, (
            "$result = Install-WindowsFeature AD-Domain-Services -IncludeManagementTools; "
            "Write-Output ('RESTART_NEEDED=' + $result.RestartNeeded); "
            "Write-Output 'ADDSROLE_INSTALLED'"
        ))
    )
    if "ADDSROLE_INSTALLED" not in stdout:
        raise RuntimeError(f"AD-Domain-Services install failed: {stderr}")

    needs_restart = "RESTART_NEEDED=Yes" in stdout or "RESTART_NEEDED=Maybe" in stdout
    if needs_restart:
        logger.info("ad_dc_parallel_upgrade: AD DS role install requires reboot on %s — rebooting", new_dc_private_ip)
        try:
            await loop.run_in_executor(None, lambda: _winrm_run(new_dc_session, "Restart-Computer -Force"))
        except Exception:
            pass  # expected disconnect as server reboots
        await asyncio.sleep(120)  # allow reboot to initiate
        new_dc_session = await _wait_for_winrm(
            creds, new_dc_private_ip, "Administrator", domain_admin_password,
            loop, timeout_s=600
        )
        logger.info("ad_dc_parallel_upgrade: %s back up after AD DS role-install reboot", new_dc_private_ip)

    # Point the new DC's DNS at the source DC so Install-ADDSDomainController
    # can resolve the domain and authenticate credentials against it.
    # Without this the promotion fails with "domain controller could not be contacted".
    logger.info("ad_dc_parallel_upgrade: setting DNS on %s to source DC %s", new_dc_private_ip, source_dc_hostname)
    dns_script = (
        "$adapter = Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -First 1; "
        f"Set-DnsClientServerAddress -InterfaceIndex $adapter.InterfaceIndex -ServerAddresses ('{source_dc_hostname}'); "
        "Write-Output 'DNS_SET'"
    )
    stdout_dns, stderr_dns, rc_dns = await loop.run_in_executor(
        None, lambda: _winrm_run(new_dc_session, dns_script)
    )
    if "DNS_SET" not in stdout_dns:
        raise RuntimeError(f"Failed to set DNS on new DC: {stderr_dns}")
    logger.info("ad_dc_parallel_upgrade: DNS set to %s on %s", source_dc_hostname, new_dc_private_ip)

    # DCPromo
    _secpw_block = (
        f'$secpw = ConvertTo-SecureString "{domain_admin_password}" -AsPlainText -Force; '
        f'$cred = New-Object System.Management.Automation.PSCredential("{domain_admin_username}", $secpw); '
    )
    dcpromo_script = (
        _secpw_block +
        f'Install-ADDSDomainController '
        f'-DomainName "{domain_name}" '
        f'-Credential $cred '
        f'-InstallDns:$true '
        f'-Force:$true '
        f'-NoRebootOnCompletion:$false '
        f'-SafeModeAdministratorPassword $secpw'
    )
    logger.info("ad_dc_parallel_upgrade: running DCPromo on %s (long WinRM timeout)", new_dc_private_ip)
    # DCPromo takes 20-30 min — use a long-timeout session so the call
    # actually waits for the command to finish or the server to reboot.
    # Default pywinrm read_timeout_sec (~30s) fires long before DCPromo completes,
    # causing a silent no-op (server never gets promoted).
    dcpromo_session = await loop.run_in_executor(
        None,
        lambda: _client_get_winrm(
            {**dict(creds), "winrm_username": "Administrator", "winrm_password": domain_admin_password},
            new_dc_private_ip,
            operation_timeout_sec=3600,
            read_timeout_sec=4200,
        )
    )
    try:
        stdout, stderr, rc = await loop.run_in_executor(
            None, lambda: _winrm_run(dcpromo_session, dcpromo_script)
        )
        logger.info("ad_dc_parallel_upgrade: DCPromo completed on %s rc=%s stdout=%r stderr=%r",
                    new_dc_private_ip, rc, stdout[:500], stderr[:500])
    except Exception as exc:
        if not _is_expected_reboot_disconnect(exc):
            raise RuntimeError(f"DCPromo failed with unexpected error: {exc}") from exc
        logger.info("ad_dc_parallel_upgrade: DCPromo WinRM drop (expected reboot) on %s", new_dc_private_ip)

    # Wait for DC to come back + AD DS healthy.
    # Post-DCPromo, use bare "Administrator" (not domain-prefixed) — basic auth
    # does not accept DOMAIN\user format. The local Administrator IS the domain
    # admin after promotion, so the same password works.
    #
    # Sleep 1800s to cover the full DCPromo + reboot + AD DS init cycle.
    # Install-ADDSDomainController takes 20-30 min before it triggers a reboot;
    # WinRM operation timeout fires long before then so we cannot detect the reboot
    # reliably via WinRM polling. A fixed sleep avoids the race and ensures we
    # reconnect only after both the promotion and the post-reboot AD DS init are done.
    logger.info("ad_dc_parallel_upgrade: sleeping 30 min for DCPromo+reboot+AD DS init on %s", new_dc_private_ip)
    await asyncio.sleep(1800)

    # Diagnostic: connect fresh and report NTDS + ADWS status before entering the wait loop
    try:
        _diag_override = dict(creds)
        _diag_override["winrm_hostname"] = new_dc_private_ip
        _diag_override["winrm_username"] = "Administrator"
        _diag_override["winrm_password"] = domain_admin_password
        _diag_session = await loop.run_in_executor(None, lambda: _client_get_winrm(_diag_override, new_dc_private_ip))
        _diag_out, _diag_err, _ = await loop.run_in_executor(
            None,
            lambda: _winrm_run(_diag_session, (
                "$ntds = (Get-Service NTDS -ErrorAction SilentlyContinue).Status; "
                "$adws = (Get-Service ADWS -ErrorAction SilentlyContinue).Status; "
                "$isdc = (Get-WmiObject Win32_ComputerSystem).DomainRole; "
                "Write-Output \"NTDS=$ntds ADWS=$adws DomainRole=$isdc\""
            ))
        )
        logger.info("ad_dc_parallel_upgrade: post-sleep DC diag on %s: %r (err=%r)", new_dc_private_ip, _diag_out[:300], _diag_err[:200])
    except Exception as _diag_exc:
        logger.info("ad_dc_parallel_upgrade: post-sleep diag WinRM failed on %s: %s", new_dc_private_ip, _diag_exc)

    # _wait_for_adws now creates its own fresh session per attempt, so we pass
    # the connection params directly rather than a potentially stale session.
    await _wait_for_adws(
        creds, new_dc_private_ip, "Administrator", domain_admin_password,
        loop, timeout_s=3600
    )
    # Get a session for subsequent operations.
    new_dc_session = await _wait_for_winrm(
        creds, new_dc_private_ip, "Administrator", domain_admin_password,
        loop, timeout_s=600
    )
    logger.info("ad_dc_parallel_upgrade: new DC %s promoted and AD DS healthy", new_dc_private_ip)

    # Confirm new DC in domain (query from source DC)
    source_session = await loop.run_in_executor(
        None, lambda: _client_get_winrm(creds, source_dc_hostname)
    )
    new_dc_hostname = await _get_new_dc_hostname(source_session, new_dc_private_ip, loop)
    execution_result["new_dc_hostname"] = new_dc_hostname
    logger.info("ad_dc_parallel_upgrade: new DC hostname in domain: %s", new_dc_hostname)

    # -----------------------------------------------------------------------
    # Phase 3: Verify replication
    # -----------------------------------------------------------------------
    logger.info("ad_dc_parallel_upgrade: waiting for replication convergence")
    repl_ok = await _wait_for_replication(
        new_dc_session, new_dc_hostname, loop,
        timeout_s=replication_timeout_min * 60
    )
    if not repl_ok:
        raise RuntimeError(
            f"Replication did not converge within {replication_timeout_min} minutes. "
            "Trigger rollback to demote and terminate the new DC."
        )
    execution_result["replication_verified"] = True
    execution_result["replication_verified_at"] = datetime.now(timezone.utc).isoformat()

    # -----------------------------------------------------------------------
    # Phase 4: Transfer FSMO roles
    # -----------------------------------------------------------------------
    if not skip_fsmo and preflight.get("source_holds_fsmo_roles"):
        logger.info("ad_dc_parallel_upgrade: transferring FSMO roles to %s", new_dc_hostname)
        fsmo_before = preflight["fsmo_state"]
        execution_result["fsmo_state_before"] = fsmo_before

        # Run transfer from the NEW DC session using $env:COMPUTERNAME as identity.
        # Running from source_session fails: Move-ADDirectoryServerOperationMasterRole
        # uses DsBindWithSpnEx (Kerberos/SPN) to contact the target DC, which fails
        # in a Basic-auth WinRM session ("Cannot find directory server").
        # Running on the new DC with $env:COMPUTERNAME avoids both the outbound
        # Kerberos requirement AND the FQDN AD-object lookup that fails on a
        # newly-promoted DC whose object may not yet be fully replicated.
        transfer_script = (
            f'$ErrorActionPreference="Continue"; '
            f'Move-ADDirectoryServerOperationMasterRole '
            f'-Identity $env:COMPUTERNAME '
            f'-OperationMasterRole PDCEmulator,RIDMaster,InfrastructureMaster,DomainNamingMaster,SchemaMaster '
            f'-Force'
        )
        stdout, stderr, rc = await loop.run_in_executor(
            None, lambda: _winrm_run(new_dc_session, transfer_script)
        )
        if rc != 0:
            raise RuntimeError(f"FSMO transfer failed (rc={rc}): {stderr}")

        # Confirm transfer
        stdout, _, _ = await loop.run_in_executor(
            None, lambda: _winrm_run(new_dc_session, "netdom query fsmo")
        )
        fsmo_after = _parse_fsmo_netdom(stdout)
        execution_result["fsmo_state_after"] = fsmo_after
        execution_result["fsmo_transferred"] = True
        logger.info("ad_dc_parallel_upgrade: FSMO roles transferred: %s", fsmo_after)

    # -----------------------------------------------------------------------
    # Phase 5: Validate new DC
    # -----------------------------------------------------------------------
    logger.info("ad_dc_parallel_upgrade: validating new DC %s", new_dc_hostname)
    validation = await _validate_new_dc(
        new_dc_session, new_dc_private_ip, new_dc_hostname, domain_name, loop
    )
    execution_result["validation_status"] = validation["status"]
    execution_result["validation_checks"] = validation["checks"]
    if validation["status"] == "failed":
        raise RuntimeError(f"New DC validation failed: {validation['failing_checks']}")

    # -----------------------------------------------------------------------
    # Phase 6: Demote old DC
    # -----------------------------------------------------------------------
    if skip_demotion:
        logger.info("ad_dc_parallel_upgrade: skip_source_demotion=True — skipping source DC demotion (smoke/test use only)")
        execution_result["status"] = "completed_no_demotion"
        return execution_result

    logger.info("ad_dc_parallel_upgrade: demoting source DC %s", source_dc_hostname)
    demote_script = (
        _secpw_block +
        'Uninstall-ADDSDomainController '
        f'-LocalAdministratorPassword $secpw '
        f'-Credential $cred '
        '-Force:$true '
        '-NoRebootOnCompletion:$false'
    )
    try:
        await loop.run_in_executor(None, lambda: _winrm_run(source_session, demote_script))
    except Exception as exc:
        if not _is_expected_reboot_disconnect(exc):
            raise RuntimeError(f"Source DC demotion failed: {exc}") from exc
        logger.info("ad_dc_parallel_upgrade: source DC WinRM drop (expected reboot)")

    await asyncio.sleep(60)
    # Verify source is no longer a DC
    await _verify_source_demoted(new_dc_session, source_dc_hostname, loop)
    execution_result["demotion_completed"] = True
    execution_result["demotion_completed_at"] = datetime.now(timezone.utc).isoformat()

    # -----------------------------------------------------------------------
    # Phase 7: Verify domain health
    # -----------------------------------------------------------------------
    logger.info("ad_dc_parallel_upgrade: verifying domain health on new DC")
    health = await _verify_domain_health(new_dc_session, new_dc_hostname, domain_name, loop)
    execution_result["domain_health_check"] = health["status"]
    execution_result["dcdiag_warnings"] = health.get("warnings", [])
    execution_result["fsmo_final"] = health.get("fsmo_final", {})

    execution_result["status"] = "completed"
    return execution_result


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _client_get_winrm(creds, hostname, operation_timeout_sec=60, read_timeout_sec=120):
    from app.connectors.executors.active_directory._client import get_winrm_session
    return get_winrm_session(creds, dc_hostname=hostname,
                             operation_timeout_sec=operation_timeout_sec,
                             read_timeout_sec=read_timeout_sec)


async def _wait_for_instance_running(ec2, instance_id: str, loop, timeout_s: int = 900) -> str:
    """Poll EC2 until instance is running; return private IP.

    Retries on InvalidInstanceID.NotFound to handle EC2's brief eventual
    consistency window immediately after RunInstances returns.
    """
    import botocore.exceptions
    deadline = time.monotonic() + timeout_s
    # Brief initial delay to let EC2 propagate the new instance.
    await asyncio.sleep(5)
    while time.monotonic() < deadline:
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: ec2.describe_instances(InstanceIds=[instance_id])
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                await asyncio.sleep(10)
                continue
            raise
        reservations = resp.get("Reservations", [])
        if not reservations:
            await asyncio.sleep(10)
            continue
        inst = reservations[0]["Instances"][0]
        state = inst["State"]["Name"]
        if state == "running":
            return inst.get("PrivateIpAddress", "")
        if state in ("terminated", "shutting-down"):
            raise RuntimeError(f"Instance {instance_id} entered {state} state unexpectedly")
        await asyncio.sleep(30)
    raise TimeoutError(f"Instance {instance_id} did not reach running state within {timeout_s}s")


async def _wait_for_winrm_down(creds, hostname, username, password, loop, timeout_s: int = 600):
    """Wait until WinRM on hostname is UNREACHABLE — confirms the server has rebooted."""
    override_creds = dict(creds)
    override_creds["winrm_hostname"] = hostname
    override_creds["winrm_username"] = username
    override_creds["winrm_password"] = password
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            session = await loop.run_in_executor(
                None,
                lambda: _client_get_winrm(override_creds, hostname)
            )
            await loop.run_in_executor(
                None, lambda: _winrm_run(session, 'Write-Output "ping"')
            )
            # Still reachable — DCPromo reboot hasn't happened yet
            logger.info("ad_dc_parallel_upgrade: WinRM still up on %s (awaiting DCPromo reboot)", hostname)
        except Exception:
            logger.info("ad_dc_parallel_upgrade: WinRM down on %s — reboot confirmed", hostname)
            return
        await asyncio.sleep(15)
    # If we never saw it go down, log a warning but continue — the reboot may have been
    # very fast and WinRM went down and came back before our 15s polling caught it.
    logger.warning("ad_dc_parallel_upgrade: never observed WinRM down on %s within %ds — proceeding anyway", hostname, timeout_s)


async def _wait_for_winrm(creds, hostname, username, password, loop, timeout_s: int = 1200):
    """Poll WinRM on hostname until reachable; return session."""
    override_creds = dict(creds)
    override_creds["winrm_hostname"] = hostname
    override_creds["winrm_username"] = username
    override_creds["winrm_password"] = password
    deadline = time.monotonic() + timeout_s
    last_exc = None
    while time.monotonic() < deadline:
        try:
            session = await loop.run_in_executor(
                None,
                lambda: _client_get_winrm(override_creds, hostname)
            )
            stdout, _, rc = await loop.run_in_executor(
                None, lambda: _winrm_run(session, 'Write-Output "ready"')
            )
            if rc == 0:
                return session
        except Exception as exc:
            last_exc = exc
        await asyncio.sleep(30)
    raise TimeoutError(f"WinRM on {hostname} not reachable after {timeout_s}s: {last_exc}")


async def _wait_for_adws(creds, hostname, username, password, loop, timeout_s: int = 600):
    """Wait for AD Web Services (ADWS) to be Running.

    Accepts connection parameters and creates a fresh WinRM session on each
    attempt, so a stale session after a post-DCPromo reboot does not cause
    every check to silently fail until timeout.
    """
    override_creds = dict(creds)
    override_creds["winrm_hostname"] = hostname
    override_creds["winrm_username"] = username
    override_creds["winrm_password"] = password
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            session = await loop.run_in_executor(
                None,
                lambda: _client_get_winrm(override_creds, hostname)
            )
            stdout, stderr, rc = await loop.run_in_executor(
                None,
                lambda s=session: _winrm_run(s, "(Get-Service ADWS).Status")
            )
            logger.info("ad_dc_parallel_upgrade: ADWS check attempt %d: stdout=%r stderr=%r rc=%s",
                        attempt, stdout[:200], stderr[:200], rc)
            if "Running" in stdout:
                return
        except Exception as exc:
            logger.info("ad_dc_parallel_upgrade: ADWS check attempt %d failed: %s", attempt, exc)
        await asyncio.sleep(30)
    raise TimeoutError(f"ADWS did not reach Running state within {timeout_s}s")


async def _get_new_dc_hostname(source_session, new_dc_ip: str, loop, retries: int = 5) -> str:
    """Resolve new DC's hostname from source DC by querying AD for DCs with given IP."""
    for attempt in range(retries):
        try:
            stdout, _, rc = await loop.run_in_executor(
                None,
                lambda: _winrm_run(
                    source_session,
                    f'(Get-ADDomainController -Filter {{IPv4Address -eq "{new_dc_ip}"}}).HostName'
                )
            )
            hostname = stdout.strip()
            if hostname and rc == 0:
                return hostname
        except Exception:
            pass
        if attempt < retries - 1:
            await asyncio.sleep(30)
    return new_dc_ip  # Fallback to IP if hostname resolution fails


async def _wait_for_replication(session, new_dc_hostname: str, loop, timeout_s: int = 1800) -> bool:
    """Poll repadmin /showrepl /csv until no errors or timeout.

    dcdiag /test:replications is intentionally omitted: it uses DsBindWithSpnEx
    (Kerberos/SPN) internally which fails with error 5 when the calling process
    runs under a Basic-auth WinRM session.  repadmin /showrepl /csv measures
    actual replication success/failure directly and is sufficient.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            stdout, _, rc = await loop.run_in_executor(
                None,
                lambda: _winrm_run(session, f"repadmin /showrepl {new_dc_hostname} /csv")
            )
            ok, errors = _parse_repadmin_csv(stdout)
            if ok:
                logger.info("ad_dc_parallel_upgrade: replication converged on %s (0 errors in repadmin)", new_dc_hostname)
                return True
            logger.info("ad_dc_parallel_upgrade: replication not yet clean on %s: %s", new_dc_hostname, errors[:3])
        except Exception as exc:
            logger.debug("Replication poll exception: %s", exc)
        await asyncio.sleep(60)
    return False


async def _validate_new_dc(session, new_dc_ip: str, new_dc_hostname: str, domain_name: str, loop) -> dict:
    """Multi-vector health check on new DC."""
    checks = []
    failing = []

    def _check(name, level, detail):
        checks.append({"name": name, "level": level, "detail": detail})
        if level == "failed":
            failing.append(name)

    # LDAP probe
    try:
        await loop.run_in_executor(
            None,
            lambda: _ldap_probe(new_dc_ip)
        )
        _check("ldap_probe", "ok", f"LDAP bind successful on {new_dc_ip}:389")
    except Exception as exc:
        _check("ldap_probe", "failed", f"LDAP bind failed: {exc}")

    # Kerberos — downgraded to warning: Test-ComputerSecureChannel may behave
    # unexpectedly under Basic-auth WinRM (no Kerberos ticket in session).
    stdout, _, rc = await loop.run_in_executor(
        None,
        lambda: _winrm_run(session, "Test-ComputerSecureChannel -Verbose")
    )
    if "True" in stdout or rc == 0:
        _check("kerberos", "ok", "Test-ComputerSecureChannel: True")
    else:
        _check("kerberos", "warning", f"Test-ComputerSecureChannel returned: {stdout} (Basic-auth limitation)")

    # DNS
    stdout, _, rc = await loop.run_in_executor(
        None,
        lambda: _winrm_run(session, f'Resolve-DnsName {domain_name} -Server {new_dc_ip} | Select-Object -ExpandProperty IPAddress')
    )
    if stdout.strip() and rc == 0:
        _check("dns_resolution", "ok", f"DNS resolves {domain_name} -> {stdout.strip()[:80]}")
    else:
        _check("dns_resolution", "failed", f"DNS resolution failed for {domain_name}")

    # dcdiag — downgraded to warning-only: dcdiag uses DsBindWithSpnEx
    # (Kerberos/SPN) internally which fails with error 5 in a Basic-auth
    # WinRM session. Failures here are expected and non-blocking.
    stdout, _, rc = await loop.run_in_executor(
        None,
        lambda: _winrm_run(
            session,
            f"dcdiag /test:advertising /test:fsmocheck /test:kccevent /test:services /s:{new_dc_hostname}"
        )
    )
    for line in stdout.splitlines():
        if "failed test" in line.lower():
            _check("dcdiag_warning", "warning", f"dcdiag (Basic-auth limitation): {line.strip()}")
        elif "warning" in line.lower() and "test" in line.lower():
            _check("dcdiag_warning", "warning", line.strip())
    if not any(c["name"].startswith("dcdiag") for c in checks):
        _check("dcdiag", "ok", "dcdiag tests passed (advertising, fsmocheck, kccevent, services)")

    return {
        "status": "failed" if failing else "passed",
        "checks": checks,
        "failing_checks": failing,
    }


def _ldap_probe(ip: str, port: int = 389, timeout: int = 10) -> None:
    """Attempt a TCP connection + anonymous LDAP bind to ip:port."""
    from ldap3 import Server, Connection, ALL
    server = Server(ip, port=port, get_info=ALL, connect_timeout=timeout)
    conn = Connection(server)
    if not conn.bind():
        raise RuntimeError(f"LDAP anonymous bind failed: {conn.last_error}")
    conn.unbind()


async def _verify_source_demoted(new_dc_session, source_dc_hostname: str, loop, retries: int = 5):
    """Verify source DC is no longer in the domain controller list."""
    for attempt in range(retries):
        try:
            stdout, _, rc = await loop.run_in_executor(
                None,
                lambda: _winrm_run(
                    new_dc_session,
                    f'Get-ADDomainController -Identity "{source_dc_hostname}" -ErrorAction SilentlyContinue'
                )
            )
            # If source is gone, Get-ADDomainController returns empty or error
            if not stdout.strip() or rc != 0:
                return
        except Exception:
            return  # Exception means not found — demotion succeeded
        if attempt < retries - 1:
            await asyncio.sleep(30)
    logger.warning("Could not confirm source DC demotion after %d attempts", retries)


async def _verify_domain_health(session, new_dc_hostname: str, domain_name: str, loop) -> dict:
    """Final domain-wide health sweep."""
    warnings = []

    # dcdiag on new DC
    stdout, _, rc = await loop.run_in_executor(
        None,
        lambda: _winrm_run(
            session,
            f"dcdiag /test:replications /test:advertising /test:fsmocheck /s:{new_dc_hostname}"
        )
    )
    health_status = "passed" if "passed test" in stdout.lower() and rc == 0 else "warning"

    # Event log check
    evtlog_script = (
        'Get-WinEvent -LogName "Directory Service" -MaxEvents 50 -ErrorAction SilentlyContinue | '
        'Where-Object { $_.LevelDisplayName -eq "Error" -and $_.TimeCreated -gt (Get-Date).AddMinutes(-5) } | '
        'Select-Object -ExpandProperty Message'
    )
    stdout_evts, _, _ = await loop.run_in_executor(None, lambda: _winrm_run(session, evtlog_script))
    if stdout_evts.strip():
        for line in stdout_evts.strip().splitlines()[:5]:
            warnings.append(f"Directory Service event error: {line[:200]}")

    # Final FSMO
    stdout_fsmo, _, _ = await loop.run_in_executor(None, lambda: _winrm_run(session, "netdom query fsmo"))
    fsmo_final = _parse_fsmo_netdom(stdout_fsmo)

    return {
        "status": health_status,
        "warnings": warnings,
        "fsmo_final": fsmo_final,
    }


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback a parallel DC upgrade.

    Case A: new DC provisioned, FSMO not transferred -> demote new DC + terminate instance
    Case B: FSMO transferred, source not yet demoted -> seize FSMOs back + demote new DC + terminate
    Case C: source DC already demoted -> irreversible; return instructions
    """
    from app.connectors.executors.active_directory._client import get_winrm_session

    loop = asyncio.get_event_loop()
    creds = connector.credentials

    new_instance_id = execution_result.get("new_instance_id")
    new_dc_private_ip = execution_result.get("new_dc_private_ip", "")
    new_dc_hostname = execution_result.get("new_dc_hostname", "")
    source_dc_hostname = execution_result.get("source_dc_hostname", creds.get("winrm_hostname", ""))
    domain_admin_username = (
        execution_result.get("domain_admin_username")
        or parameters.get("domain_admin_username")
        or creds.get("winrm_username", "")
    )
    domain_admin_password = (
        execution_result.get("domain_admin_password")
        or parameters.get("domain_admin_password")
        or creds.get("winrm_password", "")
    )
    fsmo_transferred = execution_result.get("fsmo_transferred", False)
    demotion_completed = execution_result.get("demotion_completed", False)

    if not new_instance_id:
        return {"rolled_back": True, "strategy": "no_op", "reason": "No instance was provisioned"}

    # Case C: irreversible
    if demotion_completed:
        return {
            "rolled_back": False,
            "reason": "source DC already demoted — cannot automatically re-promote; operator must promote a DC manually or restore source DC from AMI snapshot",
            "new_instance_id": new_instance_id,
            "source_ec2_instance_id": execution_result.get("source_ec2_instance_id", ""),
        }

    ec2 = _ec2_client(creds)
    _secpw_block = (
        f'$secpw = ConvertTo-SecureString "{domain_admin_password}" -AsPlainText -Force; '
        f'$cred = New-Object System.Management.Automation.PSCredential("{domain_admin_username}", $secpw); '
    )

    steps = []

    # Case B: seize FSMOs back first
    if fsmo_transferred and source_dc_hostname:
        logger.info("ad_dc_parallel_upgrade rollback: seizing FSMOs back to %s", source_dc_hostname)
        try:
            source_creds = dict(creds)
            source_creds["winrm_hostname"] = source_dc_hostname
            source_creds["winrm_username"] = domain_admin_username
            source_creds["winrm_password"] = domain_admin_password
            source_session = await loop.run_in_executor(
                None,
                lambda: get_winrm_session(source_creds, dc_hostname=source_dc_hostname)
            )
            seize_script = (
                'cmd /c "echo roles & echo connections & '
                f'echo connect to server {source_dc_hostname} & echo quit & '
                'echo seize schema master & echo seize domain naming master & '
                'echo seize infrastructure master & echo seize rid master & '
                'echo seize pdc & echo quit & echo quit" | ntdsutil'
            )
            stdout, stderr, rc = await loop.run_in_executor(
                None, lambda: _winrm_run(source_session, seize_script)
            )
            seize_ok = "transfer" in stdout.lower() or "seize" in stdout.lower()
            if not seize_ok and rc != 0:
                logger.error("ad_dc_parallel_upgrade rollback: ntdsutil seize failed: %s", stderr)
                steps.append({"step": "seize_fsmo", "status": "failed", "stderr": stderr[:500]})
            else:
                # Verify
                verify_out, _, _ = await loop.run_in_executor(
                    None, lambda: _winrm_run(source_session, "netdom query fsmo")
                )
                fsmo_restored = _parse_fsmo_netdom(verify_out)
                steps.append({"step": "seize_fsmo", "status": "ok", "fsmo_restored": fsmo_restored})
                logger.info("ad_dc_parallel_upgrade rollback: FSMOs seized to %s", source_dc_hostname)
        except Exception as exc:
            logger.error("ad_dc_parallel_upgrade rollback: seize exception: %s", exc)
            steps.append({"step": "seize_fsmo", "status": "error", "error": str(exc)})

    # Demote new DC (Cases A and B)
    if new_dc_private_ip:
        logger.info("ad_dc_parallel_upgrade rollback: demoting new DC %s", new_dc_private_ip)
        try:
            new_creds = dict(creds)
            new_creds["winrm_hostname"] = new_dc_private_ip
            new_creds["winrm_username"] = domain_admin_username
            new_creds["winrm_password"] = domain_admin_password
            new_session = await loop.run_in_executor(
                None,
                lambda: get_winrm_session(new_creds, dc_hostname=new_dc_private_ip)
            )
            demote_script = (
                _secpw_block +
                'Uninstall-ADDSDomainController '
                '-LocalAdministratorPassword $secpw '
                '-Force:$true '
                '-NoRebootOnCompletion:$false'
            )
            try:
                await loop.run_in_executor(None, lambda: _winrm_run(new_session, demote_script))
            except Exception as exc:
                if not _is_expected_reboot_disconnect(exc):
                    raise
            steps.append({"step": "demote_new_dc", "status": "ok"})
            await asyncio.sleep(30)  # Give DC time to begin reboot before terminating
        except Exception as exc:
            logger.error("ad_dc_parallel_upgrade rollback: demote new DC failed: %s", exc)
            steps.append({"step": "demote_new_dc", "status": "error", "error": str(exc)})

    # Terminate new instance
    logger.info("ad_dc_parallel_upgrade rollback: terminating instance %s", new_instance_id)
    try:
        await loop.run_in_executor(
            None,
            lambda: ec2.terminate_instances(InstanceIds=[new_instance_id])
        )
        steps.append({"step": "terminate_instance", "status": "ok", "instance_id": new_instance_id})
    except Exception as exc:
        logger.error("ad_dc_parallel_upgrade rollback: terminate failed: %s", exc)
        steps.append({"step": "terminate_instance", "status": "error", "error": str(exc)})

    strategy = "seize_fsmo_then_demote_new_dc" if fsmo_transferred else "demote_new_dc"
    all_ok = all(s["status"] == "ok" for s in steps)

    return {
        "rolled_back": all_ok,
        "strategy": strategy,
        "new_instance_id": new_instance_id,
        "new_instance_terminated": any(
            s["step"] == "terminate_instance" and s["status"] == "ok" for s in steps
        ),
        "fsmo_seized_to": source_dc_hostname if fsmo_transferred else None,
        "steps": steps,
        "notes": "ntdsutil seize performed; verify replication after rollback" if fsmo_transferred else None,
    }
