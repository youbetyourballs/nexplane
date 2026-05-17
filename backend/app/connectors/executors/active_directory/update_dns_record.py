"""
update_dns_record — update a DNS record in an AD-integrated zone via WinRM (delete + recreate).

Reads the existing value first for rollback. Performs Remove then Add in sequence.
Returns {zone_name, record_name, record_type, old_value, new_value, ttl, updated_at}.
Rollback: calls update again with old_value as new_value.
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
# Value extraction helper
# ---------------------------------------------------------------------------

def _extract_record_value(rec: dict) -> str:
    rdata = rec.get("RecordData") or {}
    for field in ("IPv4Address", "HostNameAlias", "DescriptiveText", "IPv6Address",
                  "DomainName", "MailExchange", "NameServer"):
        val = rdata.get(field)
        if val:
            if isinstance(val, dict):
                return str(next(iter(val.values()), ""))
            return str(val)
    return str(rdata) if rdata else ""


# ---------------------------------------------------------------------------
# Update logic (delete + recreate)
# ---------------------------------------------------------------------------

_PS_CREATE_A = (
    "Add-DnsServerResourceRecordA "
    "-ZoneName '{zone}' -Name '{name}' -IPv4Address '{value}' "
    "-TimeToLive (New-TimeSpan -Seconds {ttl}) -ErrorAction Stop"
)
_PS_CREATE_CNAME = (
    "Add-DnsServerResourceRecordCName "
    "-ZoneName '{zone}' -Name '{name}' -HostNameAlias '{value}' "
    "-TimeToLive (New-TimeSpan -Seconds {ttl}) -ErrorAction Stop"
)
_PS_CREATE_TXT = (
    "Add-DnsServerResourceRecord "
    "-ZoneName '{zone}' -Name '{name}' -Txt "
    "-DescriptiveText '{value}' "
    "-TimeToLive (New-TimeSpan -Seconds {ttl}) -ErrorAction Stop"
)


def _update_record(
    creds: dict,
    zone_name: str,
    record_name: str,
    record_type: str,
    new_value: str,
    ttl: int,
    dc_hostname: str | None,
) -> str | None:
    """Delete the existing record and recreate it with new_value. Returns old value."""
    proto = _winrm_client(creds, dc_hostname)
    rtype = record_type.upper()

    # Read existing value for rollback
    read_script = (
        f"Get-DnsServerResourceRecord -ZoneName '{zone_name}' "
        f"-Name '{record_name}' -RRType '{rtype}' "
        f"-ErrorAction SilentlyContinue | ConvertTo-Json -Depth 5"
    )
    read_out, _, _ = _run_ps(proto, read_script)
    old_value: str | None = None
    if read_out:
        try:
            data = json.loads(read_out)
            if isinstance(data, list):
                data = data[0] if data else {}
            old_value = _extract_record_value(data)
        except (json.JSONDecodeError, IndexError):
            old_value = None

    # Delete existing
    del_script = (
        f"Remove-DnsServerResourceRecord "
        f"-ZoneName '{zone_name}' -Name '{record_name}' -RRType '{rtype}' "
        f"-Force -ErrorAction Stop"
    )
    _, del_err, del_rc = _run_ps(proto, del_script)
    if del_rc != 0:
        raise RuntimeError(f"Delete step of update failed (rc={del_rc}): {del_err}")

    # Recreate with new value
    fmt = {"zone": zone_name, "name": record_name, "value": new_value, "ttl": ttl}
    if rtype == "A":
        create_script = _PS_CREATE_A.format(**fmt)
    elif rtype == "CNAME":
        create_script = _PS_CREATE_CNAME.format(**fmt)
    elif rtype == "TXT":
        create_script = _PS_CREATE_TXT.format(**fmt)
    else:
        raise ValueError(f"Unsupported record type for update: '{rtype}'")

    _, create_err, create_rc = _run_ps(proto, create_script)
    if create_rc != 0:
        raise RuntimeError(
            f"Recreate step of update failed (rc={create_rc}): {create_err}. "
            f"WARNING: record '{record_name}' may have been deleted but not recreated."
        )

    return old_value


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters["record_type"].upper()
    new_value = parameters["new_value"]
    ttl = int(parameters.get("ttl") or 300)
    dc_hostname = parameters.get("dc_hostname") or creds.get("winrm_hostname")

    has_winrm = all(creds.get(k) for k in ("winrm_username", "winrm_password"))

    if not has_winrm:
        return {
            "zone_name": zone_name,
            "record_name": record_name,
            "record_type": record_type,
            "old_value": "10.0.0.1",
            "new_value": new_value,
            "ttl": ttl,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    loop = asyncio.get_event_loop()
    old_value = await loop.run_in_executor(
        None,
        lambda: _update_record(creds, zone_name, record_name, record_type, new_value, ttl, dc_hostname),
    )

    return {
        "zone_name": zone_name,
        "record_name": record_name,
        "record_type": record_type,
        "old_value": old_value,
        "new_value": new_value,
        "ttl": ttl,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback by calling update again with old_value as new_value."""
    if execution_result.get("mock"):
        return {"rolled_back": True, "reason": "mock — no real update to reverse"}

    old_value = execution_result.get("old_value")
    if not old_value:
        return {
            "rolled_back": False,
            "reason": "old_value not captured — cannot revert update",
        }

    rollback_params = {
        "zone_name": execution_result["zone_name"],
        "record_name": execution_result["record_name"],
        "record_type": execution_result["record_type"],
        "new_value": old_value,
        "ttl": execution_result.get("ttl", 300),
        "dc_hostname": parameters.get("dc_hostname"),
    }
    result = await execute(rollback_params, [], connector)
    return {"rolled_back": True, "update_result": result}
