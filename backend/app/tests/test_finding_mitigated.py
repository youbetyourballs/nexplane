# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid


@pytest.mark.asyncio
async def test_mitigate_finding_sets_state():
    from app.services.finding_service import mitigate_finding

    mock_finding = MagicMock()
    mock_finding.status = "open"
    mock_finding.mitigated_at = None
    mock_finding.id = uuid.uuid4()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_finding

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    finding = await mitigate_finding(mock_db, str(mock_finding.id))
    assert finding.status == "mitigated"
    assert finding.mitigated_at is not None


def test_vulnerability_finding_has_mitigated_fields():
    from app.models.vulnerability import VulnerabilityFinding
    assert hasattr(VulnerabilityFinding, "mitigated_at")
    assert hasattr(VulnerabilityFinding, "mitigated_by_cr_id")
    assert hasattr(VulnerabilityFinding, "patch_available_at")
