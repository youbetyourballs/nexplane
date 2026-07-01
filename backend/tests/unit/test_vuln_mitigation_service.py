# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch

@pytest.mark.asyncio
async def test_suggest_mitigations_heuristic_fallback():
    from app.services.vuln_mitigation_service import suggest_mitigations
    finding = MagicMock()
    finding.id = uuid.uuid4()
    finding.cve_id = "CVE-2024-1234"
    finding.finding_type = "cve"
    finding.severity = "critical"
    finding.title = "Remote TLS buffer overflow"
    finding.description = "Network-exploitable RCE via TLS"
    asset = MagicMock()
    asset.criticality = "critical"
    asset.environment = "prod"
    with patch("app.services.vuln_mitigation_service._ai_suggest", new_callable=AsyncMock, return_value=None):
        result = await suggest_mitigations(finding, asset)
    assert len(result.suggestions) >= 3
    recommended = [s for s in result.suggestions if s.recommended]
    assert len(recommended) >= 1
    assert all(0.0 <= s.confidence <= 1.0 for s in result.suggestions)
    assert result.ai_summary
