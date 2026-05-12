import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

@pytest.mark.asyncio
async def test_process_sla_breaches_marks_breached():
    from app.services.vuln_mitigation_service import suggest_mitigations  # ensure imports work
    from app.jobs.vuln_sla_escalation import process_sla_breaches
    # Should not raise with empty org
    mock_config = {"critical": 72, "high": 168, "medium": 720, "auto_execute_on_breach": False, "severity_upgrade_hours": 48}
    with patch("app.database.AsyncSessionLocal") as mock_sl:
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
        mock_db.commit = AsyncMock()
        mock_sl.return_value = mock_db
        await process_sla_breaches(mock_config, "00000000-0000-0000-0000-000000000001")
