import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _conn(creds=None):
    m = MagicMock()
    m.credentials = creds or {"sync_server_url": "http://moroz.test", "auth_token": "tok"}
    return m


@pytest.mark.asyncio
async def test_machine_list_returns_machines():
    from app.connectors.executors.santa_sync_server import santa_machine_list

    machines = [{"machine_id": "hw-1", "hostname": "mac1", "os_version": "14.0", "santa_version": "2024.1", "last_sync": "2026-01-01T00:00:00Z", "rule_count": 5}]
    with patch("app.connectors.executors.santa_sync_server.santa_machine_list.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.list_machines = AsyncMock(return_value=machines)
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_list.execute({}, [], _conn())

    assert result["machine_count"] == 1
    assert result["machines"][0]["machine_id"] == "hw-1"


@pytest.mark.asyncio
async def test_machine_list_graceful_empty():
    from app.connectors.executors.santa_sync_server import santa_machine_list

    with patch("app.connectors.executors.santa_sync_server.santa_machine_list.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.list_machines = AsyncMock(return_value=[])
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_list.execute({}, [], _conn())

    assert result["machine_count"] == 0


@pytest.mark.asyncio
async def test_machine_group_assign_captures_previous():
    from app.connectors.executors.santa_sync_server import santa_machine_group_assign

    with patch("app.connectors.executors.santa_sync_server.santa_machine_group_assign.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.list_machines = AsyncMock(return_value=[
            {"machine_id": "hw-1", "hostname": "mac1", "os_version": "", "santa_version": "", "last_sync": "", "rule_count": 0}
        ])
        mock_instance.assign_machine_group = AsyncMock(return_value={})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_group_assign.execute(
            {"machine_id": "hw-1", "target_group": "quarantine"}, [], _conn()
        )

    assert result["machine_id"] == "hw-1"
    assert result["new_group"] == "quarantine"


@pytest.mark.asyncio
async def test_machine_group_assign_rollback():
    from app.connectors.executors.santa_sync_server import santa_machine_group_assign

    execution_result = {"machine_id": "hw-1", "previous_group": "default", "new_group": "quarantine"}
    with patch("app.connectors.executors.santa_sync_server.santa_machine_group_assign.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.assign_machine_group = AsyncMock(return_value={})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_group_assign.rollback({}, execution_result, _conn())

    assert result["rolled_back"] is True
    mock_instance.assign_machine_group.assert_called_once_with("hw-1", "default")
