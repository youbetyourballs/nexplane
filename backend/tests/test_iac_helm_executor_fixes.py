import pytest
from unittest.mock import MagicMock, patch


def _make_cf_connector(access_key_id="k", secret="s", region="us-east-1"):
    c = MagicMock()
    c.credentials = {"access_key_id": access_key_id, "secret_access_key": secret, "region": region}
    return c


@pytest.mark.asyncio
async def test_create_change_set_type_create_forwarded():
    """change_set_type=CREATE is passed as ChangeSetType to boto3."""
    mock_cf = MagicMock()
    with patch(
        "app.connectors.executors.cloudformation._client.get_client",
        return_value=mock_cf,
    ):
        from app.connectors.executors.cloudformation.create_change_set import execute
        result = await execute(
            {
                "stack_name": "smoke-stack",
                "change_set_type": "CREATE",
                "template_body": '{"Resources": {}}',
            },
            [],
            _make_cf_connector(),
        )
    assert result["stack_name"] == "smoke-stack"
    assert "change_set_name" in result
    call_kwargs = mock_cf.create_change_set.call_args[1]
    assert call_kwargs["ChangeSetType"] == "CREATE"
    assert call_kwargs["TemplateBody"] == '{"Resources": {}}'


@pytest.mark.asyncio
async def test_create_change_set_default_type_is_update():
    """Omitting change_set_type defaults to UPDATE (backwards compatible)."""
    mock_cf = MagicMock()
    with patch(
        "app.connectors.executors.cloudformation._client.get_client",
        return_value=mock_cf,
    ):
        from app.connectors.executors.cloudformation.create_change_set import execute
        await execute(
            {"stack_name": "smoke-stack", "template_body": "{}"},
            [],
            _make_cf_connector(),
        )
    call_kwargs = mock_cf.create_change_set.call_args[1]
    assert call_kwargs["ChangeSetType"] == "UPDATE"
