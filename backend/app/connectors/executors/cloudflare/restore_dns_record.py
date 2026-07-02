# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import cf_get, cf_put, cf_post
    zone_id = creds.get("zone_id")
    record_name = parameters.get("record_name")
    record_type = parameters.get("record_type", "A")
    previous_value = parameters.get("previous_value")
    ttl = parameters.get("ttl", 300)
    proxied = parameters.get("proxied", False)

    params = f"?name={record_name}&type={record_type}" if record_name else ""
    data = await cf_get(f"/zones/{zone_id}/dns_records{params}", creds, connector)
    records = data.get("result", [])
    record_id = records[0].get("id") if records else None

    body = {"type": record_type, "name": record_name, "content": previous_value, "ttl": ttl, "proxied": proxied}
    if record_id:
        await cf_put(f"/zones/{zone_id}/dns_records/{record_id}", body, creds, connector)
    else:
        await cf_post(f"/zones/{zone_id}/dns_records", body, creds, connector)

    return {
        "action": "restore_dns_record",
        "record_name": record_name,
        "restored_value": previous_value,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "restore_dns_record",
            "record_name": parameters.get("record_name"),
            "restored_value": parameters.get("previous_value"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore has no further rollback"}
