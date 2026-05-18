import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    framework = parameters.get("framework", "cis")
    checked_at = datetime.now(timezone.utc).isoformat()

    target_ids: list[str] = asset_ids or parameters.get("asset_ids", [])

    if not creds:
        return {
            "action": "check_compliance",
            "framework": framework,
            "compliant": len(target_ids),
            "non_compliant": 0,
            "unknown": 0,
            "details": [{"asset_id": a, "status": "compliant"} for a in target_ids],
            "checked_at": checked_at,
            "simulated": True,
        }

    return await _dispatch_compliance_check(target_ids, framework, connector, checked_at)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_compliance is read-only — no rollback needed"}


async def _dispatch_compliance_check(asset_ids: list, framework: str, connector, checked_at: str) -> dict:
    try:
        result = await asyncio.wait_for(
            _agent_compliance_check(asset_ids, framework, connector),
            timeout=60.0,
        )
        return {
            "action": "check_compliance",
            "framework": framework,
            "checked_at": checked_at,
            "source": "agent",
            **result,
        }
    except (asyncio.TimeoutError, Exception):
        result = await _inventory_compliance_fallback(asset_ids, framework, connector)
        return {
            "action": "check_compliance",
            "framework": framework,
            "checked_at": checked_at,
            "source": "inventory_fallback",
            **result,
        }


async def _agent_compliance_check(asset_ids: list, framework: str, connector) -> dict:
    agent_service = getattr(connector, "agent_service", None)
    if agent_service is None:
        raise RuntimeError("no agent_service on connector")

    results = await asyncio.gather(
        *[agent_service.run_command(aid, "check_compliance", {"framework": framework})
          for aid in asset_ids],
        return_exceptions=True,
    )

    compliant, non_compliant, unknown = 0, 0, 0
    details = []
    for aid, res in zip(asset_ids, results):
        if isinstance(res, Exception):
            unknown += 1
            details.append({"asset_id": aid, "status": "unknown"})
        elif res.get("compliant"):
            compliant += 1
            details.append({"asset_id": aid, "status": "compliant", "score": res.get("score")})
        else:
            non_compliant += 1
            details.append({"asset_id": aid, "status": "non_compliant", "findings": res.get("findings", [])})

    return {"compliant": compliant, "non_compliant": non_compliant, "unknown": unknown, "details": details}


async def _inventory_compliance_fallback(asset_ids: list, framework: str, connector) -> dict:
    inventory = getattr(connector, "asset_repository", None)
    compliant, non_compliant, unknown = 0, 0, 0
    details = []

    for aid in asset_ids:
        status = "unknown"
        if inventory:
            try:
                asset = await inventory.get_asset(aid)
                last_scan = getattr(asset, "metadata", {}).get(f"compliance_{framework}_status", "unknown")
                status = last_scan
            except Exception:
                pass

        if status == "compliant":
            compliant += 1
        elif status == "non_compliant":
            non_compliant += 1
        else:
            unknown += 1
        details.append({"asset_id": aid, "status": status})

    return {"compliant": compliant, "non_compliant": non_compliant, "unknown": unknown, "details": details}
