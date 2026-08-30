# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def _get_firewall(creds: dict):
    from ._client import get_firewall
    return get_firewall(creds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("hostname"):
        return {"status": "error", "error": "PaloAlto connector missing hostname credential"}

    loop = asyncio.get_event_loop()
    query = parameters.get("log_query", "")

    def _query_logs():
        fw = _get_firewall(creds)
        cmd = f"show log traffic query ({query})" if query else "show log traffic"
        resp = fw.op(cmd)
        root = ET.fromstring(resp.text) if hasattr(resp, "text") else ET.fromstring(str(resp))
        logs_el = root.find(".//logs")
        count = int(logs_el.get("count", "0")) if logs_el is not None else 0
        return count

    try:
        count = await loop.run_in_executor(None, _query_logs)
        return {
            "action": "analyze_flows",
            "flows_analyzed": count,
            "log_query": query or "(all)",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "action": "analyze_flows"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "flow analysis is read-only — no rollback needed"}
