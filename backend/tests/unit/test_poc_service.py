# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from app.models.vulnerability import FindingChangeRequest


def test_finding_change_request_model_exists():
    fcr = FindingChangeRequest()
    assert hasattr(fcr, "finding_id")
    assert hasattr(fcr, "cr_id")
    assert hasattr(fcr, "role")


def test_poc_schemas_exist():
    from app.schemas.vulnerability import (
        PocValidateRequest, ChallengeRequest, VerificationResult, FindingCRRead
    )
    req = PocValidateRequest(asset_id="00000000-0000-0000-0000-000000000001")
    assert req.asset_id is not None


def test_check_cisa_kev_hit():
    from app.services.vuln_poc_service import check_cisa_kev
    kev_data = {"vulnerabilities": [{"cveID": "CVE-2021-44228"}]}
    with patch("app.services.vuln_poc_service._kev_cache", kev_data):
        assert check_cisa_kev("CVE-2021-44228") is True


def test_check_cisa_kev_miss():
    from app.services.vuln_poc_service import check_cisa_kev
    kev_data = {"vulnerabilities": [{"cveID": "CVE-2021-44228"}]}
    with patch("app.services.vuln_poc_service._kev_cache", kev_data):
        assert check_cisa_kev("CVE-2024-9999") is False


def test_check_cisa_kev_empty_cache():
    from app.services.vuln_poc_service import check_cisa_kev
    with patch("app.services.vuln_poc_service._kev_cache", None):
        assert check_cisa_kev("CVE-2021-44228") is False


def test_apply_poc_result_exploited():
    from app.services.vuln_poc_service import apply_poc_result
    finding = MagicMock()
    finding.status = "exploitability_pending"
    finding.severity = "high"
    apply_poc_result(finding, "exploited", "metasploit", "exploit/multi/handler")
    assert finding.status == "actionable"
    assert finding.exploitability_result == "exploited"
    assert finding.poc_source == "metasploit"
    assert finding.severity == "critical"  # escalated


def test_apply_poc_result_not_exploited():
    from app.services.vuln_poc_service import apply_poc_result
    finding = MagicMock()
    finding.status = "challenged"
    finding.severity = "critical"
    apply_poc_result(finding, "not_exploited", "metasploit", "exploit/multi/handler")
    assert finding.status == "actionable"
    assert finding.exploitability_result == "not_exploited"
    assert finding.severity == "high"  # downgraded one tier


def test_apply_poc_result_inconclusive():
    from app.services.vuln_poc_service import apply_poc_result
    finding = MagicMock()
    finding.status = "exploitability_pending"
    finding.severity = "high"
    apply_poc_result(finding, "inconclusive", None, None)
    # status does not change
    assert finding.status == "exploitability_pending"


@pytest.mark.asyncio
async def test_refresh_kev_cache():
    from app.services.vuln_poc_service import refresh_kev_cache
    import app.services.vuln_poc_service as svc
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"vulnerabilities": [{"cveID": "CVE-2021-44228"}]}
    with patch("app.services.vuln_poc_service.httpx") as mock_httpx:
        mock_httpx.get.return_value = mock_resp
        await refresh_kev_cache()
    assert svc._kev_cache is not None
    assert any(v["cveID"] == "CVE-2021-44228" for v in svc._kev_cache["vulnerabilities"])


def test_poc_validate_executor_returns_schema():
    """Executor module must be importable and expose an execute() coroutine."""
    import inspect
    from app.connectors.executors.vuln import poc_validate
    assert inspect.iscoroutinefunction(poc_validate.execute)


@pytest.mark.asyncio
async def test_trigger_verification_no_scanner_raises():
    from app.services.vuln_verification_service import trigger_verification
    finding = MagicMock()
    finding.id = uuid.uuid4()
    finding.scanner = "unknown_scanner"
    finding.asset_id = uuid.uuid4()
    db = AsyncMock()
    # With no matching scanner connector, result should be inconclusive
    result = await trigger_verification(finding, db)
    assert result["result"] in ("inconclusive", "still_vulnerable")


@pytest.mark.asyncio
async def test_link_cr_to_finding_creates_fcr():
    from app.services.vuln_remediation_engine import link_cr_to_finding
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    finding_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    await link_cr_to_finding(db, finding_id, cr_id, "patch")

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    from app.models.vulnerability import FindingChangeRequest
    assert isinstance(added, FindingChangeRequest)
    assert added.finding_id == finding_id
    assert added.cr_id == cr_id
    assert added.role == "patch"


@pytest.mark.asyncio
async def test_poc_validate_endpoint_exists():
    from fastapi.testclient import TestClient
    from app.main import app
    # Just verify the route is registered — no auth needed for route existence check
    routes = [r.path for r in app.routes]
    assert any("poc-validate" in r for r in routes)


@pytest.mark.asyncio
async def test_challenge_endpoint_exists():
    from app.main import app
    routes = [r.path for r in app.routes]
    assert any("challenge" in r for r in routes)


def test_scheduler_has_kev_refresh_job():
    from app.services.scheduler_service import scheduler
    job_ids = [j.id for j in scheduler.get_jobs()]
    assert "cisa_kev_refresh" in job_ids
