import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    checked_at = datetime.now(timezone.utc).isoformat()

    if not asset_ids and not parameters.get("asset_ids"):
        return {
            "action": "check_fleet_health",
            "healthy": 0, "degraded": 0, "unreachable": 0,
            "details": [], "checked_at": checked_at,
            "note": "no assets selected",
        }

    target_ids: list[str] = asset_ids or parameters.get("asset_ids", [])

    if not creds:
        return {
            "action": "check_fleet_health",
            "healthy": len(target_ids),
            "degraded": 0,
            "unreachable": 0,
            "details": [{"asset_id": a, "status": "healthy"} for a in target_ids],
            "checked_at": checked_at,
            "simulated": True,
        }

    return await _dispatch_health_check(target_ids, parameters, connector, checked_at)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_fleet_health is read-only — no rollback needed"}


async def _dispatch_health_check(asset_ids: list, parameters: dict, connector, checked_at: str) -> dict:
    try:
        result = await asyncio.wait_for(
            _agent_health_check(asset_ids, connector),
            timeout=60.0,
        )
        return {
            "action": "check_fleet_health",
            "checked_at": checked_at,
            "source": "agent",
            **result,
        }
    except (asyncio.TimeoutError, Exception):
        result = await _inventory_health_fallback(asset_ids, connector)
        return {
            "action": "check_fleet_health",
            "checked_at": checked_at,
            "source": "inventory_fallback",
            **result,
        }


async def _agent_health_check(asset_ids: list, connector) -> dict:
    agent_service = getattr(connector, "agent_service", None)
    if agent_service is None:
        raise RuntimeError("no agent_service on connector")

    results = await asyncio.gather(
        *[agent_service.run_command(aid, "health_check", {}) for aid in asset_ids],
        return_exceptions=True,
    )

    healthy, degraded, unreachable = 0, 0, 0
    details = []
    for aid, res in zip(asset_ids, results):
        if isinstance(res, Exception):
            unreachable += 1
            details.append({"asset_id": aid, "status": "unreachable"})
        elif res.get("status") == "ok":
            healthy += 1
            details.append({"asset_id": aid, "status": "healthy"})
        else:
            degraded += 1
            details.append({"asset_id": aid, "status": "degraded", "detail": res})

    return {"healthy": healthy, "degraded": degraded, "unreachable": unreachable, "details": details}


async def _inventory_health_fallback(asset_ids: list, connector) -> dict:
    inventory = getattr(connector, "asset_repository", None)
    healthy, degraded, unreachable = 0, 0, 0
    details = []

    for aid in asset_ids:
        status = "unknown"
        if inventory:
            try:
                asset = await inventory.get_asset(aid)
                health = getattr(asset, "metadata", {}).get("health_status", "unknown")
                status = health
            except Exception:
                status = "unreachable"

        if status in ("healthy", "ok"):
            healthy += 1
        elif status == "unreachable":
            unreachable += 1
        else:
            degraded += 1
        details.append({"asset_id": aid, "status": status})

    return {"healthy": healthy, "degraded": degraded, "unreachable": unreachable, "details": details}
