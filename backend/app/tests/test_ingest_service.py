import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ingest_service import IngestService
from app.models.asset import AssetType, Environment, Criticality


@pytest.mark.asyncio
async def test_ingest_creates_new_asset():
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=[
        {
            "name": "web-server-01",
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["crowdstrike-managed"],
            "asset_metadata": {"os": "Ubuntu 22.04"},
        }
    ])

    mock_catalog = MagicMock()
    mock_catalog.get_action_def.return_value = {
        "action_id": "discover_endpoints",
        "action_type": "ingest",
        "executor": "crowdstrike.discover_endpoints",
    }
    mock_catalog.get_executor.return_value = mock_executor

    mock_connector = MagicMock()
    mock_connector.connector_type = "crowdstrike"

    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    service = IngestService(mock_catalog)
    result = await service.run("discover_endpoints", mock_connector, org_id, mock_db)

    assert result["created"] == 1
    assert result["updated"] == 0
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_updates_existing_asset():
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    from app.models.asset import Asset
    existing = Asset(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="web-server-01",
        asset_type=AssetType.server,
        environment=Environment.prod,
        criticality=Criticality.low,
        asset_metadata={},
        tags=[],
    )

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=[
        {
            "name": "web-server-01",
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["crowdstrike-managed"],
            "asset_metadata": {"os": "Ubuntu 22.04"},
        }
    ])

    mock_catalog = MagicMock()
    mock_catalog.get_action_def.return_value = {"action_type": "ingest", "executor": "crowdstrike.discover_endpoints"}
    mock_catalog.get_executor.return_value = mock_executor

    mock_connector = MagicMock()
    mock_connector.connector_type = "crowdstrike"

    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    service = IngestService(mock_catalog)
    result = await service.run("discover_endpoints", mock_connector, org_id, mock_db)

    assert result["created"] == 0
    assert result["updated"] == 1
    assert existing.criticality == Criticality.high
    assert "crowdstrike-managed" in existing.tags


@pytest.mark.asyncio
async def test_ingest_raises_for_change_action():
    mock_catalog = MagicMock()
    mock_catalog.get_action_def.return_value = {"action_type": "change", "executor": "aws.create_snapshot"}
    mock_connector = MagicMock()
    mock_db = AsyncMock(spec=AsyncSession)

    service = IngestService(mock_catalog)
    with pytest.raises(ValueError, match="not an ingest action"):
        await service.run("create_snapshot", mock_connector, uuid.uuid4(), mock_db)

