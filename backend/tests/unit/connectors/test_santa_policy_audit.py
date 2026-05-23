import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_execute_returns_rules():
    from app.connectors.executors.santa_sync_server import santa_policy_audit

    mock_conn = MagicMock()
    mock_conn.credentials = {
        "sync_server_url": "http://moroz.test",
        "auth_token": "tok",
        "default_machine_group": "default",
    }
    mock_rules = [
        {"identifier": "abc123", "rule_type": "denylist", "identifier_type": "binary", "custom_message": ""}
    ]
    with patch("app.connectors.executors.santa_sync_server.santa_policy_audit.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_rules = AsyncMock(return_value=mock_rules)
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_policy_audit.execute({}, [], mock_conn)

    assert result["rule_count"] == 1
    assert result["rules"][0]["identifier"] == "abc123"
    assert result["machine_group"] == "default"


@pytest.mark.asyncio
async def test_execute_no_creds_returns_mock():
    from app.connectors.executors.santa_sync_server import santa_policy_audit

    mock_conn = MagicMock()
    mock_conn.credentials = {}
    result = await santa_policy_audit.execute({}, [], mock_conn)
    assert "rules" in result
    assert result["rule_count"] == 0
