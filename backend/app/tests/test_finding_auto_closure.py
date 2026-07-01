# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid


@pytest.mark.asyncio
async def test_close_linked_findings_marks_remediated():
    from app.services.finding_service import close_linked_findings

    finding_id = str(uuid.uuid4())
    cr_id = str(uuid.uuid4())

    mock_finding = MagicMock()
    mock_finding.status = "open"

    mock_cr = MagicMock()
    mock_cr.finding_ids = [finding_id]

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=mock_cr)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_finding
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    count = await close_linked_findings(mock_db, cr_id)
    assert count == 1
    assert mock_finding.status == "remediated"
    assert mock_finding.remediated_at is not None
