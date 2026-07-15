# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "full"


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
        # Ensure the Go agent's validation passes: the agent requires at least one of
        # mode/policy_module_path/module_source/generate_from_audit_log. Older binaries
        # (pre module_source support) only check mode/policy_module_path/generate_from_audit_log,
        # so we also set mode="" which is ignored by selinuxExecuteOS if empty but satisfies
        # the type assertion `params["mode"].(string)` returning ok=true.
        # We use "permissive" here to keep the type-check compatible; the selinux_linux.go
        # only calls setenforce if mode is non-empty AND in the valid set, so passing the
        # current effective mode as a hint is safe if the host is already enforcing.
        # SAFER: pass mode only when module_source is present and mode is not already set.
        if module_source and "mode" not in agent_params:
            agent_params["mode"] = ""  # empty string: selinuxExecuteOS skips mode change

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
