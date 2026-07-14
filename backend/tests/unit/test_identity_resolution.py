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


@pytest.mark.asyncio
async def test_tier2_matches_hostname():
    """Single hostname match returns tier=2 with confidence 0.85."""
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    mock_asset = MagicMock()
    mock_asset.id = asset_id
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # Tier 1 misses
    mock_result.scalars.return_value.all.return_value = [mock_asset]
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={
            "stable_id": None,
            "hostname": "web-01.prod.internal",
            "surface_metadata": {},
        },
    )
    assert result.tier == 2
    assert result.asset_id == asset_id
    assert result.confidence == 0.85


@pytest.mark.asyncio
async def test_tier3_composite_fingerprint_requires_two_signals():
    """Tier 3 only fires when >=2 signals match the same asset."""
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    mock_asset = MagicMock()
    mock_asset.id = asset_id

    # stable_id=None skips Tier 1 (0 queries); hostname=None skips Tier 2 (0 queries).
    # Tier 3 starts at call 1: mac(1), os_type(2), primary_interface_ip(3).
    call_count = 0

    async def execute_side_effect(query):
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()
        if call_count == 1:  # First Tier 3 signal (mac_address) — matches
            mock_result.scalar_one_or_none.return_value = mock_asset
        elif call_count == 2:  # Second Tier 3 signal (os_type) — matches same asset
            mock_result.scalar_one_or_none.return_value = mock_asset
        else:  # Third Tier 3 signal (primary_interface_ip) — misses
            mock_result.scalar_one_or_none.return_value = None
        return mock_result

    mock_db = AsyncMock()
    mock_db.execute.side_effect = execute_side_effect

    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={
            "stable_id": None,
            "hostname": None,
            "surface_metadata": {
                "mac_address": "aa:bb:cc:dd:ee:ff",
                "os_type": "linux",
                "primary_interface_ip": "10.0.0.5",
            },
        },
    )
    assert result.tier == 3
    assert result.asset_id == asset_id
    assert result.confidence == 0.70


@pytest.mark.asyncio
async def test_tier4_all_none_inputs():
    """All-None consumer_identity falls through to Tier 4."""
    org_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={"stable_id": None, "hostname": None, "surface_metadata": {}},
    )
    assert result.tier == 4
    assert result.asset_id is None
    assert result.new_asset_data is not None
