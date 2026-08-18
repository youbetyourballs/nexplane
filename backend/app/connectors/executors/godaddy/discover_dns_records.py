# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging

from app.connectors.executors.godaddy._client import gd_get

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "none"
ROLLBACK_REASON = "Discovery actions have no rollback"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials if hasattr(connector, "credentials") else {}
    domain = parameters.get("domain", "").strip()
    if not domain:
        raise ValueError("discover_dns_records: 'domain' parameter is required")

    record_type = parameters.get("record_type", "").upper() or None
    name = parameters.get("name", "").strip() or None

    path = f"/v1/domains/{domain}/records"
    if record_type:
        path += f"/{record_type}"
        if name:
            path += f"/{name}"

    records = await gd_get(path, creds, connector)
    logger.info("discover_dns_records: fetched %d records for %s", len(records), domain)
    return {
        "domain": domain,
        "records": records,
        "count": len(records),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
