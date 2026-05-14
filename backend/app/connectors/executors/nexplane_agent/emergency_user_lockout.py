from __future__ import annotations
from datetime import datetime, timezone


def _okta_client_from_params(parameters: dict):
    """Extract Okta credentials from parameters for emergency lockout."""
    org_url = parameters.get("okta_org_url") or parameters.get("org_url")
    api_token = parameters.get("okta_api_token") or parameters.get("api_token")
    if not org_url or not api_token:
        return None, None
    base = org_url.rstrip("/") + "/api/v1"
    headers = {
        "Authorization": f"SSWS {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return base, headers


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier", "")
    systems = parameters.get("systems", ["aws_iam", "azure_ad", "linux_local"])
    results = {}
    errors = []

    for system in systems:
        try:
            if system == "aws_iam":
                from app.connectors.executors.aws.lock_iam_user import execute as iam_lock
                await iam_lock({"user_name": user}, asset_ids, connector)
                results["aws_iam"] = "locked"
            elif system == "azure_ad":
                from app.connectors.executors.azure_ad.disable_user import execute as aad_disable
                await aad_disable({"user_identifier": user}, asset_ids, connector)
                results["azure_ad"] = "locked"
            elif system == "linux_local":
                from app.connectors.executors.nexplane_agent import _dispatch
                await _dispatch.dispatch_agent_job(
                    command="lock_local_user",
                    parameters={"username": user, "terminate_sessions": True},
                    asset_ids=list(asset_ids),
                    timeout_seconds=30,
                )
                results["linux_local"] = "locked"
            elif system == "okta":
                import httpx
                base, headers = _okta_client_from_params(parameters)
                if base:
                    async with httpx.AsyncClient(timeout=30) as c:
                        resp = await c.post(f"{base}/users/{user}/lifecycle/suspend", headers=headers)
                        resp.raise_for_status()
                    results["okta"] = "locked"
                else:
                    results["okta"] = "skipped_no_credentials"
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

    # Always attempt unconditionally — delete_user_policy is idempotent,
    # and execution_result structure varies depending on how the workflow wraps it.
    try:
        from app.connectors.executors.aws.lock_iam_user import rollback as iam_unlock
        r = await iam_unlock({"user_identifier": user}, execution_result, connector)
        rollback_results["aws_iam"] = "unlocked" if r.get("rolled_back") else "failed"
    except Exception as e:
        rollback_results["aws_iam"] = f"error: {e}"

    try:
        from app.connectors.executors.azure_ad.disable_user import rollback as aad_restore
        r = await aad_restore({"user_identifier": user}, execution_result, connector)
        rollback_results["azure_ad"] = "unlocked" if r.get("rolled_back") else "skipped"
    except Exception as e:
        rollback_results["azure_ad"] = f"error: {e}"

    return {"rolled_back": True, "systems": rollback_results}
