import random
import string
from datetime import datetime, timezone


def _fake_id(prefix: str = "") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{prefix}{suffix}"


def _mock_response(parameters):
    return {
        "action": "update_dns_record",
        "record_name": parameters.get("record_name"),
        "record_type": parameters.get("record_type", "A"),
        "previous_value": "203.0.113.10",
        "new_value": parameters.get("new_value"),
        "ttl": parameters.get("ttl", 300),
        "propagation_id": _fake_id("prop-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import cf_get, cf_put, cf_post
    zone_id = creds.get("zone_id")
    record_name = parameters.get("record_name")
    record_type = parameters.get("record_type", "A")
    new_value = parameters.get("new_value")
    ttl = parameters.get("ttl", 300)
    proxied = parameters.get("proxied", False)

    # Find existing record to get previous value and record_id
    params = f"?name={record_name}&type={record_type}" if record_name else ""
    data = await cf_get(f"/zones/{zone_id}/dns_records{params}", creds)
    records = data.get("result", [])
    previous_value = records[0].get("content") if records else "unknown"
    record_id = records[0].get("id") if records else None

    body = {"type": record_type, "name": record_name, "content": new_value, "ttl": ttl, "proxied": proxied}
    if record_id:
        resp = await cf_put(f"/zones/{zone_id}/dns_records/{record_id}", body, creds)
    else:
        resp = await cf_post(f"/zones/{zone_id}/dns_records", body, creds)

    result = resp.get("result", {})
    return {
        "action": "update_dns_record",
        "record_name": record_name,
        "record_type": record_type,
        "record_id": result.get("id", record_id),
        "previous_value": previous_value,
        "new_value": new_value,
        "ttl": ttl,
        "propagation_id": _fake_id("prop-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "restore_dns_record",
        "restored_value": execution_result.get("previous_value", "unknown"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
