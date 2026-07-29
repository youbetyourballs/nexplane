# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch
from app.connectors.executors.offboard_user.steps import verify_disabled


def _connector_no_creds():
    class C:
        credentials = {}
    return C()


@pytest.mark.asyncio
async def test_no_creds_simulated_pass():
    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory", "account_identifier": "alice"},
        _connector_no_creds(),
    )
    assert result["verified"] is True
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_unknown_connector_type_fails():
    class FakeCon:
        credentials = {"some": "cred"}
    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "unknown_system", "account_identifier": "alice"},
        FakeCon(),
    )
    assert result["verified"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_rollback_is_noop():
    result = await verify_disabled.rollback({}, {}, _connector_no_creds())
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_ad_disabled_passes_on_first_check(monkeypatch):
    """_check_ad returns is_disabled=True immediately — verify passes with 1 attempt."""
    async def _mock_check(account_identifier, creds):
        return {"is_disabled": True, "userAccountControl": 514}

    monkeypatch.setattr(verify_disabled, "_CHECKERS", {"active_directory": _mock_check})
    monkeypatch.setattr(verify_disabled, "_DELAYS", [])  # skip sleep in unit test

    class FakeCon:
        credentials = {"base_dn": "DC=corp,DC=local"}

    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory", "account_identifier": "alice"},
        FakeCon(),
    )
    assert result["verified"] is True
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_verify_fails_after_all_retries(monkeypatch):
    """_check_ad always returns is_disabled=False — verify fails with error."""
    async def _mock_check(account_identifier, creds):
        return {"is_disabled": False, "userAccountControl": 512}

    monkeypatch.setattr(verify_disabled, "_CHECKERS", {"active_directory": _mock_check})
    monkeypatch.setattr(verify_disabled, "_DELAYS", [0, 0])  # instant retries in test

    class FakeCon:
        credentials = {"base_dn": "DC=corp,DC=local"}

    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory", "account_identifier": "alice"},
        FakeCon(),
    )
    assert result["verified"] is False
    assert "still active" in result["error"]
