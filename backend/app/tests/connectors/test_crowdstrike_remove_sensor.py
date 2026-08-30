# SMOKE: requires live CrowdStrike tenant
import pytest
from unittest.mock import MagicMock, patch


def _make_connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {"client_id": "cid", "client_secret": "cs"}
    return c


@pytest.mark.asyncio
async def test_calls_hide_hosts_not_mock():
    mock_hosts = MagicMock()
    mock_hosts.QueryDevicesByFilterScroll.return_value = {
        "status_code": 200,
        "body": {"resources": ["device-abc123"]},
    }
    mock_hosts.hide_hosts.return_value = {
        "status_code": 200,
        "body": {"resources": [{"id": "device-abc123"}]},
    }

    with patch("app.connectors.executors.crowdstrike.remove_sensor._get_hosts_api", return_value=mock_hosts):
        from app.connectors.executors.crowdstrike.remove_sensor import execute
        result = await execute({"hostname": "test-host"}, ["asset-1"], _make_connector())

    mock_hosts.hide_hosts.assert_called_once()
    assert result["status"] == "removed"
    assert "device_ids" in result


@pytest.mark.asyncio
async def test_returns_error_when_host_not_found():
    mock_hosts = MagicMock()
    mock_hosts.QueryDevicesByFilterScroll.return_value = {
        "status_code": 200,
        "body": {"resources": []},
    }

    with patch("app.connectors.executors.crowdstrike.remove_sensor._get_hosts_api", return_value=mock_hosts):
        from app.connectors.executors.crowdstrike.remove_sensor import execute
        result = await execute({"hostname": "unknown-host"}, ["asset-1"], _make_connector())

    assert result.get("status") == "error" or result.get("skipped") is True


@pytest.mark.asyncio
async def test_no_credentials_returns_error():
    c = MagicMock()
    c.credentials = {}
    from app.connectors.executors.crowdstrike.remove_sensor import execute
    result = await execute({}, ["asset-1"], c)
    assert result.get("status") == "error"
