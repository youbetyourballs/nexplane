import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_build_asset_context_returns_required_keys():
    from app.mcp_tools.context import build_asset_context

    mock_asset = MagicMock()
    mock_asset.id = uuid.uuid4()
    mock_asset.name = "web-prod-01"
    mock_asset.ip_address = "10.0.1.50"
    mock_asset.asset_type = "server"
    mock_asset.environment = "prod"
    mock_asset.criticality = "critical"
    mock_asset.os_type = "linux"
    mock_asset.connector_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

    result = await build_asset_context(mock_asset.id, db)
    assert "asset" in result
    assert "open_findings" in result
    assert "recent_change_requests" in result
    assert "recent_timeline_events" in result
    assert "connected_connectors" in result
    assert "installed_software" in result
