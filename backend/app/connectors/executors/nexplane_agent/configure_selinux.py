import json

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Unpack synthesized profile from soak service: {session_id, service_name, profile: <json str>}
    # into the flat shape the Go agent expects: {module_name, module_source}.
    agent_params = dict(parameters)
    profile_raw = agent_params.pop("profile", None)
    service_name = agent_params.get("service_name", "")
    if profile_raw is not None:
        profile = json.loads(profile_raw) if isinstance(profile_raw, str) else profile_raw
        module_name = (profile.get("module_name") or "nexplane-{service_name}").replace(
            "{service_name}", service_name
        )
        module_source = (profile.get("module_source") or "").replace("{service_name}", service_name)
        agent_params["module_name"] = module_name
        agent_params["module_source"] = module_source

    result = await _dispatch.dispatch_agent_job(
        command="configure_selinux",
        parameters=agent_params,
        asset_ids=list(asset_ids),
        timeout_seconds=180,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_selinux",
        parameters={"config_snapshot": execution_result.get("config_snapshot", {})},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
