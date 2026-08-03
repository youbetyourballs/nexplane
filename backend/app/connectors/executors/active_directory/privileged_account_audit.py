# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import logging
from datetime import datetime, timezone, timedelta

try:
    from ._client import get_connection, prepare_ad_target
except ImportError:
    get_connection = None
    prepare_ad_target = None

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"

# Windows FILETIME epoch offset in 100-ns intervals
_FILETIME_EPOCH_DIFF = 116444736000000000

_DEFAULT_GROUPS = [
    "Domain Admins",
    "Enterprise Admins",
    "Schema Admins",
    "Administrators",
]

_GROUP_DNS = {
    "Domain Admins": "CN=Domain Admins,CN=Users,{base_dn}",
    "Enterprise Admins": "CN=Enterprise Admins,CN=Users,{base_dn}",
    "Schema Admins": "CN=Schema Admins,CN=Users,{base_dn}",
    "Administrators": "CN=Administrators,CN=Builtin,{base_dn}",
}


def _filetime_to_dt(filetime_val: int) -> datetime | None:
    """Convert Windows FILETIME (100-ns intervals since 1601-01-01) to UTC datetime."""
    if not filetime_val or filetime_val == 0:
        return None
    unix_ts = (filetime_val - _FILETIME_EPOCH_DIFF) / 10_000_000
    if unix_ts <= 0:
        return None
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


def _is_disabled(uac: int) -> bool:
    return bool(uac & 0x2)


def _is_service_account(sam: str, description: str) -> bool:
    sam_lower = (sam or "").lower()
    desc_lower = (description or "").lower()
    if sam_lower.startswith("svc") or sam_lower.startswith("svc_"):
        return True
    if sam_lower.endswith("$"):
        return True
    if "_sa" in sam_lower:
        return True
    if "svc" in sam_lower or "service" in sam_lower:
        return True
    if "svc" in desc_lower or "service" in desc_lower:
        return True
    return False


def _is_stale(last_logon_dt: datetime | None, threshold_days: int) -> bool:
    if last_logon_dt is None:
        return True
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=threshold_days)
    return last_logon_dt < cutoff


def _assess_account(entry: dict, threshold_days: int) -> dict:
    sam = entry.get("sAMAccountName", "")
    description = entry.get("description", "") or ""
    uac = entry.get("userAccountControl", 512)
    last_logon_dt = entry.get("_last_logon_dt")

    disabled = _is_disabled(uac)
    service_account = _is_service_account(sam, description)
    stale = _is_stale(last_logon_dt, threshold_days)

    risk_flags = []
    if stale:
        risk_flags.append("stale_no_recent_logon")
    if service_account:
        risk_flags.append("service_account_with_admin_rights")
    if disabled:
        risk_flags.append("disabled_but_in_privileged_group")

    severity = "low"
    if len(risk_flags) >= 2:
        severity = "high"
    elif risk_flags:
        severity = "medium"

    result = dict(entry)
    result.pop("_last_logon_dt", None)
    result["disabled"] = disabled
    result["is_service_account"] = service_account
    result["is_stale"] = stale
    result["last_logon_iso"] = last_logon_dt.isoformat() if last_logon_dt else None
    result["risk_flags"] = risk_flags
    result["severity"] = severity
    result["is_risky"] = bool(risk_flags)
    return result


def _build_remediations(risky_accounts: list) -> list:
    remediations = []
    for acc in risky_accounts:
        sam = acc.get("sAMAccountName", "")
        dn = acc.get("distinguishedName", "")
        flags = acc.get("risk_flags", [])
        if "stale_no_recent_logon" in flags or "disabled_but_in_privileged_group" in flags:
            remediations.append({
                "change_type": "disable_account",
                "parameters": {"username": sam, "user_dn": dn},
                "reason": f"Account '{sam}' flagged: {', '.join(flags)}",
            })
        elif "service_account_with_admin_rights" in flags and not acc.get("disabled"):
            remediations.append({
                "change_type": "enforce_mfa",
                "parameters": {"username": sam, "user_dn": dn},
                "reason": f"Active service account '{sam}' holds admin group membership",
            })
    return remediations


def _mock_findings(parameters: dict) -> dict:
    threshold = parameters.get("stale_threshold_days", 90)
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    stale_dt = (datetime.now(tz=timezone.utc) - timedelta(days=120)).isoformat()

    accounts = [
        {
            "sAMAccountName": "svc_backup",
            "distinguishedName": "CN=svc_backup,CN=Users,DC=corp,DC=local",
            "last_logon_iso": stale_dt,
            "userAccountControl": 512,
            "description": "Backup service account",
            "groups": ["Domain Admins"],
            "disabled": False,
            "is_service_account": True,
            "is_stale": True,
            "risk_flags": ["stale_no_recent_logon", "service_account_with_admin_rights"],
            "severity": "high",
            "is_risky": True,
        },
        {
            "sAMAccountName": "old_admin",
            "distinguishedName": "CN=old_admin,CN=Users,DC=corp,DC=local",
            "last_logon_iso": None,
            "userAccountControl": 514,
            "description": "",
            "groups": ["Administrators"],
            "disabled": True,
            "is_service_account": False,
            "is_stale": True,
            "risk_flags": ["stale_no_recent_logon", "disabled_but_in_privileged_group"],
            "severity": "high",
            "is_risky": True,
        },
        {
            "sAMAccountName": "jsmith",
            "distinguishedName": "CN=jsmith,CN=Users,DC=corp,DC=local",
            "last_logon_iso": datetime.now(tz=timezone.utc).isoformat(),
            "userAccountControl": 512,
            "description": "IT Administrator",
            "groups": ["Domain Admins"],
            "disabled": False,
            "is_service_account": False,
            "is_stale": False,
            "risk_flags": [],
            "severity": "low",
            "is_risky": False,
        },
    ]

    risky = [a for a in accounts if a["is_risky"]]
    remediations = _build_remediations(risky)

    return {
        "mock": True,
        "group_summary": {
            "Domain Admins": 2,
            "Enterprise Admins": 0,
            "Schema Admins": 0,
            "Administrators": 1,
        },
        "accounts": accounts,
        "risky_accounts": risky,
        "proposed_remediations": remediations,
        "audited_at": now_iso,
        "stale_threshold_days": threshold,
    }


def _real_audit(parameters: dict, creds: dict) -> dict:
    from ldap3 import SUBTREE

    conn = get_connection(creds)
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    threshold_days = parameters.get("stale_threshold_days", 90)
    include_groups = parameters.get("include_groups") or list(_GROUP_DNS.keys())
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    group_summary = {}
    seen_dns = {}  # dn -> account entry (dedup across groups)

    for group_name in include_groups:
        dn_template = _GROUP_DNS.get(group_name)
        if not dn_template:
            logger.warning("Unknown group name: %s", group_name)
            group_summary[group_name] = 0
            continue

        group_dn = dn_template.format(base_dn=base_dn)
        try:
            conn.search(
                group_dn,
                "(objectClass=*)",
                search_scope=SUBTREE,
                attributes=["member"],
            )
        except Exception as exc:
            logger.warning("Could not search group %s: %s", group_name, exc)
            group_summary[group_name] = 0
            continue

        if not conn.entries:
            logger.warning("Group not found: %s", group_dn)
            group_summary[group_name] = 0
            continue

        group_entry = conn.entries[0]
        members = group_entry.member.values if group_entry.member else []
        group_summary[group_name] = len(members)

        for member_dn in members:
            if member_dn in seen_dns:
                seen_dns[member_dn]["groups"].append(group_name)
                continue
            try:
                conn.search(
                    member_dn,
                    "(objectClass=*)",
                    search_scope=SUBTREE,
                    attributes=[
                        "sAMAccountName",
                        "userAccountControl",
                        "lastLogonTimestamp",
                        "description",
                        "givenName",
                        "sn",
                        "mail",
                        "distinguishedName",
                    ],
                )
            except Exception as exc:
                logger.warning("Could not fetch member %s: %s", member_dn, exc)
                continue

            if not conn.entries:
                continue

            e = conn.entries[0]

            def _val(attr):
                try:
                    v = getattr(e, attr).value
                    return v if v is not None else ""
                except Exception:
                    return ""

            sam = str(_val("sAMAccountName"))
            uac = int(_val("userAccountControl") or 512)
            description = str(_val("description"))
            dn_val = str(_val("distinguishedName") or member_dn)

            raw_logon = _val("lastLogonTimestamp")
            last_logon_dt = None
            if raw_logon:
                try:
                    last_logon_dt = _filetime_to_dt(int(raw_logon))
                except (TypeError, ValueError):
                    pass

            seen_dns[member_dn] = {
                "sAMAccountName": sam,
                "distinguishedName": dn_val,
                "userAccountControl": uac,
                "description": description,
                "givenName": str(_val("givenName")),
                "sn": str(_val("sn")),
                "mail": str(_val("mail")),
                "_last_logon_dt": last_logon_dt,
                "groups": [group_name],
            }

    conn.unbind()

    accounts = []
    for entry in seen_dns.values():
        assessed = _assess_account(entry, threshold_days)
        accounts.append(assessed)

    risky = [a for a in accounts if a["is_risky"]]
    remediations = _build_remediations(risky) if parameters.get("generate_remediations", True) else []

    return {
        "group_summary": group_summary,
        "accounts": accounts,
        "risky_accounts": risky,
        "proposed_remediations": remediations,
        "audited_at": now_iso,
        "stale_threshold_days": threshold_days,
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_findings(parameters)
    try:
        resolved = await prepare_ad_target(connector, creds)
        return await asyncio.get_event_loop().run_in_executor(
            None, _real_audit, parameters, resolved
        )
    except Exception as exc:
        logger.error("privileged_account_audit failed: %s", exc)
        return {
            "error": str(exc),
            "group_summary": {},
            "accounts": [],
            "risky_accounts": [],
            "proposed_remediations": [],
            "audited_at": datetime.now(tz=timezone.utc).isoformat(),
        }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "reason": "read-only audit, no changes made",
    }
