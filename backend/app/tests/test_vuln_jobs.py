import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.models.vulnerability import VulnerabilityFinding, RemediationSLA


@pytest.mark.asyncio
async def test_sla_enforcement_marks_breached(db_session, test_org, test_finding_with_overdue_sla):
    from app.jobs.sla_enforcement import enforce_slas
    await enforce_slas(db_session)
    await db_session.refresh(test_finding_with_overdue_sla["sla"])
    assert test_finding_with_overdue_sla["sla"].breached is True


@pytest.mark.asyncio
async def test_sla_enforcement_generates_cr_for_open_finding(
    db_session, test_org, test_finding_with_overdue_sla
):
    from app.jobs.sla_enforcement import enforce_slas
    finding = test_finding_with_overdue_sla["finding"]
    with patch(
        "app.services.vuln_remediation_engine.generate_change_request_for_finding",
        new_callable=AsyncMock,
    ) as mock_gen:
        mock_gen.return_value = MagicMock(id=uuid.uuid4(), status="draft")
        await enforce_slas(db_session)
    mock_gen.assert_called_once()


@pytest.mark.asyncio
async def test_sla_enforcement_skips_non_overdue(db_session, test_org, test_finding):
    """Findings with future SLA must not be marked breached."""
    from app.jobs.sla_enforcement import enforce_slas
    sla = RemediationSLA(
        organization_id=test_org.id,
        finding_id=test_finding.id,
        severity="critical",
        sla_hours=72,
        due_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db_session.add(sla)
    await db_session.flush()
    await enforce_slas(db_session)
    await db_session.refresh(sla)
    assert sla.breached is False


@pytest.mark.asyncio
async def test_finding_asset_match_job_resolves_unmatched(db_session, test_org, test_unmatched_finding, test_asset_with_ip):
    from app.jobs.finding_asset_match import retry_asset_matching
    await retry_asset_matching(db_session)
    await db_session.refresh(test_unmatched_finding)
    assert test_unmatched_finding.asset_id == test_asset_with_ip.id


@pytest.mark.asyncio
async def test_scanner_poll_job_calls_ingest(db_session, test_org):
    """Scanner poll job should call the CrowdStrike client and ingest results."""
    from app.jobs.scanner_poll import poll_crowdstrike
    from app.models.connector_credential import ConnectorCredential
    from unittest.mock import MagicMock
    mock_cred = MagicMock(spec=ConnectorCredential)
    mock_cred.organization_id = test_org.id
    with patch(
        "app.jobs.scanner_poll.fetch_crowdstrike_findings",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_fetch, patch.object(
        db_session, "execute",
        new_callable=AsyncMock,
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_cred)),
    ):
        await poll_crowdstrike(db_session, test_org.id)
    mock_fetch.assert_called_once()
