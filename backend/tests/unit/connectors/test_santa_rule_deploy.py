import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import time


def _conn():
    m = MagicMock()
    m.credentials = {"sync_server_url": "http://moroz.test", "auth_token": "tok", "default_machine_group": "default"}
    return m


@pytest.mark.asyncio
async def test_deploy_pushes_and_verifies():
    from app.connectors.executors.santa_sync_server import santa_rule_deploy

    push_time = time.time()
    machines_before = [{"machine_id": "hw-1", "last_sync": "2020-01-01T00:00:00Z"}]
    machines_after = [{"machine_id": "hw-1", "last_sync": "2099-01-01T00:00:00Z"}]

    with patch("app.connectors.executors.santa_sync_server.santa_rule_deploy.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_rules = AsyncMock(return_value=[])
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "merge"})
        mock_instance.list_machines = AsyncMock(side_effect=[machines_before, machines_after])
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_rule_deploy.execute(
            {
                "rule_type": "denylist",
                "identifier_type": "binary",
                "identifier": "abc123sha256",
                "verify_timeout_seconds": 5,
            },
            [],
            _conn(),
        )

    assert result["deployed"] is True
    assert result["verified_machines"] >= 0


@pytest.mark.asyncio
async def test_deploy_rollback_removes_rule():
    from app.connectors.executors.santa_sync_server import santa_rule_deploy

    execution_result = {
        "identifier": "abc123sha256",
        "identifier_type": "binary",
        "rule_type": "denylist",
        "previous_state": "absent",
        "machine_group": "default",
        "snapshot_before": [],
    }
    with patch("app.connectors.executors.santa_sync_server.santa_rule_deploy.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 0, "machine_group": "default", "mode": "replace"})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_rule_deploy.rollback({}, execution_result, _conn())

    assert result["rolled_back"] is True
