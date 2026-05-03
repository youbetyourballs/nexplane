import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_remove_from_teams_mock():
    from app.connectors.executors.entra_id.remove_from_teams import execute
    result = await execute({"user_id": "user-object-id"}, [], MockConnector())
    assert result["action"] == "remove_from_teams"
    assert isinstance(result["teams_removed"], list)


@pytest.mark.asyncio
async def test_assign_license_mock():
    from app.connectors.executors.entra_id.assign_license import execute
    result = await execute(
        {"user_id": "user-object-id", "sku_id": "6fd2c87f-b296-42f0-b197-1e91e994b900"},
        [],
        MockConnector(),
    )
    assert result["action"] == "assign_license"
    assert result["sku_id"] == "6fd2c87f-b296-42f0-b197-1e91e994b900"


@pytest.mark.asyncio
async def test_remove_license_mock():
    from app.connectors.executors.entra_id.remove_license import execute
    result = await execute(
        {"user_id": "user-object-id", "sku_id": "6fd2c87f-b296-42f0-b197-1e91e994b900"},
        [],
        MockConnector(),
    )
    assert result["action"] == "remove_license"
    assert result["removed"] is True


@pytest.mark.asyncio
async def test_remove_from_teams_rollback():
    from app.connectors.executors.entra_id.remove_from_teams import rollback
    result = await rollback(
        {"user_id": "user-object-id"},
        {"teams_removed": [{"teamId": "t1", "teamName": "Team1"}]},
        MockConnector(),
    )
    assert "rolled_back" in result or "action" in result
