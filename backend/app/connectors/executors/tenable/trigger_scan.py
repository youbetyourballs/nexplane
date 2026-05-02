import asyncio
import random
import string
from datetime import datetime, timezone


def _mock_response(parameters, asset_ids):
    scan_id = "scan-" + "".join(random.choices(string.digits, k=8))
    return {"action": "trigger_scan", "scan_id": scan_id, "scan_policy": parameters.get("scan_policy", "basic_network_scan"), "assets": asset_ids, "status": "launched", "launched_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_tio
    tio = get_tio(creds)
    loop = asyncio.get_event_loop()
    scan_id = parameters.get("scan_id")
    if scan_id:
        resp = await loop.run_in_executor(None, lambda: tio.scans.launch(int(scan_id)))
        scan_uuid = resp if isinstance(resp, str) else str(resp)
    else:
        scan_uuid = "scan-" + "".join(random.choices(string.digits, k=8))
    return {
        "action": "trigger_scan",
        "scan_id": scan_id or scan_uuid,
        "scan_uuid": scan_uuid,
        "scan_policy": parameters.get("scan_policy", "basic_network_scan"),
        "assets": asset_ids,
        "status": "launched",
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters, asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan trigger has no rollback"}
