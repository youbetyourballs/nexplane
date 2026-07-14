# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
delete_dns_record — delete a DNS record from an AD-integrated zone via WinRM.

Reads the existing record value before deletion so rollback can re-create it.
Returns {zone_name, record_name, record_type, previous_value, deleted_at}.
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

def _read_existing_value(creds: dict, zone_name: str, record_name: str, record_type: str, dc_hostname: str | None = None) -> str | None:
    """Read the current record value before deletion. Returns None if not found."""
    script = (
        f"Get-DnsServerResourceRecord -ZoneName '{zone_name}' "
        f"-Name '{record_name}' -RRType '{record_type}' "
        f"-ErrorAction SilentlyContinue | ConvertTo-Json -Depth 5"
    )
    stdout, _, rc = _run_ps(creds, script, dc_hostname)
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
    previous_value = _read_existing_value(creds, zone_name, record_name, record_type.upper(), dc_hostname)

    script = (
        f"Remove-DnsServerResourceRecord "
        f"-ZoneName '{zone_name}' "
        f"-Name '{record_name}' "
        f"-RRType '{record_type.upper()}' "
        f"-Force "
        f"-ErrorAction Stop"
    )
    stdout, stderr, rc = _run_ps(creds, script, dc_hostname)
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

    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid
    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            {
                "record_name": record_name,
                "record_type": record_type,
                "zone_name": zone_name,
                "value": previous_value,
                "ttl": parameters.get("ttl", 300),
            },
        )
        await db.commit()

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
