# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {"tenant_id": "t1", "client_id": "c1", "client_secret": "s1", "subscription_id": "sub1"}
    return c


@pytest.mark.asyncio
async def test_upgrade_and_partial_rollback(connector):
    mock_cache = MagicMock()
    mock_cache.redis_version = "6"
    mock_cache.provisioning_state = "Succeeded"

    call_count = [0]
    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_cache
        upgraded = MagicMock()
        upgraded.redis_version = "7"
        upgraded.provisioning_state = "Succeeded"
        return upgraded

    with patch("app.connectors.executors.azure.azure_cache_redis_upgrade._run", side_effect=fake_run):
        with patch("app.connectors.executors.azure.azure_cache_redis_upgrade._wait_ready",
                   new=AsyncMock(return_value="7")):
            from app.connectors.executors.azure.azure_cache_redis_upgrade import execute
            result = await execute(
                {"resource_group": "rg1", "cache_name": "my-redis", "target_version": "7"},
                [], connector,
            )
    assert result["status"] == "upgraded"
    assert result["previous_version"] == "6"

    from app.connectors.executors.azure.azure_cache_redis_upgrade import rollback
    rb = await rollback({}, result, connector)
    assert rb["rolled_back"] is False
