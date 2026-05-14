import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_rotate_ssh_keys_executor_exists():
    from app.connectors.executors.nexplane_agent.rotate_ssh_keys import execute, rollback
    assert callable(execute)
    assert callable(rollback)


def test_rotate_db_creds_executor_exists():
    from app.connectors.executors.nexplane_agent.rotate_db_creds import execute
    assert callable(execute)


@pytest.mark.asyncio
async def test_check_prerequisites_returns_mock_without_creds():
    from app.connectors.executors.ssh.check_prerequisites import execute
    result = await execute({}, ["asset-1"], None)
    assert result["action"] == "check_prerequisites"
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_start_service_returns_mock_without_creds():
    from app.connectors.executors.ssh.start_service import execute
    result = await execute({"service_name": "nexplane-agent"}, ["asset-1"], None)
    assert result["action"] == "start_service"
    assert result["hosts"][0]["started"] is True
