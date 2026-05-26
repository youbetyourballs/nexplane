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
