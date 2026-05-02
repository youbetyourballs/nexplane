from datetime import datetime, timezone


def _mock_response(parameters):
    return {
        "action": "capture_dns_record",
        "record_name": parameters.get("record_name"),
        "record_type": parameters.get("record_type", "A"),
        "current_value": "203.0.113.10",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import cf_get
    zone_id = creds.get("zone_id")
    record_name = parameters.get("record_name")
    record_type = parameters.get("record_type", "A")
    params = f"?name={record_name}&type={record_type}" if record_name else ""
    data = await cf_get(f"/zones/{zone_id}/dns_records{params}", creds)
    records = data.get("result", [])
    if records:
        rec = records[0]
        return {
            "action": "capture_dns_record",
            "record_id": rec.get("id"),
            "record_name": rec.get("name"),
            "record_type": rec.get("type"),
            "current_value": rec.get("content"),
            "ttl": rec.get("ttl"),
            "proxied": rec.get("proxied"),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
    return _mock_response(parameters)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture has no rollback"}
