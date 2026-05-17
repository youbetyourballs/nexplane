"""
create_dns_record — create an A, CNAME, or TXT record in an AD-integrated DNS zone via WinRM.

Pre-checks for record existence before creating. Captures rollback context so
delete_dns_record can cleanly reverse the operation.
Returns {zone_name, record_name, record_type, value, ttl, created_at}.
"""

import asyncio
import json
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# WinRM helpers (same pattern as dc_integrity_check)
# ---------------------------------------------------------------------------

def _winrm_client(creds: dict, dc_hostname: str | None = None):
    import winrm

    host = dc_hostname or creds["winrm_hostname"]
    port = int(creds.get("winrm_port", 5985))
    use_ssl = str(creds.get("winrm_use_ssl", "false")).lower() == "true"
    scheme = "https" if use_ssl else "http"

    return winrm.Protocol(
        endpoint=f"{scheme}://{host}:{port}/wsman",
        transport="ntlm",
        username=creds["winrm_username"],
        password=creds["winrm_password"],
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


# ---------------------------------------------------------------------------
# DNS creation logic
# ---------------------------------------------------------------------------

_SUPPORTED_TYPES = {"A", "CNAME", "TXT"}

_PS_CHECK_EXISTS = """
try {{
    $r = Get-DnsServerResourceRecord -ZoneName '{zone}' -Name '{name}' -RRType '{rtype}' -ErrorAction SilentlyContinue
    if ($r) {{ Write-Output 'EXISTS' }} else {{ Write-Output 'NOT_FOUND' }}
}} catch {{
    Write-Output 'NOT_FOUND'
}}
"""

_PS_CREATE_A = (
    "Add-DnsServerResourceRecordA "
    "-ZoneName '{zone}' "
    "-Name '{name}' "
    "-IPv4Address '{value}' "
    "-TimeToLive (New-TimeSpan -Seconds {ttl}) "
    "-ErrorAction Stop"
)

_PS_CREATE_CNAME = (
    "Add-DnsServerResourceRecordCName "
    "-ZoneName '{zone}' "
    "-Name '{name}' "
    "-HostNameAlias '{value}' "
    "-TimeToLive (New-TimeSpan -Seconds {ttl}) "
    "-ErrorAction Stop"
)

_PS_CREATE_TXT = (
    "Add-DnsServerResourceRecord "
    "-ZoneName '{zone}' "
    "-Name '{name}' "
    "-Txt "
    "-DescriptiveText '{value}' "
    "-TimeToLive (New-TimeSpan -Seconds {ttl}) "
    "-ErrorAction Stop"
)


def _create_record(
    creds: dict,
    zone_name: str,
    record_name: str,
    record_type: str,
    value: str,
    ttl: int,
    dc_hostname: str | None,
) -> None:
    rtype_upper = record_type.upper()
    if rtype_upper not in _SUPPORTED_TYPES:
        raise ValueError(f"Unsupported record type '{record_type}'. Supported: {', '.join(sorted(_SUPPORTED_TYPES))}")

    proto = _winrm_client(creds, dc_hostname)

    # Pre-existence check
    check_script = _PS_CHECK_EXISTS.format(zone=zone_name, name=record_name, rtype=rtype_upper)
    check_out, _, _ = _run_ps(proto, check_script)
    if "EXISTS" in check_out:
        raise ValueError(
            f"DNS record '{record_name}' ({rtype_upper}) already exists in zone '{zone_name}'. "
            "Use update_dns_record to change an existing record."
        )

    # Build creation script
    fmt = {"zone": zone_name, "name": record_name, "value": value, "ttl": ttl}
    if rtype_upper == "A":
        script = _PS_CREATE_A.format(**fmt)
    elif rtype_upper == "CNAME":
        script = _PS_CREATE_CNAME.format(**fmt)
    else:  # TXT
        script = _PS_CREATE_TXT.format(**fmt)

    stdout, stderr, rc = _run_ps(proto, script)
    if rc != 0:
        raise RuntimeError(f"DNS record creation failed (rc={rc}): {stderr or stdout}")


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters["record_type"].upper()
    value = parameters["value"]
    ttl = int(parameters.get("ttl") or 300)
    dc_hostname = parameters.get("dc_hostname") or creds.get("winrm_hostname")

    has_winrm = all(creds.get(k) for k in ("winrm_username", "winrm_password"))

    if not has_winrm:
        return {
            "zone_name": zone_name,
            "record_name": record_name,
            "record_type": record_type,
            "value": value,
            "ttl": ttl,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: _create_record(creds, zone_name, record_name, record_type, value, ttl, dc_hostname),
    )

    return {
        "zone_name": zone_name,
        "record_name": record_name,
        "record_type": record_type,
        "value": value,
        "ttl": ttl,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback by deleting the record that was just created."""
    if execution_result.get("mock"):
        return {"rolled_back": True, "reason": "mock — no real record to remove"}

    from . import delete_dns_record as _del

    rollback_params = {
        "zone_name": execution_result["zone_name"],
        "record_name": execution_result["record_name"],
        "record_type": execution_result["record_type"],
        "dc_hostname": parameters.get("dc_hostname"),
    }
    result = await _del.execute(rollback_params, [], connector)
    return {"rolled_back": True, "delete_result": result}
