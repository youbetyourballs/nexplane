import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_discover_api_key_consumers_finds_references():
    from app.workers.credential_expiry_worker import _discover_api_key_consumers
    import uuid

    asset = MagicMock()
    asset.id = uuid.uuid4()
    asset.name = "my-server"
    asset.asset_metadata = {"env_vars": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"}

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=mock_result)

    consumers = await _discover_api_key_consumers(db, "AKIAIOSFODNN7EXAMPLE", "aws_iam_key")
    assert len(consumers) == 1
    assert consumers[0]["asset_name"] == "my-server"
    assert consumers[0]["config_path"] == "env_vars"


@pytest.mark.asyncio
async def test_discover_api_key_consumers_no_match():
    from app.workers.credential_expiry_worker import _discover_api_key_consumers

    asset = MagicMock()
    asset.asset_metadata = {"env_vars": "OTHER_KEY=xyz"}

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=mock_result)

    consumers = await _discover_api_key_consumers(db, "AKIAIOSFODNN7EXAMPLE", "aws_iam_key")
    assert consumers == []


@pytest.mark.asyncio
async def test_check_vault_leases_renews_renewable():
    from app.workers.credential_expiry_worker import _check_vault_leases

    connector = MagicMock()
    connector.id = "vault-conn-1"
    db = AsyncMock(spec=AsyncSession)

    short_ttl_lease = {"lease_id": "database/creds/my-role/abc", "ttl": 3600, "renewable": True}

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", new=AsyncMock(return_value=[connector])), \
         patch("app.connectors.executors.hashicorp_vault._client.VaultClient") as mock_cls:

        mock_client = MagicMock()
        mock_client.list_leases.return_value = [short_ttl_lease]
        mock_cls.from_connector.return_value = mock_client

        await _check_vault_leases(db)
        mock_client.renew_lease.assert_called_once_with(short_ttl_lease["lease_id"])


@pytest.mark.asyncio
async def test_check_vault_leases_creates_finding_when_not_renewable():
    from app.workers.credential_expiry_worker import _check_vault_leases

    connector = MagicMock()
    connector.id = "vault-conn-1"
    db = AsyncMock(spec=AsyncSession)

    expired_lease = {"lease_id": "pki/issue/my-role/xyz", "ttl": 3600, "renewable": False}

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", new=AsyncMock(return_value=[connector])), \
         patch("app.connectors.executors.hashicorp_vault._client.VaultClient") as mock_cls, \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        mock_client = MagicMock()
        mock_client.list_leases.return_value = [expired_lease]
        mock_cls.from_connector.return_value = mock_client

        await _check_vault_leases(db)
        mock_client.renew_lease.assert_not_called()
        mock_finding.assert_called_once()
