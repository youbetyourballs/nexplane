import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_disable_user_skips_without_credentials():
    from app.connectors.executors.azure_ad.disable_user import execute
    result = await execute({"user_identifier": "user@example.com"}, ["asset-1"], None)
    assert result["status"] == "skipped"
    assert result["reason"] == "no_azure_ad_credentials"


@pytest.mark.asyncio
async def test_create_user_skips_without_credentials():
    from app.connectors.executors.azure_ad.create_user import execute
    result = await execute({"user_principal_name": "new@example.com"}, ["asset-1"], None)
    assert result["status"] == "skipped"
    assert result["reason"] == "no_azure_ad_credentials"


@pytest.mark.asyncio
async def test_disable_user_raises_without_identifier():
    from app.connectors.executors.azure_ad.disable_user import execute
    with pytest.raises(ValueError, match="user_identifier is required"):
        await execute({}, ["asset-1"], None)


@pytest.mark.asyncio
async def test_emergency_lockout_includes_azure_ad():
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute
    mock_aad = AsyncMock(return_value={"user": "test", "accountEnabled": False, "status": "skipped", "reason": "no_azure_ad_credentials"})
    mock_iam = AsyncMock(side_effect=Exception("AWS not configured"))
    with patch("app.connectors.executors.azure_ad.disable_user.execute", mock_aad), \
         patch("app.connectors.executors.aws.lock_iam_user.execute", mock_iam):
        result = await execute(
            {"user_identifier": "user@example.com", "systems": ["azure_ad"]},
            ["asset-1"], None
        )
    assert result["lockout_status"]["azure_ad"] == "locked"


def test_credential_expiry_worker_importable():
    from app.workers.credential_expiry_worker import check_credential_expiry
    assert callable(check_credential_expiry)


def test_azure_ad_client_requires_all_creds():
    from app.connectors.executors.azure_ad.azure_ad_client import get_azure_ad_client
    # None connector -> no client
    assert get_azure_ad_client(None) is None
    # Partial creds -> no client
    mock_connector = MagicMock()
    mock_connector.credentials = {"tenant_id": "t", "client_id": "c"}
    assert get_azure_ad_client(mock_connector) is None
    # Full creds -> client
    mock_connector.credentials = {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
    client = get_azure_ad_client(mock_connector)
    assert client is not None
    assert client.tenant_id == "t"
