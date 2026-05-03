from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    completed_at = datetime.now(timezone.utc).isoformat()

    report = {
        "report_type": "onboarding",
        "target_email": target_email,
        "display_name": parameters.get("display_name"),
        "manager_email": parameters.get("manager_email"),
        "completed_at": completed_at,
        "note": (
            "Temporary passwords for AD and Google Workspace accounts are available "
            "in the preceding step results. Credentials must be transmitted to the "
            "new employee via a secure channel."
        ),
    }

    return {
        "action": "generate_onboarding_report",
        "report": report,
        "executed_at": completed_at,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "onboarding_report_rollback", "skipped": True, "reason": "reports are not reversible"}
