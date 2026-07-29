# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.connectors.executors.offboard_user.steps import discover_accounts


def _connector_no_creds():
    class C:
        credentials = {}
    return C()


@pytest.mark.asyncio
async def test_no_creds_returns_not_found():
    result = await discover_accounts.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory"},
        _connector_no_creds(),
    )
    assert result["found"] is False
    assert result["simulated"] is True
    assert result["connector_type"] == "active_directory"
    assert result["account_identifier"] is None


@pytest.mark.asyncio
async def test_unknown_connector_type_returns_not_found():
    result = await discover_accounts.execute(
        {"target_email": "alice@corp.com", "connector_type": "unknown_system"},
        _connector_no_creds(),
    )
    assert result["found"] is False
    assert "error" in result["details"]


@pytest.mark.asyncio
async def test_rollback_is_noop():
    result = await discover_accounts.rollback({}, {}, _connector_no_creds())
    assert result["skipped"] is True
