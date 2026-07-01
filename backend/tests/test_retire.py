# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Tests for containerize_retire executor and retire_service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


class TestRetireService:
    @pytest.mark.asyncio
    async def test_marks_asset_retired(self):
        from app.services.retire_service import mark_asset_retired
        from app.models.asset import Asset

        mock_asset = MagicMock(spec=Asset)
        mock_asset.asset_metadata = {
            "applications": [{"name": "nexplane-smoketest", "containerization_status": "deployed"}]
        }

        mock_db = AsyncMock()
        mock_db.get.return_value = mock_asset

        asset_id = str(uuid.uuid4())
        execution_result = {
            "steps": [{
                "result": {
                    "action": "containerize_retire",
                    "systemd_unit": "nexplane-smoketest.service",
                    "service_stopped": True,
                }
            }]
        }

        await mark_asset_retired(mock_db, [asset_id], execution_result)
        apps = mock_asset.asset_metadata.get("applications", [])
        assert apps[0]["containerization_status"] == "retired"

    @pytest.mark.asyncio
    async def test_noop_when_service_not_stopped(self):
        from app.services.retire_service import mark_asset_retired
        mock_db = AsyncMock()
        await mark_asset_retired(mock_db, [], {"steps": [{"result": {"action": "containerize_retire", "service_stopped": False}}]})
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_executor_dispatches_unit(self):
        from app.connectors.executors.nexplane_agent.containerize_retire import execute
        connector = MagicMock()
        asset_id = str(uuid.uuid4())

        with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job") as mock_dispatch:
            mock_dispatch.return_value = {"action": "containerize_retire", "service_stopped": False, "dry_run": True}
            result = await execute({"systemd_unit": "nexplane-smoketest.service", "dry_run": True}, [asset_id], connector)

        assert result["action"] == "containerize_retire"
