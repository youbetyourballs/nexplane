from __future__ import annotations
"""Wazuh — register a new agent via the REST API."""
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from ._client import get_wazuh_client

    agent_name = parameters.get("agent_name", "")
    if not agent_name:
        raise ValueError("agent_name is required")

    client = get_wazuh_client(connector)
    if client is None:
        return {
            "action": "wazuh_deploy_agent",
            "status": "skipped",
            "reason": "no_wazuh_credentials",
            "agent_name": agent_name,
        }

    data = client.register_agent(agent_name)
    agent_id = data.get("id") or data.get("agent_id", "")

    return {
        "action": "wazuh_deploy_agent",
        "agent_name": agent_name,
        "agent_id": agent_id,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_wazuh_client

    agent_id = execution_result.get("agent_id", "")
    if not agent_id:
        return {"rolled_back": False, "reason": "no_agent_id_in_result"}

    client = get_wazuh_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no_wazuh_credentials"}

    client.delete_agent(agent_id)
    return {
        "rolled_back": True,
        "agent_id": agent_id,
        "action": "wazuh_deploy_agent_rollback",
    }
