import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_deactivate_user_mock():
    from app.connectors.executors.slack.deactivate_user import execute
    result = await execute({"user_id": "U012AB3CD"}, [], MockConnector())
    assert result["action"] == "deactivate_user"
    assert result["user_id"] == "U012AB3CD"
    assert result["deactivated"] is True


@pytest.mark.asyncio
async def test_reactivate_user_mock():
    from app.connectors.executors.slack.reactivate_user import execute
    result = await execute({"user_id": "U012AB3CD"}, [], MockConnector())
    assert result["action"] == "reactivate_user"
    assert result["activated"] is True


@pytest.mark.asyncio
async def test_deactivate_user_rollback():
    from app.connectors.executors.slack.deactivate_user import rollback
    result = await rollback({"user_id": "U012AB3CD"}, {"deactivated": True}, MockConnector())
    assert result["action"] == "reactivate_user"


@pytest.mark.asyncio
async def test_slack_catalog_loads():
    import pathlib
    from app.connectors.catalog_service import ActionCatalogService
    catalog_dir = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"
    svc = ActionCatalogService(catalog_dir)
    assert "slack" in svc._catalog
    slack_actions = [a["action_id"] for a in svc._catalog["slack"]]
    assert "deactivate_user" in slack_actions
    assert "reactivate_user" in slack_actions
