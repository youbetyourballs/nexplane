# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_update_config_file_dispatches_agent_job():
    from app.connectors.executors.nexplane_agent.update_config_file_reference import execute

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "file_path": "/etc/app/config.conf",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    }
    mock_cr.asset_id = "asset-uuid-456"
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    mock_job_result = {
        "output": '{"status": "updated", "backup_path": "/tmp/nexplane-backup-abc123/config.conf", "replacements": 3}',
        "exit_code": 0,
    }

    with patch(
        "app.connectors.executors.nexplane_agent.update_config_file_reference._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value=mock_job_result,
    ):
        result = await execute(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert "rollback_data" in result
    assert result["rollback_data"]["backup_path"] == "/tmp/nexplane-backup-abc123/config.conf"
    assert result["rollback_data"]["target_path"] == "/etc/app/config.conf"


@pytest.mark.asyncio
async def test_update_config_file_skipped_when_old_value_not_found():
    from app.connectors.executors.nexplane_agent.update_config_file_reference import execute

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "file_path": "/etc/app/config.conf",
        "old_value": "nonexistent-value",
        "new_value": "new-db.internal",
    }
    mock_cr.asset_id = None
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    mock_job_result = {
        "output": '{"status": "skipped", "reason": "old_value not found in file"}',
        "exit_code": 0,
    }

    with patch(
        "app.connectors.executors.nexplane_agent.update_config_file_reference._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value=mock_job_result,
    ):
        result = await execute(mock_cr, mock_connector, mock_db)

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_rollback_dispatches_reference_restore():
    from app.connectors.executors.nexplane_agent.update_config_file_reference import rollback

    mock_cr = MagicMock()
    mock_cr.execution_result = {
        "rollback_data": {
            "backup_path": "/tmp/nexplane-backups/config.conf",
            "target_path": "/etc/app/config.conf",
            "asset_id": "asset-uuid-456",
        }
    }
    mock_connector = MagicMock()
    mock_db = AsyncMock()

    mock_restore_result = {
        "output": '{"status": "restored", "target": "/etc/app/config.conf"}',
        "exit_code": 0,
    }

    with patch(
        "app.connectors.executors.nexplane_agent.update_config_file_reference._dispatch.dispatch_agent_job",
        new_callable=AsyncMock,
        return_value=mock_restore_result,
    ) as mock_dispatch:
        result = await rollback(mock_cr, mock_connector, mock_db)

    assert result["status"] == "rolled_back"
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["command"] == "reference-restore"
    assert call_kwargs["parameters"]["backup-path"] == "/tmp/nexplane-backups/config.conf"
    assert call_kwargs["parameters"]["target-path"] == "/etc/app/config.conf"
    assert "asset-uuid-456" in call_kwargs["asset_ids"]
