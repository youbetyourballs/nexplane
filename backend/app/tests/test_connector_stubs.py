import pytest


@pytest.mark.asyncio
async def test_okta_suspend_returns_mock_without_creds():
    """Okta executor gracefully skips when no credentials configured."""
    from app.connectors.executors.okta.suspend_user import execute
    result = await execute({"user_id": "test123"}, ["asset-1"], type("C", (), {"credentials": {}})())
    assert result.get("status") in ("skipped", "suspended", "done", "SUSPENDED")


@pytest.mark.asyncio
async def test_okta_deactivate_returns_mock_without_creds():
    """Okta deactivate executor gracefully skips when no credentials configured."""
    from app.connectors.executors.okta.deactivate_user import execute
    result = await execute({"user_id": "test123"}, ["asset-1"], type("C", (), {"credentials": {}})())
    assert result.get("action") == "deactivate_user"
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_okta_revoke_sessions_returns_mock_without_creds():
    """Okta revoke_sessions executor gracefully skips when no credentials."""
    from app.connectors.executors.okta.revoke_sessions import execute
    result = await execute({"user_id": "test123"}, ["asset-1"], type("C", (), {"credentials": {}})())
    assert result.get("action") == "revoke_sessions"
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_tailscale_list_devices_returns_mock_without_creds():
    from app.connectors.executors.tailscale.list_devices import execute
    result = await execute({}, ["asset-1"], None)
    assert isinstance(result, dict)
    assert "devices" in result
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_tailscale_delete_device_returns_mock_without_creds():
    from app.connectors.executors.tailscale.delete_device import execute
    result = await execute({"device_id": "dev-abc"}, ["asset-1"], None)
    assert result.get("status") == "skipped"
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_tailscale_authorize_device_returns_mock_without_creds():
    from app.connectors.executors.tailscale.authorize_device import execute
    result = await execute({"device_id": "dev-abc"}, ["asset-1"], None)
    assert result.get("status") == "skipped"
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_tailscale_expire_device_key_returns_mock_without_creds():
    from app.connectors.executors.tailscale.expire_device_key import execute
    result = await execute({"device_id": "dev-abc"}, ["asset-1"], None)
    assert result.get("status") == "skipped"
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_emergency_lockout_okta_skips_without_creds():
    """Emergency lockout returns skipped_no_credentials for okta when params have no okta creds."""
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute
    result = await execute(
        {"user_identifier": "alice@example.com", "systems": ["okta"]},
        ["asset-1"],
        None,
    )
    assert result["lockout_status"]["okta"] == "skipped_no_credentials"
