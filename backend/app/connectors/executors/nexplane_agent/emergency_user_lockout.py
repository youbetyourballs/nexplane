from __future__ import annotations
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier", "")
    systems = parameters.get("systems", ["aws_iam", "linux_local"])
    results = {}
    errors = []

    for system in systems:
        try:
            if system == "aws_iam":
                from app.connectors.executors.aws.lock_iam_user import execute as iam_lock
                await iam_lock({"user_name": user}, asset_ids, connector)
                results["aws_iam"] = "locked"
            elif system == "linux_local":
                from app.connectors.executors.nexplane_agent import _dispatch
                await _dispatch.dispatch_agent_job(
                    command="lock_local_user",
                    parameters={"username": user, "terminate_sessions": True},
                    asset_ids=list(asset_ids),
                    timeout_seconds=30,
                )
                results["linux_local"] = "locked"
        except Exception as e:
            results[system] = "failed"
            errors.append({"system": system, "error": str(e)})

    return {
        "action": "emergency_user_lockout",
        "user_identifier": user,
        "lockout_status": results,
        "errors": errors,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    user = parameters.get("user_identifier", "")
    rollback_results = {}
    if execution_result.get("lockout_status", {}).get("aws_iam") == "locked":
        from app.connectors.executors.aws.lock_iam_user import rollback as iam_unlock
        r = await iam_unlock({"user_identifier": user}, execution_result, connector)
        rollback_results["aws_iam"] = "unlocked" if r.get("rolled_back") else "failed"
    return {"rolled_back": True, "systems": rollback_results}
