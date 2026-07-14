# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import sys
import asyncio
from unittest.mock import MagicMock, patch


# Patch google SDKs before any import of the modules under test
_google_mock = MagicMock()
_container_v1_mock = MagicMock()
_oauth2_mock = MagicMock()
_google_auth_mock = MagicMock()
_google_auth_transport_mock = MagicMock()
_google_auth_transport_requests_mock = MagicMock()

sys.modules.setdefault("google", _google_mock)
sys.modules.setdefault("google.cloud", _google_mock.cloud)
sys.modules.setdefault("google.cloud.container_v1", _container_v1_mock)
sys.modules.setdefault("google.oauth2", _oauth2_mock)
sys.modules.setdefault("google.oauth2.service_account", _oauth2_mock.service_account)
sys.modules.setdefault("google.auth", _google_auth_mock)
sys.modules.setdefault("google.auth.transport", _google_auth_transport_mock)
sys.modules.setdefault("google.auth.transport.requests", _google_auth_transport_requests_mock)


def _make_creds():
    import json
    return {
        "service_account_key_json": json.dumps({
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key1",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4PAtEsHAFbFPaJ7Kk6SJQNQLQ==\n-----END RSA PRIVATE KEY-----\n",
            "client_email": "nexplane@test-project.iam.gserviceaccount.com",
            "client_id": "123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }),
        "project_id": "test-project",
    }


class TestGkeClientFactory:
    def test_get_container_client_returns_client(self):
        from app.connectors.executors.gcp._client import get_container_client
        creds = _make_creds()
        client = get_container_client(creds)
        assert client is not None

    def test_poll_gke_operation_done_returns(self):
        from app.connectors.executors.gcp._gke_helpers import poll_gke_operation
        from unittest.mock import MagicMock
        client = MagicMock()
        done_op = MagicMock()
        done_op.status = _container_v1_mock.Operation.Status.DONE
        done_op.status_message = ""
        client.get_operation.return_value = done_op
        asyncio.get_event_loop().run_until_complete(
            poll_gke_operation(client, "projects/p/locations/l/operations/op1", 60)
        )

    def test_poll_gke_operation_failure_raises(self):
        import pytest
        from app.connectors.executors.gcp._gke_helpers import poll_gke_operation
        client = MagicMock()
        failed_op = MagicMock()
        failed_op.status = _container_v1_mock.Operation.Status.DONE
        failed_op.status_message = "quota exceeded"
        client.get_operation.return_value = failed_op
        with pytest.raises(RuntimeError, match="GKE operation failed"):
            asyncio.get_event_loop().run_until_complete(
                poll_gke_operation(client, "projects/p/locations/l/operations/op1", 60)
            )

    def test_poll_gke_operation_timeout_raises(self):
        import pytest
        from app.connectors.executors.gcp._gke_helpers import poll_gke_operation
        client = MagicMock()
        pending_op = MagicMock()
        pending_op.status = MagicMock()  # not DONE
        client.get_operation.return_value = pending_op
        with pytest.raises(TimeoutError):
            asyncio.get_event_loop().run_until_complete(
                poll_gke_operation(client, "projects/p/locations/l/operations/op1", 0)
            )


class TestCreateGkeCluster:
    def _params(self):
        return {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_count": 1,
            "machine_type": "e2-medium",
            "disk_size_gb": 100,
            "network": "default",
        }

    def _connector(self, with_creds=False):
        c = MagicMock()
        c.credentials = _make_creds() if with_creds else {}
        return c

    def test_mock_path_returns_expected_keys(self):
        from app.connectors.executors.gcp.gcp_create_gke_cluster import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(self._params(), [], self._connector())
        )
        assert result.get("mock") is True
        assert "cluster_name" in result
        assert "node_pool_name" in result

    def test_rollback_capability_full(self):
        from app.connectors.executors.gcp import gcp_create_gke_cluster
        assert gcp_create_gke_cluster.ROLLBACK_CAPABILITY == "full"

    def test_execute_calls_create_cluster_and_node_pool(self):
        from app.connectors.executors.gcp.gcp_create_gke_cluster import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op123"
        op_mock.status = _container_v1_mock.Operation.Status.DONE
        op_mock.status_message = ""
        client_mock.create_cluster.return_value = op_mock
        client_mock.create_node_pool.return_value = op_mock
        done_op = MagicMock()
        done_op.status = _container_v1_mock.Operation.Status.DONE
        done_op.status_message = ""
        client_mock.get_operation.return_value = done_op

        async def mock_poll(*args, **kwargs):
            pass

        with patch("app.connectors.executors.gcp.gcp_create_gke_cluster.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_create_gke_cluster.poll_gke_operation", side_effect=mock_poll):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        client_mock.create_cluster.assert_called_once()
        client_mock.create_node_pool.assert_called_once()

    def test_rollback_deletes_cluster(self):
        from app.connectors.executors.gcp.gcp_create_gke_cluster import rollback
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        op_mock.status = _container_v1_mock.Operation.Status.DONE
        op_mock.status_message = ""
        client_mock.delete_cluster.return_value = op_mock
        done_op = MagicMock()
        done_op.status = _container_v1_mock.Operation.Status.DONE
        done_op.status_message = ""
        client_mock.get_operation.return_value = done_op
        execution_result = {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "default-pool",
            "project_id": "test-project",
        }

        async def mock_poll(*args, **kwargs):
            pass

        with patch("app.connectors.executors.gcp.gcp_create_gke_cluster.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_create_gke_cluster.poll_gke_operation", side_effect=mock_poll):
                result = asyncio.get_event_loop().run_until_complete(
                    rollback(self._params(), execution_result, connector)
                )
        assert result.get("rolled_back") is True
        client_mock.delete_cluster.assert_called_once()


class TestDeleteGkeCluster:
    def _params(self):
        return {"cluster_name": "test-cluster", "location": "us-central1-a"}

    def _connector(self, with_creds=False):
        c = MagicMock()
        c.credentials = _make_creds() if with_creds else {}
        return c

    def test_mock_path(self):
        from app.connectors.executors.gcp.gcp_delete_gke_cluster import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(self._params(), [], self._connector())
        )
        assert result.get("mock") is True

    def test_rollback_capability_irreversible(self):
        from app.connectors.executors.gcp import gcp_delete_gke_cluster
        assert gcp_delete_gke_cluster.ROLLBACK_CAPABILITY == "irreversible"

    def test_rollback_returns_rolled_back_false(self):
        from app.connectors.executors.gcp.gcp_delete_gke_cluster import rollback
        result = asyncio.get_event_loop().run_until_complete(
            rollback(self._params(), {}, self._connector())
        )
        assert result.get("rolled_back") is False

    def test_execute_calls_delete_cluster(self):
        from app.connectors.executors.gcp.gcp_delete_gke_cluster import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        op_mock.status = _container_v1_mock.Operation.Status.DONE
        op_mock.status_message = ""
        client_mock.delete_cluster.return_value = op_mock
        done_op = MagicMock()
        done_op.status = _container_v1_mock.Operation.Status.DONE
        done_op.status_message = ""
        client_mock.get_operation.return_value = done_op

        async def mock_poll(*args, **kwargs):
            pass

        with patch("app.connectors.executors.gcp.gcp_delete_gke_cluster.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_delete_gke_cluster.poll_gke_operation", side_effect=mock_poll):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        assert result.get("deleted") is True
        client_mock.delete_cluster.assert_called_once()


class TestAddGkeNodePool:
    def _params(self):
        return {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "pool-2",
            "node_count": 1,
            "machine_type": "e2-medium",
        }

    def _connector(self, with_creds=False):
        c = MagicMock()
        c.credentials = _make_creds() if with_creds else {}
        return c

    def test_mock_path(self):
        from app.connectors.executors.gcp.gcp_add_gke_node_pool import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(self._params(), [], self._connector())
        )
        assert result.get("mock") is True
        assert "node_pool_name" in result

    def test_rollback_capability_full(self):
        from app.connectors.executors.gcp import gcp_add_gke_node_pool
        assert gcp_add_gke_node_pool.ROLLBACK_CAPABILITY == "full"

    def test_execute_calls_create_node_pool(self):
        from app.connectors.executors.gcp.gcp_add_gke_node_pool import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.create_node_pool.return_value = op_mock
        with patch("app.connectors.executors.gcp.gcp_add_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_add_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        client_mock.create_node_pool.assert_called_once()
        assert result["node_pool_name"] == "pool-2"

    def test_rollback_deletes_pool(self):
        from app.connectors.executors.gcp.gcp_add_gke_node_pool import rollback
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.delete_node_pool.return_value = op_mock
        execution_result = {
            "node_pool_name": "pool-2",
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "project_id": "test-project",
        }
        with patch("app.connectors.executors.gcp.gcp_add_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_add_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    rollback(self._params(), execution_result, connector)
                )
        assert result.get("rolled_back") is True
        client_mock.delete_node_pool.assert_called_once()


class TestDeleteGkeNodePool:
    def _params(self):
        return {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "pool-2",
        }

    def _connector(self, with_creds=False):
        c = MagicMock()
        c.credentials = _make_creds() if with_creds else {}
        return c

    def test_mock_path(self):
        from app.connectors.executors.gcp.gcp_delete_gke_node_pool import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(self._params(), [], self._connector())
        )
        assert result.get("mock") is True

    def test_rollback_capability_full(self):
        from app.connectors.executors.gcp import gcp_delete_gke_node_pool
        assert gcp_delete_gke_node_pool.ROLLBACK_CAPABILITY == "full"

    def test_drain_recommended_true_when_instance_groups_present(self):
        from app.connectors.executors.gcp.gcp_delete_gke_node_pool import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        pool_mock = MagicMock()
        pool_mock.instance_group_urls = ["https://group1", "https://group2"]
        client_mock.get_node_pool.return_value = pool_mock
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.delete_node_pool.return_value = op_mock
        with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        assert result.get("drain_recommended") is True
        assert result.get("active_node_count") == 2

    def test_drain_recommended_false_when_no_instance_groups(self):
        from app.connectors.executors.gcp.gcp_delete_gke_node_pool import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        pool_mock = MagicMock()
        pool_mock.instance_group_urls = []
        client_mock.get_node_pool.return_value = pool_mock
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.delete_node_pool.return_value = op_mock
        with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        assert result.get("drain_recommended") is False

    def test_prestatestore_captured_before_delete(self):
        from app.connectors.executors.gcp.gcp_delete_gke_node_pool import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        pool_mock = MagicMock()
        pool_mock.instance_group_urls = []
        pool_mock.name = "pool-2"
        call_order = []
        client_mock.get_node_pool.side_effect = lambda *a, **kw: (call_order.append("get"), pool_mock)[1]
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.delete_node_pool.side_effect = lambda *a, **kw: (call_order.append("delete"), op_mock)[1]
        with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        assert call_order.index("get") < call_order.index("delete")

    def test_rollback_recreates_pool(self):
        from app.connectors.executors.gcp.gcp_delete_gke_node_pool import rollback
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.create_node_pool.return_value = op_mock
        execution_result = {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "project_id": "test-project",
            "pre_state": {
                "node_pool_name": "pool-2",
                "node_count": 1,
                "machine_type": "e2-medium",
            },
        }
        with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_delete_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    rollback(self._params(), execution_result, connector)
                )
        assert result.get("rolled_back") is True
        client_mock.create_node_pool.assert_called_once()


class TestScaleGkeNodePool:
    def _params(self):
        return {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "default-pool",
            "node_count": 2,
        }

    def _connector(self, with_creds=False):
        c = MagicMock()
        c.credentials = _make_creds() if with_creds else {}
        return c

    def test_mock_path(self):
        from app.connectors.executors.gcp.gcp_scale_gke_node_pool import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(self._params(), [], self._connector())
        )
        assert result.get("mock") is True
        assert result.get("scaled") is True

    def test_rollback_capability_full(self):
        from app.connectors.executors.gcp import gcp_scale_gke_node_pool
        assert gcp_scale_gke_node_pool.ROLLBACK_CAPABILITY == "full"

    def test_execute_captures_prior_count(self):
        from app.connectors.executors.gcp.gcp_scale_gke_node_pool import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        pool_mock = MagicMock()
        pool_mock.initial_node_count = 1
        client_mock.get_node_pool.return_value = pool_mock
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.set_node_pool_size.return_value = op_mock
        with patch("app.connectors.executors.gcp.gcp_scale_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_scale_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        assert result["pre_state"]["node_count"] == 1
        assert result["new_count"] == 2

    def test_rollback_restores_count(self):
        from app.connectors.executors.gcp.gcp_scale_gke_node_pool import rollback
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.set_node_pool_size.return_value = op_mock
        execution_result = {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "default-pool",
            "project_id": "test-project",
            "pre_state": {"node_count": 1},
            "new_count": 2,
        }
        with patch("app.connectors.executors.gcp.gcp_scale_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_scale_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    rollback(self._params(), execution_result, connector)
                )
        assert result.get("rolled_back") is True
        client_mock.set_node_pool_size.assert_called_once()


class TestUpdateGkeNodePool:
    def _params(self):
        return {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "default-pool",
            "display_name": "renamed-pool",
        }

    def _connector(self, with_creds=False):
        c = MagicMock()
        c.credentials = _make_creds() if with_creds else {}
        return c

    def test_mock_path(self):
        from app.connectors.executors.gcp.gcp_update_gke_node_pool import execute
        result = asyncio.get_event_loop().run_until_complete(
            execute(self._params(), [], self._connector())
        )
        assert result.get("mock") is True
        assert result.get("updated") is True

    def test_rollback_capability_full(self):
        from app.connectors.executors.gcp import gcp_update_gke_node_pool
        assert gcp_update_gke_node_pool.ROLLBACK_CAPABILITY == "full"

    def test_execute_captures_prior_config(self):
        from app.connectors.executors.gcp.gcp_update_gke_node_pool import execute
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        pool_mock = MagicMock()
        pool_mock.name = "default-pool"
        pool_mock.config.labels = {"env": "prod"}
        client_mock.get_node_pool.return_value = pool_mock
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.update_node_pool.return_value = op_mock
        with patch("app.connectors.executors.gcp.gcp_update_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_update_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    execute(self._params(), [], connector)
                )
        assert result.get("pre_state", {}).get("labels") == {"env": "prod"}
        assert result["updated"] is True

    def test_rollback_restores_config(self):
        from app.connectors.executors.gcp.gcp_update_gke_node_pool import rollback
        from unittest.mock import AsyncMock
        connector = self._connector(with_creds=True)
        client_mock = MagicMock()
        op_mock = MagicMock()
        op_mock.name = "projects/test-project/locations/us-central1-a/operations/op1"
        client_mock.update_node_pool.return_value = op_mock
        execution_result = {
            "cluster_name": "test-cluster",
            "location": "us-central1-a",
            "node_pool_name": "default-pool",
            "project_id": "test-project",
            "pre_state": {"labels": {"env": "prod"}},
        }
        with patch("app.connectors.executors.gcp.gcp_update_gke_node_pool.get_container_client", return_value=client_mock):
            with patch("app.connectors.executors.gcp.gcp_update_gke_node_pool.poll_gke_operation", new_callable=AsyncMock):
                result = asyncio.get_event_loop().run_until_complete(
                    rollback(self._params(), execution_result, connector)
                )
        assert result.get("rolled_back") is True
        client_mock.update_node_pool.assert_called_once()
