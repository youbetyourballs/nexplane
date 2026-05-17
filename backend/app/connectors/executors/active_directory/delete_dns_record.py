"""
delete_dns_record — delete a DNS record from an AD-integrated zone via WinRM.

Reads the existing record value before deletion so rollback can re-create it.
Returns {zone_name, record_name, record_type, previous_value, deleted_at}.
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
# DNS value extraction helper (mirrors list_dns_records logic)
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
# Delete logic
# ---------------------------------------------------------------------------

def _read_existing_value(proto, zone_name: str, record_name: str, record_type: str) -> str | None:
    """Read the current record value before deletion. Returns None if not found."""
    script = (
        f"Get-DnsServerResourceRecord -ZoneName '{zone_name}' "
        f"-Name '{record_name}' -RRType '{record_type}' "
        f"-ErrorAction SilentlyContinue | ConvertTo-Json -Depth 5"
    )
    stdout, _, rc = _run_ps(proto, script)
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
        if isinstance(data, list):
            data = data[0] if data else {}
        return _extract_record_value(data)
    except (json.JSONDecodeError, IndexError):
        return None


def _delete_record(
    creds: dict,
    zone_name: str,
    record_name: str,
    record_type: str,
    dc_hostname: str | None,
) -> str | None:
    proto = _winrm_client(creds, dc_hostname)

    previous_value = _read_existing_value(proto, zone_name, record_name, record_type.upper())

    script = (
        f"Remove-DnsServerResourceRecord "
        f"-ZoneName '{zone_name}' "
        f"-Name '{record_name}' "
        f"-RRType '{record_type.upper()}' "
        f"-Force "
        f"-ErrorAction Stop"
    )
    stdout, stderr, rc = _run_ps(proto, script)
    if rc != 0:
        raise RuntimeError(f"Remove-DnsServerResourceRecord failed (rc={rc}): {stderr or stdout}")

    return previous_value


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters["record_type"].upper()
    dc_hostname = parameters.get("dc_hostname") or creds.get("winrm_hostname")

    has_winrm = all(creds.get(k) for k in ("winrm_username", "winrm_password"))

    if not has_winrm:
        return {
            "zone_name": zone_name,
            "record_name": record_name,
            "record_type": record_type,
            "previous_value": "10.0.0.1",
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    loop = asyncio.get_event_loop()
    previous_value = await loop.run_in_executor(
        None,
        lambda: _delete_record(creds, zone_name, record_name, record_type, dc_hostname),
    )

    return {
        "zone_name": zone_name,
        "record_name": record_name,
        "record_type": record_type,
        "previous_value": previous_value,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback by re-creating the record using the previous_value captured at delete time."""
    if execution_result.get("mock"):
        return {"rolled_back": True, "reason": "mock — no real deletion to reverse"}

    previous_value = execution_result.get("previous_value")
    if not previous_value:
        return {
            "rolled_back": False,
            "reason": "previous_value not captured — cannot re-create deleted record",
        }

    from . import create_dns_record as _create

    rollback_params = {
        "zone_name": execution_result["zone_name"],
        "record_name": execution_result["record_name"],
        "record_type": execution_result["record_type"],
        "value": previous_value,
        "ttl": 300,
        "dc_hostname": parameters.get("dc_hostname"),
    }
    result = await _create.execute(rollback_params, [], connector)
    return {"rolled_back": True, "create_result": result}
