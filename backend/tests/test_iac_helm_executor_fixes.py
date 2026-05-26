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


def _make_helm_connector(kubeconfig_b64="dGVzdA=="):  # base64("test")
    c = MagicMock()
    c.credentials = {"kubeconfig": kubeconfig_b64}
    return c


@pytest.mark.asyncio
async def test_rollback_release_passes_kubeconfig():
    """rollback_release passes --kubeconfig to the helm subprocess."""
    import base64
    kubeconfig_b64 = base64.b64encode(b"apiVersion: v1\nclusters: []").decode()
    connector = _make_helm_connector(kubeconfig_b64)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with patch("subprocess.run", side_effect=fake_run):
        from app.connectors.executors.helm.rollback_release import execute
        result = await execute(
            {"release_name": "smoke-nginx", "namespace": "default", "revision": 0},
            [],
            connector,
        )
    assert result.get("rolled_back") is True
    assert "--kubeconfig" in captured["cmd"]
    kubeconfig_arg_idx = captured["cmd"].index("--kubeconfig")
    kubeconfig_path = captured["cmd"][kubeconfig_arg_idx + 1]
    assert kubeconfig_path.endswith(".yaml")


@pytest.mark.asyncio
async def test_uninstall_release_passes_kubeconfig():
    """uninstall_release passes --kubeconfig to the helm subprocess."""
    import base64
    kubeconfig_b64 = base64.b64encode(b"apiVersion: v1\nclusters: []").decode()
    connector = _make_helm_connector(kubeconfig_b64)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with patch("subprocess.run", side_effect=fake_run):
        from app.connectors.executors.helm.uninstall_release import execute
        result = await execute(
            {"release_name": "smoke-nginx", "namespace": "default"},
            [],
            connector,
        )
    assert result.get("uninstalled") is True
    assert "--kubeconfig" in captured["cmd"]
