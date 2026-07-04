# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for _auto_asset_id threading and rollback asset cleanup."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from app.services import connector_service


# ─── _upsert_auto_asset returns the asset ─────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_auto_asset_returns_new_asset():
    db = AsyncMock()
    org_id = uuid.uuid4()

    # Simulate no existing asset found
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.first.return_value = None
    db.execute.return_value = scalar_result

    payload = {
        "name": "test-key",
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {},
        "tags": [],
    }

    # Use the real Asset class — construction doesn't need a DB connection.
    # Only mock db.execute so SQLAlchemy select() runs but returns no existing row.
    result = await connector_service._upsert_auto_asset(payload, org_id, db)

    assert result is not None
    db.add.assert_called()
    db.flush.assert_called()


@pytest.mark.asyncio
async def test_upsert_auto_asset_returns_existing_asset():
    db = AsyncMock()
    org_id = uuid.uuid4()

    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.asset_metadata = {}
    existing.tags = []

    scalar_result = MagicMock()
    scalar_result.scalars.return_value.first.return_value = existing
    db.execute.return_value = scalar_result

    payload = {"name": "test-key", "asset_metadata": {}, "tags": []}
    result = await connector_service._upsert_auto_asset(payload, org_id, db)

    assert result is existing


# ─── execute_action threads _auto_asset_id ────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_action_includes_auto_asset_id():
    mock_asset = MagicMock()
    asset_id = uuid.uuid4()
    mock_asset.id = asset_id

    connector = MagicMock()
    connector.organization_id = uuid.uuid4()
    db = AsyncMock()

    executor = AsyncMock()
    executor.execute.return_value = {
        "_auto_asset": {"name": "my-key", "asset_type": "server", "environment": "prod",
                        "criticality": "medium", "asset_metadata": {}, "tags": []},
        "key_name": "my-key",
    }

    with patch.object(connector_service, "_attach_credentials", AsyncMock()), \
         patch.object(connector_service, "get_catalog_service") as mock_catalog_svc, \
         patch.object(connector_service, "_upsert_auto_asset", AsyncMock(return_value=mock_asset)):
        mock_catalog_svc.return_value.get_executor.return_value = executor
        result = await connector_service.execute_action(
            "aws", "create_key_pair", {"key_name": "my-key"}, [],
            connector=connector, db=db,
        )

    assert "_auto_asset" not in result
    assert result["_auto_asset_id"] == str(asset_id)


@pytest.mark.asyncio
async def test_execute_action_no_auto_asset_no_id_key():
    connector = MagicMock()
    db = AsyncMock()

    executor = AsyncMock()
    executor.execute.return_value = {"key_name": "my-key"}

    with patch.object(connector_service, "_attach_credentials", AsyncMock()), \
         patch.object(connector_service, "get_catalog_service") as mock_catalog_svc:
        mock_catalog_svc.return_value.get_executor.return_value = executor
        result = await connector_service.execute_action(
            "aws", "create_key_pair", {"key_name": "my-key"}, [],
            connector=connector, db=db,
        )

    assert "_auto_asset_id" not in result


# ─── _delete_auto_asset ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_auto_asset_deletes_existing():
    db = AsyncMock()
    asset_id = str(uuid.uuid4())
    mock_asset = MagicMock()

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = mock_asset
    db.execute.return_value = scalar_result

    await connector_service._delete_auto_asset(asset_id, db)

    db.delete.assert_called_once_with(mock_asset)
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_auto_asset_noop_when_not_found():
    db = AsyncMock()
    asset_id = str(uuid.uuid4())

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = None
    db.execute.return_value = scalar_result

    # Should not raise
    await connector_service._delete_auto_asset(asset_id, db)

    db.delete.assert_not_called()
    db.commit.assert_not_called()
