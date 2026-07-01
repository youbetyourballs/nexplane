# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_remove_from_groups_mock():
    from app.connectors.executors.google_workspace.remove_from_groups import execute
    result = await execute({"user_email": "alice@example.com"}, [], MockConnector())
    assert result["action"] == "remove_from_groups"
    assert result["user_email"] == "alice@example.com"
    assert isinstance(result["groups_removed"], list)


@pytest.mark.asyncio
async def test_reset_2fa_mock():
    from app.connectors.executors.google_workspace.reset_2fa import execute
    result = await execute({"user_email": "alice@example.com"}, [], MockConnector())
    assert result["action"] == "reset_2fa"
    assert result["user_email"] == "alice@example.com"
    assert result["reset"] is True


@pytest.mark.asyncio
async def test_revoke_oauth_tokens_mock():
    from app.connectors.executors.google_workspace.revoke_oauth_tokens import execute
    result = await execute({"user_email": "alice@example.com"}, [], MockConnector())
    assert result["action"] == "revoke_oauth_tokens"
    assert isinstance(result["tokens_revoked"], list)


@pytest.mark.asyncio
async def test_wipe_mobile_device_mock():
    from app.connectors.executors.google_workspace.wipe_mobile_device import execute
    result = await execute(
        {"user_email": "alice@example.com", "resource_id": "device123", "confirm_wipe": True},
        [],
        MockConnector(),
    )
    assert result["action"] == "wipe_mobile_device"
    assert result["wiped"] is True


@pytest.mark.asyncio
async def test_wipe_mobile_device_requires_confirm():
    from app.connectors.executors.google_workspace.wipe_mobile_device import execute
    result = await execute(
        {"user_email": "alice@example.com", "confirm_wipe": False},
        [],
        MockConnector(),
    )
    assert result.get("error") or result.get("skipped"), "should refuse without confirm_wipe"


@pytest.mark.asyncio
async def test_remove_from_groups_rollback():
    from app.connectors.executors.google_workspace.remove_from_groups import rollback
    result = await rollback(
        {"user_email": "alice@example.com"},
        {"groups_removed": ["group1@example.com"]},
        MockConnector(),
    )
    assert "rolled_back" in result or "action" in result
