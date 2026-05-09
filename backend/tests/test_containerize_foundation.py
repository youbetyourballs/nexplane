import pytest
from app.models.change_request import ChangeType
from app.models.asset import AssetType


def test_agent_appdiscovery_change_type_exists():
    assert ChangeType.agent_appdiscovery == "agent_appdiscovery"


def test_agent_containerize_build_change_type_exists():
    assert ChangeType.agent_containerize_build == "agent_containerize_build"


def test_k8s_workload_deploy_change_type_exists():
    assert ChangeType.k8s_workload_deploy == "k8s_workload_deploy"


def test_agent_containerize_retire_change_type_exists():
    assert ChangeType.agent_containerize_retire == "agent_containerize_retire"


def test_kubernetes_cluster_asset_type_exists():
    assert AssetType.kubernetes_cluster == "kubernetes_cluster"


def test_container_image_asset_type_exists():
    assert AssetType.container_image == "container_image"


def test_appdiscovery_in_implicit_rollback_types():
    import app.services.safety_engine as se
    assert ChangeType.agent_appdiscovery in se._IMPLICIT_ROLLBACK_TYPES
    assert ChangeType.agent_containerize_build in se._IMPLICIT_ROLLBACK_TYPES
    assert ChangeType.k8s_workload_deploy in se._IMPLICIT_ROLLBACK_TYPES
    assert ChangeType.agent_containerize_retire in se._IMPLICIT_ROLLBACK_TYPES


import asyncio


def test_app_discovery_executor_returns_applications():
    from app.connectors.executors.nexplane_agent.app_discovery import execute
    result = asyncio.run(execute({}, [], None))
    assert result["action"] == "discover_applications"
    assert isinstance(result["applications"], list)
    assert len(result["applications"]) > 0
    app = result["applications"][0]
    assert "name" in app
    assert "stateful" in app
    assert "containerization_status" in app


def test_workload_deploy_executor_requires_target_cluster():
    from app.connectors.executors.kubernetes.workload_deploy import execute
    with pytest.raises(ValueError, match="target_cluster"):
        asyncio.run(execute({}, [], None))


def test_workload_deploy_executor_succeeds_with_target_cluster():
    from app.connectors.executors.kubernetes.workload_deploy import execute
    result = asyncio.run(execute({"target_cluster": "eks-prod"}, [], None))
    assert result["action"] == "k8s_workload_deploy"
    assert result["pods_ready"] >= 1
