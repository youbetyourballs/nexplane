# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_http_check_passes():
    from app.services.verification_check_service import run_verification_checks
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "OK"
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        results = await run_verification_checks(
            [{"type": "http", "url": "http://localhost:80", "expected_status": 200}],
            asset_ids=["asset-1"],
        )
    assert results[0]["passed"] is True


@pytest.mark.asyncio
async def test_http_check_fails_wrong_status():
    from app.services.verification_check_service import run_verification_checks
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Error"
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        results = await run_verification_checks(
            [{"type": "http", "url": "http://localhost:80", "expected_status": 200}],
            asset_ids=["asset-1"],
        )
    assert results[0]["passed"] is False
    assert "500" in results[0]["detail"]
