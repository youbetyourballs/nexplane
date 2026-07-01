# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.identity_resolution import resolve_user_across_connectors, _email_filter_for_connector


def test_email_filter_active_directory():
    clause = _email_filter_for_connector("active_directory", "alice@corp.com")
    assert clause is not None


def test_email_filter_okta():
    clause = _email_filter_for_connector("okta", "alice@corp.com")
    assert clause is not None


def test_email_filter_unknown_connector():
    clause = _email_filter_for_connector("unknown_type", "alice@corp.com")
    assert clause is None


@pytest.mark.asyncio
async def test_resolve_returns_empty_when_no_connectors():
    """When no connectors exist for the org, result should be empty."""
    db = AsyncMock()
    # First execute call returns empty scalars (no connectors)
    connectors_result = MagicMock()
    connectors_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=connectors_result)

    results = await resolve_user_across_connectors(db, "alice@corp.com", uuid.uuid4())
    assert results == []


@pytest.mark.asyncio
async def test_resolve_skips_unknown_connector_types():
    """Connectors with types not in the email path map should be skipped."""
    db = AsyncMock()

    # Build a fake connector with an unknown type
    fake_connector = MagicMock()
    fake_connector.connector_type = "unknown_system"
    fake_connector.id = uuid.uuid4()
    fake_connector.organization_id = uuid.uuid4()

    connectors_result = MagicMock()
    connectors_result.scalars.return_value.all.return_value = [fake_connector]
    db.execute = AsyncMock(return_value=connectors_result)

    results = await resolve_user_across_connectors(db, "alice@corp.com", uuid.uuid4())
    assert results == []
