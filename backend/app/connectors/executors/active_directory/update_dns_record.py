# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
update_dns_record — update a DNS record in an AD-integrated zone via WinRM (delete + recreate).

Reads the existing value first for rollback. Performs Remove then Add in sequence.
Returns {zone_name, record_name, record_type, old_value, new_value, ttl, updated_at}.
Rollback: calls update again with old_value as new_value.
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


def _get_record(
    creds: dict,
    zone_name: str,
    record_name: str,
    record_type: str,
    dc_hostname: str | None,
) -> str | None:
    """Read the current record value. No side effects."""
    rtype = record_type.upper()
    read_script = (
        f"Get-DnsServerResourceRecord -ZoneName '{zone_name}' "
        f"-Name '{record_name}' -RRType '{rtype}' "
        f"-ErrorAction SilentlyContinue | ConvertTo-Json -Depth 5"
    )
    read_out, _, _ = _run_ps(creds, read_script, dc_hostname)
    if not read_out:
        return None
    try:
        data = json.loads(read_out)
        if isinstance(data, list):
            data = data[0] if data else {}
        return _extract_record_value(data)
    except (json.JSONDecodeError, IndexError):
        return None


def _update_record(
    creds: dict,
    zone_name: str,
    record_name: str,
    record_type: str,
    new_value: str,
    ttl: int,
    dc_hostname: str | None,
) -> None:
    """Delete the existing record and recreate it with new_value. Pre-state must be captured before calling this."""
    rtype = record_type.upper()

    # Delete existing
    del_script = (
        f"Remove-DnsServerResourceRecord "
        f"-ZoneName '{zone_name}' -Name '{record_name}' -RRType '{rtype}' "
        f"-Force -ErrorAction Stop"
    )
    _, del_err, del_rc = _run_ps(creds, del_script, dc_hostname)
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

    _, create_err, create_rc = _run_ps(creds, create_script, dc_hostname)
    if create_rc != 0:
        raise RuntimeError(
            f"Recreate step of update failed (rc={create_rc}): {create_err}. "
            f"WARNING: record '{record_name}' may have been deleted but not recreated."
        )


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

    # 1. Read pre-state (no side effects)
    old_value = await loop.run_in_executor(
        None,
        lambda: _get_record(creds, zone_name, record_name, record_type, dc_hostname),
    )

    # 2. Persist pre-state to DB before destructive action
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
                "old_value": old_value,
                "ttl": ttl,
            },
        )
        await db.commit()

    # 3. Perform the destructive action
    await loop.run_in_executor(
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

    # Prefer PreStateStore as authoritative source; fall back to execution_result
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid
    pre_state = None
    try:
        async with AsyncSessionLocal() as db:
            pre_state = await PreStateStore.retrieve(
                db,
                _uuid.UUID(str(parameters["cr_id"])),
                str(parameters.get("step_id", "step_0")),
                _uuid.UUID(str(parameters["org_id"])),
            )
    except Exception:
        pass

    if pre_state:
        old_value = pre_state.get("old_value")
        zone_name = pre_state.get("zone_name", execution_result.get("zone_name"))
        record_name = pre_state.get("record_name", execution_result.get("record_name"))
        record_type = pre_state.get("record_type", execution_result.get("record_type"))
        ttl = pre_state.get("ttl", execution_result.get("ttl", 300))
    else:
        old_value = execution_result.get("old_value")
        zone_name = execution_result.get("zone_name")
        record_name = execution_result.get("record_name")
        record_type = execution_result.get("record_type")
        ttl = execution_result.get("ttl", 300)

    if not old_value:
        return {
            "rolled_back": False,
            "reason": "old_value not captured — cannot revert update",
        }

    rollback_params = {
        "zone_name": zone_name,
        "record_name": record_name,
        "record_type": record_type,
        "new_value": old_value,
        "ttl": ttl,
        "dc_hostname": parameters.get("dc_hostname"),
        "cr_id": parameters["cr_id"],
        "step_id": parameters.get("step_id", "step_0"),
        "org_id": parameters["org_id"],
    }
    result = await execute(rollback_params, [], connector)
    return {"rolled_back": True, "update_result": result}
