# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.identity_resolution import resolve_consumer_identity, IdentityResolutionResult


@pytest.mark.asyncio
async def test_tier1_matches_external_id():
    """Stable cloud ID matches Asset.asset_metadata['arn'] or similar."""
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    mock_db = AsyncMock()

    # Mock: asset found by external_id in asset_metadata
    mock_asset = MagicMock()
    mock_asset.id = asset_id
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_asset
    mock_db.execute.return_value = mock_result

    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={
            "stable_id": "arn:aws:lambda:us-east-1:123:function:my-func",
            "hostname": None,
            "surface_metadata": {},
        },
    )
    assert result.tier == 1
    assert result.asset_id == asset_id
    assert result.confidence >= 0.99


@pytest.mark.asyncio
async def test_tier4_creates_new_asset_data():
    """No match at any tier returns tier=4 with new_asset_data populated."""
    org_id = uuid.uuid4()
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={
            "stable_id": None,
            "hostname": "unknown-host-xyz.internal",
            "surface_metadata": {},
        },
    )
    assert result.tier == 4
    assert result.asset_id is None
    assert result.new_asset_data is not None
    assert "name" in result.new_asset_data
