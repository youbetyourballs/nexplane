# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Sync Splunk notable events (or saved search results) as Nexplane findings."""

try:
    from ._client import get_splunk_client
except ImportError:
    get_splunk_client = None  # type: ignore[assignment]


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Pull notable events from Splunk ES (or fallback generic events) and return as findings.

    Parameters:
        earliest (str): SPL earliest time, e.g. "-24h". Default: "-24h"
        latest (str): SPL latest time, e.g. "now". Default: "now"
        saved_search (str): Optional saved search name to run instead of notable lookup

    Returns dict with:
        action: "splunk_sync_notables"
        count: number of events found
        events: list of event dicts
    """
    _gsc = get_splunk_client
    if _gsc is None:
        from ._client import get_splunk_client as _gsc  # type: ignore[assignment]
    client = await _gsc(connector)
    if client is None:
        return {
            "action": "splunk_sync_notables",
            "count": 0,
            "events": [],
            "status": "skipped",
            "reason": "no credentials",
        }

    earliest: str = parameters.get("earliest", "-24h")
    latest: str = parameters.get("latest", "now")
    saved_search: str = parameters.get("saved_search", "")

    try:
        if saved_search:
            raw_events = client.search(
                f"savedsearch \"{saved_search}\"",
                earliest=earliest,
                latest=latest,
            )
        else:
            raw_events = client.get_notable_events()

        findings = []
        for evt in raw_events:
            finding = {
                "source": "splunk",
                "event_id": evt.get("event_id") or evt.get("_key", ""),
                "rule_name": evt.get("rule_name") or evt.get("search_name", ""),
                "severity": evt.get("urgency") or evt.get("severity", "unknown"),
                "status": evt.get("status_label") or evt.get("status", "open"),
                "timestamp": evt.get("_time", ""),
                "raw": evt,
            }
            findings.append(finding)

        return {
            "action": "splunk_sync_notables",
            "count": len(findings),
            "events": findings,
        }
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_notables is read-only — no rollback"}
