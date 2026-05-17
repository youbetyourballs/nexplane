"""
list_dns_records — enumerate DNS records in an AD-integrated zone via WinRM PowerShell.

Uses Get-DnsServerResourceRecord on the DC. Optionally filters by record type.
Returns {zone_name, records: [{name, type, value, ttl}], count: N}.
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
    import winrm

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
# DNS record parsing
# ---------------------------------------------------------------------------

def _extract_record_value(rec: dict) -> str:
    """Pull the meaningful value out of a Get-DnsServerResourceRecord record object."""
    rdata = rec.get("RecordData") or {}
    # Common record data field names returned by the DNS server cmdlet
    for field in ("IPv4Address", "HostNameAlias", "DescriptiveText", "IPv6Address",
                  "DomainName", "MailExchange", "NameServer"):
        val = rdata.get(field)
        if val:
            # IPv4Address objects serialize as {"Address": "1.2.3.4"}
            if isinstance(val, dict):
                return str(next(iter(val.values()), ""))
            return str(val)
    return str(rdata) if rdata else ""


def _parse_records(raw: str) -> list[dict]:
    """Parse JSON from Get-DnsServerResourceRecord | ConvertTo-Json into record dicts."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(data, list):
        data = [data]

    records = []
    for rec in data:
        name = str(rec.get("HostName", ""))
        rtype = str(rec.get("RecordType", ""))
        ttl_raw = rec.get("TimeToLive")
        # TimeToLive is a TimeSpan serialized as {"Ticks":..., "TotalSeconds":...}
        if isinstance(ttl_raw, dict):
            ttl = int(ttl_raw.get("TotalSeconds", 0))
        elif ttl_raw is not None:
            try:
                ttl = int(ttl_raw)
            except (TypeError, ValueError):
                ttl = 0
        else:
            ttl = 0
        value = _extract_record_value(rec)
        records.append({"name": name, "type": rtype, "value": value, "ttl": ttl})
    return records


def _list_records(creds: dict, zone_name: str, record_type: str | None, dc_hostname: str | None) -> list[dict]:
    proto = _winrm_client(creds, dc_hostname)

    type_filter = f" -RRType '{record_type}'" if record_type else ""
    script = (
        f"Get-DnsServerResourceRecord -ZoneName '{zone_name}'{type_filter} "
        f"-ErrorAction Stop | ConvertTo-Json -Depth 5"
    )
    stdout, stderr, rc = _run_ps(proto, script)
    if rc != 0 or stdout.startswith("ERROR"):
        raise RuntimeError(f"Get-DnsServerResourceRecord failed (rc={rc}): {stderr or stdout}")
    return _parse_records(stdout)


# ---------------------------------------------------------------------------
# Executor entry points
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    zone_name = parameters["zone_name"]
    record_type = parameters.get("record_type") or None
    dc_hostname = parameters.get("dc_hostname") or creds.get("winrm_hostname")

    has_winrm = all(creds.get(k) for k in ("winrm_username", "winrm_password"))

    if not has_winrm:
        # Mock response when WinRM credentials are absent
        return {
            "zone_name": zone_name,
            "records": [
                {"name": "@", "type": "SOA", "value": "mock-dc.smoke.nexplane.local", "ttl": 3600},
                {"name": "@", "type": "NS", "value": "mock-dc.smoke.nexplane.local", "ttl": 3600},
            ],
            "count": 2,
            "mock": True,
            "listed_at": datetime.now(timezone.utc).isoformat(),
        }

    loop = asyncio.get_event_loop()
    records = await loop.run_in_executor(
        None, lambda: _list_records(creds, zone_name, record_type, dc_hostname)
    )

    return {
        "zone_name": zone_name,
        "records": records,
        "count": len(records),
        "listed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list_dns_records is read-only — no rollback needed"}
