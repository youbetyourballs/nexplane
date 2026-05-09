from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch agent_appdiscovery to the registered Nexplane agent on the target host.

    Falls back to mock data when no agent is registered (e.g. in unit tests or
    when the agent has not yet been deployed to the target asset).
    """
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    try:
        result = await dispatch_agent_job(
            command="discover_applications",
            parameters=parameters,
            asset_ids=list(asset_ids),
            timeout_seconds=120,
        )
        return result
    except RuntimeError as exc:
        raise RuntimeError(
            f"No Nexplane agent registered for target asset(s) {asset_ids}. "
            "Deploy the agent before running agent_appdiscovery."
        ) from exc


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "appdiscovery is read-only, no rollback required"}
