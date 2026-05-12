import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_deep_discover_executor_dispatches_agent_job():
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value={"action": "deep_discover", "workloads": []},
    ) as mock_dispatch:
        from app.connectors.executors.nexplane_agent.deep_discover import execute
        result = await execute({}, ["asset-id-123"], connector=None)

    mock_dispatch.assert_called_once_with(
        command="deep_discover",
        parameters={},
        asset_ids=["asset-id-123"],
        timeout_seconds=120,
    )
    assert result["action"] == "deep_discover"


@pytest.mark.asyncio
async def test_deep_discover_rollback_is_noop():
    from app.connectors.executors.nexplane_agent.deep_discover import rollback

    result = await rollback({}, {}, connector=None)
    assert result["rolled_back"] is True
