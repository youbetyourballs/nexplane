import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
    return c


@pytest.mark.asyncio
async def test_discover_users_returns_users():
    from backend.app.connectors.executors.azure_ad.discover_users import execute
    mock_client = AsyncMock()
    mock_client.list_users = AsyncMock(return_value=[
        {"id": "u1", "displayName": "Alice", "userPrincipalName": "alice@test.com", "accountEnabled": True}
    ])
    with patch("backend.app.connectors.executors.azure_ad.discover_users.get_azure_ad_client", return_value=mock_client):
        result = await execute({}, ["asset-1"], _make_connector())
    assert result["users"][0]["id"] == "u1"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_discover_users_no_creds_skipped():
    from backend.app.connectors.executors.azure_ad.discover_users import execute
    with patch("backend.app.connectors.executors.azure_ad.discover_users.get_azure_ad_client", return_value=None):
        result = await execute({}, [], _make_connector(creds={}))
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_get_group_membership_returns_groups():
    from backend.app.connectors.executors.azure_ad.get_group_membership import execute
    mock_client = AsyncMock()
    mock_client.get_group_membership = AsyncMock(return_value=[
        {"id": "g1", "displayName": "Security Team"}
    ])
    with patch("backend.app.connectors.executors.azure_ad.get_group_membership.get_azure_ad_client", return_value=mock_client):
        result = await execute({"user_id": "u1"}, ["asset-1"], _make_connector())
    assert result["groups"][0]["id"] == "g1"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_get_group_membership_no_user_id_raises():
    from backend.app.connectors.executors.azure_ad.get_group_membership import execute
    mock_client = AsyncMock()
    with patch("backend.app.connectors.executors.azure_ad.get_group_membership.get_azure_ad_client", return_value=mock_client):
        with pytest.raises(ValueError, match="user_id is required"):
            await execute({}, [], _make_connector())


@pytest.mark.asyncio
async def test_get_group_membership_no_creds_skipped():
    from backend.app.connectors.executors.azure_ad.get_group_membership import execute
    with patch("backend.app.connectors.executors.azure_ad.get_group_membership.get_azure_ad_client", return_value=None):
        result = await execute({"user_id": "u1"}, [], _make_connector(creds={}))
    assert result["status"] == "skipped"
