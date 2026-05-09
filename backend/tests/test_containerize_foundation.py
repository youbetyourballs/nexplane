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
