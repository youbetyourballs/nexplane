# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging

from app.connectors.executors.godaddy._client import gd_get, gd_put

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials if hasattr(connector, "credentials") else {}
    domain = parameters["domain"].strip()
    record_type = parameters["record_type"].upper().strip()
    name = parameters["name"].strip()
    value = parameters["value"].strip()
    ttl = int(parameters.get("ttl", 600))

    # Snapshot current records for rollback
    try:
        snapshot = await gd_get(f"/v1/domains/{domain}/records/{record_type}/{name}", creds, connector)
    except Exception:
        snapshot = []

    new_record = [{"data": value, "ttl": ttl}]
    await gd_put(f"/v1/domains/{domain}/records/{record_type}/{name}", new_record, creds, connector)
    logger.info("update_dns_record: set %s %s.%s -> %s (ttl=%s)", record_type, name, domain, value, ttl)

    return {
        "domain": domain,
        "record_type": record_type,
        "name": name,
        "value": value,
        "ttl": ttl,
        "_snapshot": snapshot,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials if hasattr(connector, "credentials") else {}
    domain = execution_result["domain"]
    record_type = execution_result["record_type"]
    name = execution_result["name"]
    snapshot = execution_result.get("_snapshot", [])

    if snapshot:
        await gd_put(f"/v1/domains/{domain}/records/{record_type}/{name}", snapshot, creds, connector)
        logger.info("update_dns_record rollback: restored %s %s.%s", record_type, name, domain)
        return {"rolled_back": True, "restored_records": snapshot}
    else:
        # Record didn't exist before — delete it
        from app.connectors.executors.godaddy._client import gd_delete
        await gd_delete(f"/v1/domains/{domain}/records/{record_type}/{name}", creds, connector)
        logger.info("update_dns_record rollback: deleted %s %s.%s (was not present before)", record_type, name, domain)
        return {"rolled_back": True, "action": "deleted_new_record"}
