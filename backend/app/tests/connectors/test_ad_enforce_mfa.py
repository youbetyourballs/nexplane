# SMOKE: requires live Active Directory domain controller
import pytest
from unittest.mock import MagicMock, patch


def _make_connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {
        "hostname": "10.0.1.10", "username": "Administrator", "password": "Password1!",
        "domain": "corp.example.com",
    }
    return c


@pytest.mark.asyncio
async def test_sets_smart_card_required_flag_not_hardcoded():
    mock_session = MagicMock()

    def fake_run_winrm_ps(session, script):
        return ('{"success":true,"uac_before":512,"uac_after":262656}', "", 0)

    with patch(
        "app.connectors.executors.active_directory.enforce_mfa._get_winrm_session",
        return_value=mock_session,
    ), patch(
        "app.connectors.executors.active_directory.enforce_mfa._run_winrm_ps",
        side_effect=fake_run_winrm_ps,
    ):
        from app.connectors.executors.active_directory.enforce_mfa import execute
        result = await execute({"username": "jsmith"}, ["asset-1"], _make_connector())
    assert result["mfa_required"] is True
    assert result.get("uac_before") is not None, "Must capture UAC before for rollback"


@pytest.mark.asyncio
async def test_rollback_clears_smart_card_flag():
    mock_session = MagicMock()

    def fake_run_winrm_ps(session, script):
        return ('{"success":true}', "", 0)

    with patch(
        "app.connectors.executors.active_directory.enforce_mfa._get_winrm_session",
        return_value=mock_session,
    ), patch(
        "app.connectors.executors.active_directory.enforce_mfa._run_winrm_ps",
        side_effect=fake_run_winrm_ps,
    ):
        from app.connectors.executors.active_directory.enforce_mfa import rollback
        result = await rollback(
            {"username": "jsmith"},
            {"username": "jsmith", "uac_before": 512},
            _make_connector(),
        )
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_returns_error_when_username_missing():
    from app.connectors.executors.active_directory.enforce_mfa import execute
    result = await execute({}, ["asset-1"], _make_connector())
    assert result.get("status") == "error"


@pytest.mark.asyncio
async def test_returns_error_when_no_credentials():
    c = MagicMock()
    c.credentials = {}
    from app.connectors.executors.active_directory.enforce_mfa import execute
    result = await execute({"username": "jsmith"}, ["asset-1"], c)
    assert result.get("status") == "error"
