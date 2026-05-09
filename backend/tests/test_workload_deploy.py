"""Tests for k8s_workload_deploy executor and workload_deploy_service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


class TestWorkloadDeployExecutor:
    @pytest.mark.asyncio
    async def test_execute_missing_target_cluster(self):
        from app.connectors.executors.kubernetes.workload_deploy import execute
        with pytest.raises((ValueError, RuntimeError)):
            await execute({}, [str(uuid.uuid4())], MagicMock())

    @pytest.mark.asyncio
    async def test_execute_missing_app_name(self):
        from app.connectors.executors.kubernetes.workload_deploy import execute
        with pytest.raises((ValueError, RuntimeError)):
            await execute({"target_cluster_id": str(uuid.uuid4())}, [str(uuid.uuid4())], MagicMock())

    @pytest.mark.asyncio
    async def test_execute_dry_run(self):
        from app.connectors.executors.kubernetes.workload_deploy import execute
        asset_id = str(uuid.uuid4())
        cluster_id = str(uuid.uuid4())

        with patch("app.connectors.executors.kubernetes.workload_deploy._load_build_result") as mock_load, \
             patch("app.connectors.executors.kubernetes.workload_deploy._load_kubeconfig") as mock_kube:
            mock_load.return_value = {"manifests": {"deployment": "apiVersion: apps/v1\nkind: Deployment\n", "service": "apiVersion: v1\nkind: Service\n", "pvc": "", "config_map": ""}}
            mock_kube.return_value = "kubeconfig-content"

            result = await execute({"app_name": "nexplane-smoketest", "target_cluster_id": cluster_id, "namespace": "test", "dry_run": True}, [asset_id], MagicMock())

        assert result["action"] == "k8s_workload_deploy"
        assert result["dry_run"] is True


class TestWorkloadDeployService:
    @pytest.mark.asyncio
    async def test_registers_kubernetes_workload_asset(self):
        from app.services.workload_deploy_service import register_workload_asset
        from app.models.asset import Asset, AssetType

        mock_source = MagicMock(spec=Asset)
        mock_source.organization_id = uuid.uuid4()
        mock_db = AsyncMock()
        mock_db.get.return_value = mock_source
        mock_db.add = MagicMock()

        asset_id = str(uuid.uuid4())
        cluster_id = str(uuid.uuid4())
        execution_result = {"steps": [{"result": {"action": "k8s_workload_deploy", "app_name": "myapp", "namespace": "prod", "cluster_asset_id": cluster_id, "pod_count": 1, "dry_run": False}}]}

        await register_workload_asset(mock_db, [asset_id], execution_result)

        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert added.asset_type == AssetType.kubernetes_workload
