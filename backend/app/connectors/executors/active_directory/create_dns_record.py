# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
create_dns_record — create an A, CNAME, or TXT record in an AD-integrated DNS zone via WinRM.

Pre-checks for record existence before creating. Captures rollback context so
delete_dns_record can cleanly reverse the operation.
Returns {zone_name, record_name, record_type, value, ttl, created_at}.
"""

import asyncio
import json
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"

# ---------------------------------------------------------------------------
# WinRM helpers (same pattern as dc_integrity_check)
# ---------------------------------------------------------------------------

def _run_ps(creds: dict, script: str, dc_hostname: str | None = None) -> tuple[str, str, int]:
    from ._client import run_winrm_ps
    return run_winrm_ps(creds, script, dc_hostname)


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

    # Pre-existence check
    check_script = _PS_CHECK_EXISTS.format(zone=zone_name, name=record_name, rtype=rtype_upper)
    check_out, _, _ = _run_ps(creds, check_script, dc_hostname)
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

    stdout, stderr, rc = _run_ps(creds, script, dc_hostname)
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
