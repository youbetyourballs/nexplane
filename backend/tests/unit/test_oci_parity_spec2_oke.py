# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import sys
from unittest.mock import MagicMock, patch, AsyncMock


def _connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {
        "user": "u", "key_content": "k", "fingerprint": "f",
        "tenancy": "t", "region": "us-ashburn-1", "private_key": "pk",
    }
    return c


def _empty_connector():
    c = MagicMock()
    c.credentials = {}
    return c


class TestClientFactory:
    def test_get_container_engine_client_exists(self):
        from app.connectors.executors.oci._client import get_container_engine_client
        assert callable(get_container_engine_client)

    def test_poll_work_request_exists(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        assert callable(poll_work_request)

    def test_poll_work_request_returns_identifier_on_success(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        fake_resource = MagicMock()
        fake_resource.entity_type = "cluster"
        fake_resource.identifier = "ocid1.cluster.x"
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = [fake_resource]
        fake_client = MagicMock()
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        result = asyncio.run(poll_work_request(fake_client, "wr-1", "cluster", timeout=60))
        assert result == "ocid1.cluster.x"

    def test_poll_work_request_raises_on_failed(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        fake_wr = MagicMock()
        fake_wr.status = "FAILED"
        fake_wr.time_finished = "2026-01-01"
        fake_client = MagicMock()
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        try:
            asyncio.run(poll_work_request(fake_client, "wr-1", "cluster", timeout=60))
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass


class TestCreateOkeCluster:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_create_oke_cluster as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import execute
        result = asyncio.run(execute(
            {"compartment_id": "ocid1.compartment.x", "name": "test-cluster",
             "vcn_id": "ocid1.vcn.x", "kubernetes_version": "v1.29.1",
             "subnet_ids": ["ocid1.subnet.x"], "node_shape": "VM.Standard.E3.Flex",
             "node_count": 1},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert "cluster_id" in result
        assert "node_pool_id" in result

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import rollback
        result = asyncio.run(rollback({}, {"cluster_id": "ocid1.cluster.x", "node_pool_id": "ocid1.nodepool.x"}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True
        assert result["cluster_id"] == "ocid1.cluster.x"
        assert result["node_pool_id"] == "ocid1.nodepool.x"

    def test_execute_creates_cluster_and_node_pool(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import execute
        fake_client = MagicMock()
        fake_wr_cluster = MagicMock()
        fake_wr_cluster.status = "SUCCEEDED"
        cluster_resource = MagicMock()
        cluster_resource.entity_type = "cluster"
        cluster_resource.identifier = "ocid1.cluster.real"
        fake_wr_cluster.resources = [cluster_resource]

        fake_wr_np = MagicMock()
        fake_wr_np.status = "SUCCEEDED"
        np_resource = MagicMock()
        np_resource.entity_type = "nodepool"
        np_resource.identifier = "ocid1.nodepool.real"
        fake_wr_np.resources = [np_resource]

        call_count = [0]
        def _get_wr(wr_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(data=fake_wr_cluster)
            return MagicMock(data=fake_wr_np)

        fake_client.create_cluster.return_value = MagicMock(
            headers={"opc-work-request-id": "wr-cluster-1"}
        )
        fake_client.create_node_pool.return_value = MagicMock(
            headers={"opc-work-request-id": "wr-np-1"}
        )
        fake_client.get_work_request.side_effect = _get_wr
        fake_oci = MagicMock()

        with patch("app.connectors.executors.oci.oci_create_oke_cluster.get_container_engine_client", return_value=fake_client), \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            result = asyncio.run(execute(
                {"compartment_id": "ocid1.compartment.x", "name": "test-cluster",
                 "vcn_id": "ocid1.vcn.x", "kubernetes_version": "v1.29.1",
                 "subnet_ids": ["ocid1.subnet.x"], "node_shape": "VM.Standard.E3.Flex",
                 "node_count": 1},
                [], _connector()
            ))
        assert result["cluster_id"] == "ocid1.cluster.real"
        assert result["node_pool_id"] == "ocid1.nodepool.real"
        fake_client.create_cluster.assert_called_once()
        fake_client.create_node_pool.assert_called_once()

    def test_rollback_deletes_node_pool_then_cluster(self):
        from app.connectors.executors.oci.oci_create_oke_cluster import rollback
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-del-np"})
        fake_client.delete_cluster.return_value = MagicMock(headers={"opc-work-request-id": "wr-del-cl"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        delete_order = []
        fake_client.delete_node_pool.side_effect = lambda np_id: (delete_order.append("nodepool"), MagicMock(headers={"opc-work-request-id": "wr1"}))[1]
        fake_client.delete_cluster.side_effect = lambda cl_id: (delete_order.append("cluster"), MagicMock(headers={"opc-work-request-id": "wr2"}))[1]

        with patch("app.connectors.executors.oci.oci_create_oke_cluster.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_create_oke_cluster.poll_work_request", new_callable=AsyncMock) as mock_poll:
            result = asyncio.run(rollback(
                {},
                {"cluster_id": "ocid1.cluster.real", "node_pool_id": "ocid1.nodepool.real"},
                _connector()
            ))
        assert result["rolled_back"] is True
        assert delete_order.index("nodepool") < delete_order.index("cluster")
        # Verify poll_work_request was called for both nodepool and cluster
        assert mock_poll.call_count == 2
        calls = mock_poll.call_args_list
        # First call for nodepool, second for cluster
        assert calls[0][0][1] == "wr1"  # nodepool work request
        assert calls[0][0][2] == "nodepool"
        assert calls[1][0][1] == "wr2"  # cluster work request
        assert calls[1][0][2] == "cluster"


class TestAddOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_add_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import execute
        result = asyncio.run(execute(
            {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x",
             "name": "pool-2", "kubernetes_version": "v1.29.1",
             "node_shape": "VM.Standard.E3.Flex", "node_count": 1, "subnet_id": "ocid1.subnet.x"},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert "node_pool_id" in result

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import rollback
        result = asyncio.run(rollback({}, {"node_pool_id": "ocid1.nodepool.x"}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_returns_node_pool_id(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import execute
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        np_resource = MagicMock()
        np_resource.entity_type = "nodepool"
        np_resource.identifier = "ocid1.nodepool.real"
        fake_wr.resources = [np_resource]
        fake_client.create_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_add_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            result = asyncio.run(execute(
                {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x",
                 "name": "pool-2", "kubernetes_version": "v1.29.1",
                 "node_shape": "VM.Standard.E3.Flex", "node_count": 1, "subnet_id": "ocid1.subnet.x"},
                [], _connector()
            ))
        assert result["node_pool_id"] == "ocid1.nodepool.real"

    def test_rollback_deletes_node_pool(self):
        from app.connectors.executors.oci.oci_add_oke_node_pool import rollback
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        with patch("app.connectors.executors.oci.oci_add_oke_node_pool.get_container_engine_client", return_value=fake_client):
            result = asyncio.run(rollback({}, {"node_pool_id": "ocid1.nodepool.x"}, _connector()))
        fake_client.delete_node_pool.assert_called_once_with("ocid1.nodepool.x")
        assert result["rolled_back"] is True


class TestDeleteOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_delete_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import execute
        result = asyncio.run(execute({"node_pool_id": "ocid1.nodepool.x"}, [], _empty_connector()))
        assert result["mock"] is True
        assert "drain_recommended" in result

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import rollback
        result = asyncio.run(rollback({}, {}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_drain_recommended_true_when_active_nodes(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import execute
        active_node = MagicMock()
        active_node.lifecycle_state = "ACTIVE"
        fake_pool = MagicMock()
        fake_pool.nodes = [active_node]
        fake_pool.compartment_id = "ocid1.compartment.x"
        fake_pool.cluster_id = "ocid1.cluster.x"
        fake_pool.name = "pool-1"
        fake_pool.kubernetes_version = "v1.29.1"
        fake_pool.node_shape = "VM.Standard.E3.Flex"
        fake_pool.node_config_details.size = 1
        fake_pool.node_config_details.placement_configs = []
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        with patch("app.connectors.executors.oci.oci_delete_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.AsyncSessionLocal") as mock_session:
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["drain_recommended"] is True
        assert result["active_node_count"] == 1

    def test_drain_recommended_false_when_no_active_nodes(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import execute
        fake_pool = MagicMock()
        fake_pool.nodes = []
        fake_pool.compartment_id = "ocid1.compartment.x"
        fake_pool.cluster_id = "ocid1.cluster.x"
        fake_pool.name = "pool-1"
        fake_pool.kubernetes_version = "v1.29.1"
        fake_pool.node_shape = "VM.Standard.E3.Flex"
        fake_pool.node_config_details.size = 0
        fake_pool.node_config_details.placement_configs = []
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.delete_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        with patch("app.connectors.executors.oci.oci_delete_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.AsyncSessionLocal") as mock_session:
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["drain_recommended"] is False

    def test_rollback_recreates_pool_from_pre_state(self):
        from app.connectors.executors.oci.oci_delete_oke_node_pool import rollback
        captured_state = {
            "compartment_id": "ocid1.compartment.x",
            "cluster_id": "ocid1.cluster.x",
            "name": "pool-1",
            "kubernetes_version": "v1.29.1",
            "node_shape": "VM.Standard.E3.Flex",
            "node_config_details": {
                "size": 1,
                "placement_configs": [{"availability_domain": "AD-1", "subnet_id": "ocid1.subnet.x"}],
            },
        }
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        np_resource = MagicMock()
        np_resource.entity_type = "nodepool"
        np_resource.identifier = "ocid1.nodepool.new"
        fake_wr.resources = [np_resource]
        fake_client.create_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_delete_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_delete_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.retrieve = AsyncMock(return_value=captured_state)
            result = asyncio.run(rollback(
                {"node_pool_id": "ocid1.nodepool.old", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                {}, _connector()
            ))
        assert result["rolled_back"] is True
        assert result["new_node_pool_id"] == "ocid1.nodepool.new"
        fake_client.create_node_pool.assert_called_once()


class TestDeleteOkeCluster:
    def test_rollback_capability_irreversible(self):
        import app.connectors.executors.oci.oci_delete_oke_cluster as m
        assert m.ROLLBACK_CAPABILITY == "irreversible"
        assert m.ROLLBACK_REASON

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_delete_oke_cluster import execute
        result = asyncio.run(execute(
            {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x"},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert result["deleted"] is True

    def test_rollback_returns_false(self):
        from app.connectors.executors.oci.oci_delete_oke_cluster import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False
        assert "reason" in result

    def test_execute_deletes_node_pools_before_cluster(self):
        from app.connectors.executors.oci.oci_delete_oke_cluster import execute
        fake_pool = MagicMock()
        fake_pool.id = "ocid1.nodepool.x"
        fake_pool.lifecycle_state = "ACTIVE"
        fake_client = MagicMock()
        fake_client.list_node_pools.return_value = MagicMock(data=[fake_pool])
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        delete_order = []
        fake_client.delete_node_pool.side_effect = lambda np_id: (delete_order.append("nodepool"), MagicMock(headers={"opc-work-request-id": "wr1"}))[1]
        fake_client.delete_cluster.side_effect = lambda cl_id: (delete_order.append("cluster"), MagicMock(headers={"opc-work-request-id": "wr2"}))[1]

        with patch("app.connectors.executors.oci.oci_delete_oke_cluster.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_delete_oke_cluster.poll_work_request", new_callable=AsyncMock):
            result = asyncio.run(execute(
                {"cluster_id": "ocid1.cluster.x", "compartment_id": "ocid1.compartment.x"},
                [], _connector()
            ))
        assert result["deleted"] is True
        assert delete_order.index("nodepool") < delete_order.index("cluster")


class TestScaleOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_scale_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import execute
        result = asyncio.run(execute(
            {"node_pool_id": "ocid1.nodepool.x", "node_count": 3}, [], _empty_connector()
        ))
        assert result["mock"] is True
        assert result["scaled"] is True

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import rollback
        result = asyncio.run(rollback({"node_pool_id": "ocid1.nodepool.x"}, {}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_captures_prior_count_and_scales(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import execute
        fake_pool = MagicMock()
        fake_pool.node_config_details.size = 2
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.update_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_scale_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "node_count": 4,
                 "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["previous_count"] == 2
        assert result["new_count"] == 4
        assert result["scaled"] is True
        fake_client.update_node_pool.assert_called_once()

    def test_rollback_restores_prior_count(self):
        from app.connectors.executors.oci.oci_scale_oke_node_pool import rollback
        fake_client = MagicMock()
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.update_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_scale_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_scale_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.retrieve = AsyncMock(return_value={"node_count": 2})
            result = asyncio.run(rollback(
                {"node_pool_id": "ocid1.nodepool.x", "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                {}, _connector()
            ))
        assert result["rolled_back"] is True
        assert result["restored_count"] == 2


class TestUpdateOkeNodePool:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_update_oke_node_pool as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_update_oke_node_pool import execute
        result = asyncio.run(execute(
            {"node_pool_id": "ocid1.nodepool.x", "name": "new-name"}, [], _empty_connector()
        ))
        assert result["mock"] is True
        assert result["updated"] is True

    def test_mock_mode_rollback(self):
        from app.connectors.executors.oci.oci_update_oke_node_pool import rollback
        result = asyncio.run(rollback({"node_pool_id": "ocid1.nodepool.x"}, {}, _empty_connector()))
        assert result["mock"] is True
        assert result["rolled_back"] is True

    def test_execute_captures_prior_name_and_updates(self):
        from app.connectors.executors.oci.oci_update_oke_node_pool import execute
        fake_pool = MagicMock()
        fake_pool.name = "old-name"
        fake_pool.initial_node_labels = []
        fake_client = MagicMock()
        fake_client.get_node_pool.return_value = MagicMock(data=fake_pool)
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = []
        fake_client.update_node_pool.return_value = MagicMock(headers={"opc-work-request-id": "wr-1"})
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.oci_update_oke_node_pool.get_container_engine_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.oci_update_oke_node_pool.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.oci_update_oke_node_pool.AsyncSessionLocal") as mock_session, \
             patch.dict(sys.modules, {"oci": fake_oci, "oci.container_engine": fake_oci.container_engine, "oci.container_engine.models": fake_oci.container_engine.models}):
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock()
            result = asyncio.run(execute(
                {"node_pool_id": "ocid1.nodepool.x", "name": "new-name",
                 "cr_id": "cr-1", "step_id": "s-1", "org_id": "org-1"},
                [], _connector()
            ))
        assert result["updated"] is True
        fake_client.update_node_pool.assert_called_once()


class TestGetOkeKubeconfig:
    def test_rollback_capability(self):
        import app.connectors.executors.oci.oci_get_oke_kubeconfig as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_mock_mode_execute(self):
        from app.connectors.executors.oci.oci_get_oke_kubeconfig import execute
        result = asyncio.run(execute({"cluster_id": "ocid1.cluster.x"}, [], _empty_connector()))
        assert result["mock"] is True
        assert "kubeconfig" in result
        assert result["kubeconfig"]

    def test_rollback_returns_read_only(self):
        from app.connectors.executors.oci.oci_get_oke_kubeconfig import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False
        assert result["reason"] == "read-only"

    def test_execute_returns_kubeconfig_yaml(self):
        from app.connectors.executors.oci.oci_get_oke_kubeconfig import execute
        fake_content = b"apiVersion: v1\nkind: Config\n"
        fake_response = MagicMock()
        fake_response.data.content = fake_content
        fake_client = MagicMock()
        fake_client.create_kubeconfig.return_value = fake_response
        with patch("app.connectors.executors.oci.oci_get_oke_kubeconfig.get_container_engine_client", return_value=fake_client):
            result = asyncio.run(execute({"cluster_id": "ocid1.cluster.x"}, [], _connector()))
        assert "apiVersion" in result["kubeconfig"]
        fake_client.create_kubeconfig.assert_called_once_with("ocid1.cluster.x")
