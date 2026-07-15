# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import logging
from app.connectors.executors.nexplane_agent import _dispatch

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

_DEFAULT_PATHS = ["/etc", "/opt", "/var/www", "/home"]
_DEFAULT_EXTENSIONS = [".conf", ".env", ".yaml", ".yml", ".json", ".toml", ".ini", ".properties", ".sh"]


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    paths = params.get("paths") or _DEFAULT_PATHS
    extensions = params.get("extensions") or _DEFAULT_EXTENSIONS
    asset_id = str(cr.asset_id) if getattr(cr, "asset_id", None) else None

    result = await _dispatch.dispatch_agent_job(
        command="reference-scan",
        parameters={
            "terms": ",".join(search_terms),
            "paths": ",".join(paths),
            "extensions": ",".join(extensions),
        },
        asset_ids=[asset_id] if asset_id else [],
        timeout_seconds=300,
    )

    if result.get("exit_code", 0) != 0:
        return {
            "hits": [],
            "scan_summary": {
                "scanned": 0,
                "matched": 0,
                "error": result.get("output", "agent job failed"),
            },
        }

    # dispatch_agent_job returns job.result (a dict). In the live path the agent
    # stores the parsed JSON dict directly. In some test/integration paths the
    # result dict contains an "output" key with the raw JSON string — handle both.
    raw_output = result.get("output")
    if raw_output is not None:
        try:
            data = json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Agent reference-scan returned non-JSON output field")
            return {"hits": [], "scan_summary": {"scanned": 0, "matched": 0, "error": "parse_failure"}}
    elif "hits" in result:
        data = result
    else:
        logger.warning("Agent reference-scan result has no recognisable payload")
        return {"hits": [], "scan_summary": {"scanned": 0, "matched": 0, "error": "empty_result"}}

    return {
        "hits": data.get("hits", []),
        "scan_summary": data.get("scan_summary", {}),
    }


async def rollback(cr, connector, db) -> dict:
    return {"rolled_back": False, "reason": "reference scan is read-only, no rollback required"}
