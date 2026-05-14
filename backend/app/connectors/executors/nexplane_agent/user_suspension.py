from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier", "")
    duration_hours = float(parameters.get("duration_hours", 0))

    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute as lockout
    lockout_result = await lockout(
        {"user_identifier": user, "systems": parameters.get("systems", ["aws_iam"])},
        asset_ids, connector,
    )

    result = {
        "action": "user_suspension",
        "user_identifier": user,
        "lockout_result": lockout_result,
        "suspended_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }

    if duration_hours > 0:
        try:
            from app.services.scheduled_cr_service import schedule_reversal_cr
            reversal_cr_id = await schedule_reversal_cr(
                original_parameters=parameters,
                original_asset_ids=list(asset_ids),
                execute_after_hours=duration_hours,
                change_type="emergency_user_lockout",
            )
            result["scheduled_reversal_cr_id"] = reversal_cr_id
        except Exception as e:
            result["scheduled_reversal_error"] = str(e)

    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import rollback as unlock
    return await unlock(parameters, execution_result, connector)
