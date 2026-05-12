"""Dispatch executor for the deep_discover agent command.

Calls the Go agent's deep_discover command which performs full host enrichment:
outbound connections, env var names, open file descriptors, runtime library
dependencies, and config-file intelligence (nginx/Apache/IIS vhosts, upstreams).
"""
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="deep_discover",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "deep_discover is read-only, no rollback required"}
