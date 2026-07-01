# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _make_connector(creds=None):
    mock_conn = MagicMock()
    if creds is None:
        mock_conn.credentials = {
            "sync_server_url": "http://moroz.test",
            "auth_token": "tok",
            "default_machine_group": "default",
        }
    else:
        mock_conn.credentials = creds
    return mock_conn


@pytest.mark.asyncio
async def test_execute_stores_snapshot_before():
    from app.connectors.executors.santa_sync_server import santa_push_rules

    existing_rules = [
        {"identifier": "existing", "rule_type": "allowlist", "identifier_type": "binary", "custom_message": ""}
    ]
    new_rules = [{"rule_type": "denylist", "identifier_type": "binary", "identifier": "newsha"}]

    with patch("app.connectors.executors.santa_sync_server.santa_push_rules.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_rules = AsyncMock(return_value=existing_rules)
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "merge"})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_push_rules.execute({"rules": new_rules}, [], _make_connector())

    assert result["pushed"] == 1
    assert result["snapshot_before"] == existing_rules
    assert result["mode"] == "merge"


@pytest.mark.asyncio
async def test_rollback_restores_snapshot():
    from app.connectors.executors.santa_sync_server import santa_push_rules

    snapshot = [{"identifier": "old", "rule_type": "allowlist", "identifier_type": "binary", "custom_message": ""}]
    execution_result = {"snapshot_before": snapshot, "machine_group": "default"}

    with patch("app.connectors.executors.santa_sync_server.santa_push_rules.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "replace"})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_push_rules.rollback({}, execution_result, _make_connector())

    assert result["rolled_back"] is True
    mock_instance.push_rules.assert_called_once()
    call_kwargs = mock_instance.push_rules.call_args
    assert call_kwargs.kwargs.get("mode") == "replace" or call_kwargs.args[2] == "replace"


@pytest.mark.asyncio
async def test_execute_no_creds_returns_mock():
    from app.connectors.executors.santa_sync_server import santa_push_rules

    result = await santa_push_rules.execute(
        {"rules": [{"rule_type": "denylist", "identifier_type": "binary", "identifier": "abc"}]},
        [],
        _make_connector(creds={}),
    )
    assert result["pushed"] == 0
