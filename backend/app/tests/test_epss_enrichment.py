# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for EPSS score enrichment (vuln_poc_service)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from app.services.vuln_poc_service import fetch_epss, enrich_finding_epss, get_epss_score


@pytest.mark.asyncio
async def test_fetch_epss_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [{"cve": "CVE-2021-44228", "epss": "0.97542", "percentile": "0.99968"}]
    }
    with patch("app.services.vuln_poc_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_epss("CVE-2021-44228")

    assert result is not None
    assert abs(result["score"] - 0.97542) < 0.001
    assert abs(result["percentile"] - 0.99968) < 0.001
    assert isinstance(result["fetched_at"], datetime)


@pytest.mark.asyncio
async def test_fetch_epss_no_data():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"data": []}
    with patch("app.services.vuln_poc_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_epss("CVE-2099-00001")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_epss_non_cve_skipped():
    result = await fetch_epss("GHSA-xxxx-xxxx-xxxx")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_epss_network_error_returns_none():
    import app.services.vuln_poc_service as _svc
    _svc._epss_cache.pop("CVE-2021-44228", None)
    with patch("app.services.vuln_poc_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await fetch_epss("CVE-2021-44228")

    assert result is None


@pytest.mark.asyncio
async def test_enrich_finding_epss_applies_fields():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": [{"cve": "CVE-2021-44228", "epss": "0.9", "percentile": "0.99"}]
    }

    class FakeFinding:
        cve_id = "CVE-2021-44228"
        epss_score = None
        epss_percentile = None
        epss_fetched_at = None

    finding = FakeFinding()
    with patch("app.services.vuln_poc_service.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        # Clear cache to force fetch
        import app.services.vuln_poc_service as _svc
        _svc._epss_cache.pop("CVE-2021-44228", None)
        await enrich_finding_epss(finding)

    assert finding.epss_score is not None
    assert abs(finding.epss_score - 0.9) < 0.001
    assert finding.epss_fetched_at is not None


@pytest.mark.asyncio
async def test_enrich_finding_epss_no_cve_is_noop():
    class FakeFinding:
        cve_id = None
        epss_score = None

    finding = FakeFinding()
    await enrich_finding_epss(finding)
    assert finding.epss_score is None
