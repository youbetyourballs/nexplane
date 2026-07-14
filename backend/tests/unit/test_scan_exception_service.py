# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_dismiss_exception_requires_reason():
    from app.services.scan_exception_service import dismiss_exception
    with pytest.raises(ValueError, match="reason"):
        await dismiss_exception(uuid.uuid4(), "", AsyncMock(), uuid.uuid4())


@pytest.mark.asyncio
async def test_dismiss_exception_sets_status():
    from app.services.scan_exception_service import dismiss_exception

    org_id = uuid.uuid4()
    mock_exception = MagicMock()
    mock_exception.organization_id = org_id
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_exception
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    result = await dismiss_exception(uuid.uuid4(), "intentional — read-only replica", mock_db, org_id)

    assert mock_exception.status == "dismissed"
    assert "read-only replica" in mock_exception.resolution_notes
    assert result["status"] == "dismissed"


@pytest.mark.asyncio
async def test_list_exceptions_filters_by_status():
    from app.services.scan_exception_service import list_exceptions

    org_id = uuid.uuid4()
    pending_exc = MagicMock()
    pending_exc.status = "pending"

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [pending_exc]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    results = await list_exceptions(mock_db, org_id, status="pending")

    assert len(results) == 1
    assert results[0].status == "pending"
    mock_db.execute.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_with_update_cr_creates_draft_cr():
    from app.services.scan_exception_service import resolve_with_update_cr

    org_id = uuid.uuid4()
    exception_id = uuid.uuid4()

    mock_exception = MagicMock()
    mock_exception.organization_id = org_id
    mock_exception.consumer_asset_id = uuid.uuid4()
    mock_exception.location = "Dockerfile"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_exception
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    update_cr_params = {"new_image": "nginx:1.25"}
    result = await resolve_with_update_cr(exception_id, update_cr_params, mock_db, org_id)

    # A new CR must have been added (not just linked)
    mock_db.add.assert_called_once()
    added_cr = mock_db.add.call_args[0][0]
    from app.models.change_request import ChangeType, ChangeRequestStatus
    assert added_cr.change_type == ChangeType.update_reference
    assert added_cr.status == ChangeRequestStatus.draft
    assert added_cr.desired_outcome == update_cr_params
    assert mock_exception.status == "resolved"
    assert result["status"] == "resolved"
    assert "cr_id" in result


@pytest.mark.asyncio
async def test_resolve_exception_unknown_path_raises():
    from app.services.scan_exception_service import resolve_exception

    org_id = uuid.uuid4()
    mock_db = AsyncMock()

    with pytest.raises(ValueError, match="Unknown resolution path"):
        await resolve_exception(mock_db, org_id, uuid.uuid4(), {"path": "invalid-path"})


@pytest.mark.asyncio
async def test_resolve_exception_dismiss_with_reason():
    from app.services.scan_exception_service import resolve_exception

    org_id = uuid.uuid4()
    exception_id = uuid.uuid4()

    mock_exception = MagicMock()
    mock_exception.organization_id = org_id
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_exception
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    result = await resolve_exception(mock_db, org_id, exception_id, {"path": "dismiss-with-reason", "reason": "Not applicable to this environment"})

    assert mock_exception.status == "dismissed"
    assert "Not applicable" in mock_exception.resolution_notes
    assert result["status"] == "dismissed"
