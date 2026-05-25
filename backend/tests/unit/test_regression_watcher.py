import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def resolved_finding():
    f = MagicMock()
    f.id = uuid.uuid4()
    f.organization_id = uuid.uuid4()
    f.scanner = "nessus"
    f.scanner_finding_id = "nessus-001"
    f.cve_id = "CVE-2021-44228"
    f.asset_id = uuid.uuid4()
    f.status = "resolved"
    return f


@pytest.mark.asyncio
async def test_non_rollback_regression_restarts_sla():
    from app.routers.vulnerability import _check_regression

    db = AsyncMock()
    existing = MagicMock()
    existing.status = "resolved"
    existing.id = uuid.uuid4()
    existing.cve_id = "CVE-2021-44228"
    existing.asset_id = uuid.uuid4()

    new_finding = MagicMock()
    new_finding.cve_id = "CVE-2021-44228"
    new_finding.asset_id = existing.asset_id

    with patch("app.routers.vulnerability._get_latest_cr_for_asset", new_callable=AsyncMock, return_value=None):
        result = await _check_regression(existing, db)

    assert result == "regressed"
    assert existing.status == "regressed"


@pytest.mark.asyncio
async def test_rollback_regression_sets_intentional_flag():
    from app.routers.vulnerability import _check_regression
    from unittest.mock import MagicMock, AsyncMock, patch

    db = AsyncMock()
    existing = MagicMock()
    existing.status = "resolved"
    existing.id = uuid.uuid4()
    existing.cve_id = "CVE-2021-44228"
    existing.asset_id = uuid.uuid4()

    rollback_cr = MagicMock()
    rollback_cr.status = "rolled_back"

    with patch("app.routers.vulnerability._get_latest_cr_for_asset", new_callable=AsyncMock, return_value=rollback_cr):
        result = await _check_regression(existing, db)

    assert result == "regressed_intentional"
    assert "intentional" in existing.status


@pytest.mark.asyncio
async def test_non_resolved_finding_skipped():
    from app.routers.vulnerability import _check_regression

    db = AsyncMock()
    existing = MagicMock()
    existing.status = "open"

    result = await _check_regression(existing, db)
    assert result is None
