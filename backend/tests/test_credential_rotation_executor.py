# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_execute_steps_completes_all():
    from app.services.credential_rotation_executor import execute_steps

    cr_id = uuid.uuid4()
    mock_step_data = [
        {"connector_type": "aws", "action_id": "rotate_iam_key", "params": {"username": "u"}, "label": "S1"},
    ]

    with patch("app.services.credential_rotation_executor.AsyncSessionLocal") as mock_sl, \
         patch("app.services.credential_rotation_executor.execute_action", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {"rotated": True}

        mock_cr = MagicMock()
        mock_cr.desired_outcome = {"steps": mock_step_data}
        mock_cr.organization_id = uuid.uuid4()

        mock_run = MagicMock()
        mock_run.result = {}

        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one.return_value = mock_cr
        mock_execute_result.scalar_one_or_none.return_value = mock_run

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_execute_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_db

        result = await execute_steps(cr_id)

    assert result["steps"][0]["status"] == "completed"
    assert "paused" not in result


@pytest.mark.asyncio
async def test_execute_steps_pauses_on_failure():
    from app.services.credential_rotation_executor import execute_steps

    cr_id = uuid.uuid4()
    mock_step_data = [
        {"connector_type": "aws", "action_id": "rotate_iam_key", "params": {}, "label": "S1"},
    ]

    with patch("app.services.credential_rotation_executor.AsyncSessionLocal") as mock_sl, \
         patch("app.services.credential_rotation_executor.execute_action", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = RuntimeError("access denied")

        mock_cr = MagicMock()
        mock_cr.desired_outcome = {"steps": mock_step_data}
        mock_cr.organization_id = uuid.uuid4()

        mock_run = MagicMock()
        mock_run.result = {}

        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one.return_value = mock_cr
        mock_execute_result.scalar_one_or_none.return_value = mock_run

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_execute_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_db

        result = await execute_steps(cr_id)

    assert result.get("paused") is True
    assert result["steps"][0]["status"] == "failed"
    assert "access denied" in result["steps"][0]["error"]
