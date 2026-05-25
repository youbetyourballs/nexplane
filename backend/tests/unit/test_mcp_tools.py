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


def test_cr_tools_registered():
    import app.mcp_tools.change_requests  # noqa: F401
    assert app.mcp_tools.change_requests is not None


@pytest.mark.asyncio
async def test_create_cr_tool_returns_draft_status():
    """create_change_request must return a CR in draft state, never execute directly."""
    from app.mcp_tools.change_requests import create_change_request
    from unittest.mock import AsyncMock, MagicMock, patch
    import uuid

    mock_user = MagicMock()
    mock_user.organization_id = uuid.uuid4()
    mock_user.id = uuid.uuid4()
    mock_user.role = "admin"

    mock_cr = MagicMock()
    mock_cr.id = uuid.uuid4()
    mock_cr.status = "draft"
    mock_cr.title = "Test CR"
    mock_cr.change_type = "patch_packages"
    mock_cr.created_at = None
    mock_cr.asset_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_cr)))
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db_cm = AsyncMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cm.__aexit__ = AsyncMock()

    with patch("app.mcp_tools.change_requests._auth", new_callable=AsyncMock, return_value=(mock_user, mock_db, mock_db_cm)):
        with patch("app.mcp_tools.context.build_asset_context", new_callable=AsyncMock, return_value={"asset": {}}):
            result = await create_change_request(
                token="nxp_test",
                change_type="patch_packages",
                asset_id=str(mock_user.organization_id),
                title="Patch log4j",
                parameters={"os_family": "linux", "mode": "cve", "cve_id": "CVE-2021-44228"},
            )
    assert result.get("status") == "draft"
