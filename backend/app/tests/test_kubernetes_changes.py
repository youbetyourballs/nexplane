import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_restart_deployment_mock():
    from app.connectors.executors.kubernetes.restart_deployment import execute
    result = await execute(
        {"namespace": "default", "deployment_name": "my-app"},
        [],
        MockConnector(),
    )
    assert result["action"] == "restart_deployment"
    assert result["deployment_name"] == "my-app"


@pytest.mark.asyncio
async def test_scale_deployment_mock():
    from app.connectors.executors.kubernetes.scale_deployment import execute
    result = await execute(
        {"namespace": "default", "deployment_name": "my-app", "replicas": 3},
        [],
        MockConnector(),
    )
    assert result["action"] == "scale_deployment"
    assert result["replicas"] == 3


@pytest.mark.asyncio
async def test_scale_deployment_rollback():
    from app.connectors.executors.kubernetes.scale_deployment import rollback
    result = await rollback(
        {"namespace": "default", "deployment_name": "my-app", "replicas": 3},
        {"previous_replicas": 1},
        MockConnector(),
    )
    assert result["action"] == "scale_deployment"


@pytest.mark.asyncio
async def test_apply_network_policy_mock():
    from app.connectors.executors.kubernetes.apply_network_policy import execute
    manifest = '{"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "deny-all"}, "spec": {"podSelector": {}}}'
    result = await execute(
        {"namespace": "default", "manifest": manifest},
        [],
        MockConnector(),
    )
    assert result["action"] == "apply_network_policy"


@pytest.mark.asyncio
async def test_update_rbac_mock():
    from app.connectors.executors.kubernetes.update_rbac import execute
    manifest = '{"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "test-binding"}, "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "view"}, "subjects": []}'
    result = await execute({"manifest": manifest}, [], MockConnector())
    assert result["action"] == "update_rbac"


@pytest.mark.asyncio
async def test_rotate_secret_mock():
    from app.connectors.executors.kubernetes.rotate_secret import execute
    result = await execute(
        {"namespace": "default", "secret_name": "my-secret", "data": {"API_KEY": "new-value"}},
        [],
        MockConnector(),
    )
    assert result["action"] == "rotate_secret"
    assert result["secret_name"] == "my-secret"


@pytest.mark.asyncio
async def test_helm_upgrade_mock():
    from app.connectors.executors.kubernetes.helm_upgrade import execute
    result = await execute(
        {"namespace": "default", "release_name": "my-release", "chart": "bitnami/nginx"},
        [],
        MockConnector(),
    )
    assert result["action"] == "helm_upgrade"
    assert result["release_name"] == "my-release"


@pytest.mark.asyncio
async def test_helm_rollback_mock():
    from app.connectors.executors.kubernetes.helm_rollback import execute
    result = await execute(
        {"namespace": "default", "release_name": "my-release", "revision": 2},
        [],
        MockConnector(),
    )
    assert result["action"] == "helm_rollback"
