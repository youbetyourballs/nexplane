import json

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Unpack synthesized profile from soak service: {session_id, service_name, profile: <json str>}
    # into the flat shape the Go agent expects: {profile_name, profile_content, mode}.
    agent_params = dict(parameters)
    profile_raw = agent_params.pop("profile", None)
    service_name = agent_params.get("service_name", "")
    if profile_raw is not None:
        profile = json.loads(profile_raw) if isinstance(profile_raw, str) else profile_raw
        profile_name = (profile.get("profile_name") or "").replace("{service_name}", service_name)
        profile_text = (profile.get("profile_text") or "").replace("{service_name}", service_name)
        agent_params["profile_name"] = profile_name
        agent_params["profile_content"] = profile_text
        agent_params["mode"] = profile.get("mode", "complain")

    result = await _dispatch.dispatch_agent_job(
        command="configure_apparmor",
        parameters=agent_params,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_apparmor",
        parameters={"snapshot": execution_result.get("snapshot", {})},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
