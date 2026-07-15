# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import unittest
from unittest.mock import MagicMock, patch


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {"kubeconfig": "..."}


def _make_apps_clients(apps_api):
    return {"apps": apps_api, "core": MagicMock(), "rbac": MagicMock()}


def _make_core_clients(core_api):
    return {"core": core_api, "apps": MagicMock(), "rbac": MagicMock()}


class TestPatchDeploymentRollback(unittest.TestCase):
    def _make_mock_api(self):
        api = MagicMock()
        mock_dep = MagicMock()
        mock_dep.to_dict.return_value = {
            "metadata": {"name": "my-dep", "namespace": "default"},
            "spec": {"replicas": 3, "template": {"spec": {"containers": []}}},
        }
        api.read_namespaced_deployment.return_value = mock_dep
        return api

    def test_execute_captures_prior_spec(self):
        mock_api = self._make_mock_api()
        with patch(
            "backend.app.connectors.executors.kubernetes.patch_deployment.get_k8s_clients",
            return_value=_make_apps_clients(mock_api),
        ):
            from backend.app.connectors.executors.kubernetes import patch_deployment
            result = run(
                patch_deployment.execute(
                    {
                        "deployment_name": "my-dep",
                        "namespace": "default",
                        "patch": {"spec": {"replicas": 1}},
                    },
                    [],
                    FakeConnector(),
                )
            )
        self.assertIn("pre_state", result)
        self.assertIn("spec", result["pre_state"])
        self.assertEqual(result["pre_state"]["spec"]["spec"]["replicas"], 3)

    def test_rollback_patches_back_to_prior_spec(self):
        mock_api = self._make_mock_api()
        with patch(
            "backend.app.connectors.executors.kubernetes.patch_deployment.get_k8s_clients",
            return_value=_make_apps_clients(mock_api),
        ):
            from backend.app.connectors.executors.kubernetes import patch_deployment
            prior = {"metadata": {"name": "my-dep"}, "spec": {"replicas": 3}}
            result = run(
                patch_deployment.rollback(
                    {"deployment_name": "my-dep", "namespace": "default"},
                    {
                        "deployment_name": "my-dep",
                        "namespace": "default",
                        "pre_state": {"spec": prior},
                    },
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        mock_api.patch_namespaced_deployment.assert_called_once()
        call_args = mock_api.patch_namespaced_deployment.call_args
        self.assertEqual(call_args[0][0], "my-dep")
        self.assertEqual(call_args[0][1], "default")

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "backend.app.connectors.executors.kubernetes.patch_deployment.get_k8s_clients",
            return_value=_make_apps_clients(MagicMock()),
        ):
            from backend.app.connectors.executors.kubernetes import patch_deployment
            result = run(
                patch_deployment.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])


class TestUncordonNodeRollback(unittest.TestCase):
    def test_execute_captures_was_unschedulable_true(self):
        mock_api = MagicMock()
        mock_node = MagicMock()
        mock_node.spec.unschedulable = True
        mock_api.read_node.return_value = mock_node
        with patch(
            "backend.app.connectors.executors.kubernetes.uncordon_node.get_k8s_clients",
            return_value=_make_core_clients(mock_api),
        ):
            from backend.app.connectors.executors.kubernetes import uncordon_node
            result = run(
                uncordon_node.execute({"node_name": "node-1"}, [], FakeConnector())
            )
        self.assertTrue(result["pre_state"]["was_unschedulable"])
        mock_api.patch_node.assert_called_once_with("node-1", {"spec": {"unschedulable": False}})

    def test_rollback_recordons_when_was_unschedulable(self):
        mock_api = MagicMock()
        with patch(
            "backend.app.connectors.executors.kubernetes.uncordon_node.get_k8s_clients",
            return_value=_make_core_clients(mock_api),
        ):
            from backend.app.connectors.executors.kubernetes import uncordon_node
            result = run(
                uncordon_node.rollback(
                    {"node_name": "node-1"},
                    {"node_name": "node-1", "pre_state": {"was_unschedulable": True}},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["action"], "re-cordoned")
        mock_api.patch_node.assert_called_once_with("node-1", {"spec": {"unschedulable": True}})

    def test_rollback_noop_when_was_already_uncordoned(self):
        mock_api = MagicMock()
        with patch(
            "backend.app.connectors.executors.kubernetes.uncordon_node.get_k8s_clients",
            return_value=_make_core_clients(mock_api),
        ):
            from backend.app.connectors.executors.kubernetes import uncordon_node
            result = run(
                uncordon_node.rollback(
                    {"node_name": "node-1"},
                    {"node_name": "node-1", "pre_state": {"was_unschedulable": False}},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        self.assertIn("no-op", result["action"])
        mock_api.patch_node.assert_not_called()

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "backend.app.connectors.executors.kubernetes.uncordon_node.get_k8s_clients",
            return_value=_make_core_clients(MagicMock()),
        ):
            from backend.app.connectors.executors.kubernetes import uncordon_node
            result = run(
                uncordon_node.rollback({}, {"pre_state": {}}, FakeConnector())
            )
        self.assertFalse(result["rolled_back"])


if __name__ == "__main__":
    unittest.main()
