# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


def _make_cr(status="approved", org_id=None, requester_id=None):
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    cr = MagicMock(spec=ChangeRequest)
    cr.id = uuid.uuid4()
    cr.organization_id = org_id or uuid.uuid4()
    cr.requester_id = requester_id or uuid.uuid4()
    cr.status = ChangeRequestStatus(status)
    cr.execution_runs = []
    return cr


@pytest.mark.asyncio
async def test_approved_cr_can_execute():
    from app.services.change_execution_service import ChangeExecutionService

    cr = _make_cr(status="approved")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    with patch("app.services.change_execution_service.workflow_runner") as mock_runner, \
         patch("app.services.change_execution_service.record_event", new_callable=AsyncMock):
        mock_runner.start_workflow = AsyncMock(return_value="wf-id-123")
        mock_runner.WorkflowInput = MagicMock(return_value=MagicMock())
        result = await ChangeExecutionService.start(cr.id, cr.requester_id, "manual", db)
        mock_runner.start_workflow.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_unapproved_cr_cannot_execute():
    from app.services.change_execution_service import ChangeExecutionService
    from fastapi import HTTPException

    cr = _make_cr(status="draft")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)

    with pytest.raises(HTTPException) as exc_info:
        await ChangeExecutionService.start(cr.id, cr.requester_id, "manual", db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_scheduled_source_unapproved_cr_cannot_execute():
    from app.services.change_execution_service import ChangeExecutionService
    from fastapi import HTTPException

    cr = _make_cr(status="planned")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)

    with pytest.raises(HTTPException) as exc_info:
        await ChangeExecutionService.start(cr.id, cr.requester_id, "scheduled", db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_recurring_job_source_unapproved_cr_cannot_execute():
    from app.services.change_execution_service import ChangeExecutionService
    from fastapi import HTTPException

    cr = _make_cr(status="awaiting_approval")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)

    with pytest.raises(HTTPException) as exc_info:
        await ChangeExecutionService.start(cr.id, cr.requester_id, "recurring_job", db)
    assert exc_info.value.status_code == 400
