# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def _get_firewall(creds: dict):
    from ._client import get_firewall
    return get_firewall(creds)


def _candidate_has_deny_for_zones(fw, source_zone: str, destination_zone: str):
    """Return (True, rule_name) if candidate config has a deny rule matching the given zones."""
    resp = fw.op("show config candidate")
    root = ET.fromstring(resp.text) if hasattr(resp, "text") else ET.fromstring(str(resp))
    for rule in root.findall(".//security/rules/entry"):
        action_el = rule.find("action")
        if action_el is None or action_el.text != "deny":
            continue
        from_zones = {m.text for m in rule.findall("from/member")}
        to_zones = {m.text for m in rule.findall("to/member")}
        if (
            (source_zone in from_zones or "any" in from_zones)
            and (destination_zone in to_zones or "any" in to_zones)
        ):
            rule_name = rule.get("name", "unknown")
            return True, rule_name
    return False, None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("hostname"):
        return {"status": "error", "error": "PaloAlto connector missing hostname credential"}

    source_zone = parameters.get("source_zone", "trust")
    destination_zone = parameters.get("destination_zone", "untrust")

    def _validate():
        fw = _get_firewall(creds)
        blocked, rule_name = _candidate_has_deny_for_zones(fw, source_zone, destination_zone)
        return blocked, rule_name

    try:
        blocked, blocking_rule = await asyncio.get_running_loop().run_in_executor(None, _validate)
        return {
            "action": "validate_staged",
            "source_zone": source_zone,
            "destination_zone": destination_zone,
            "no_critical_flows_blocked": not blocked,
            "blocking_rule": blocking_rule,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "action": "validate_staged"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation is read-only — no rollback needed"}
