# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import unittest
from unittest.mock import MagicMock, patch


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {"project_id": "my-project", "type": "service_account"}


def _make_fake_instance():
    inst = MagicMock()
    inst.machine_type = "zones/us-central1-a/machineTypes/n1-standard-2"
    ni = MagicMock()
    ni.network = "projects/my-project/global/networks/default"
    ni.subnetwork = "projects/my-project/regions/us-central1/subnetworks/default"
    inst.network_interfaces = [ni]
    meta_item = MagicMock()
    meta_item.key = "startup-script"
    meta_item.value = "#!/bin/bash"
    inst.metadata = MagicMock()
    inst.metadata.items = [meta_item]
    inst.tags = MagicMock()
    inst.tags.items = ["http-server"]
    inst.labels = {"env": "prod"}
    return inst


class TestDeleteInstanceRollback(unittest.TestCase):
    def test_execute_captures_pre_state(self):
        fake_instance = _make_fake_instance()
        mock_client = MagicMock()
        mock_client.get.return_value = fake_instance
        mock_op = MagicMock()
        mock_op.operation.name = "operation-123"
        mock_client.delete.return_value = mock_op

        with patch(
            "app.connectors.executors.gcp.delete_instance.get_credentials",
            return_value=MagicMock(),
        ), patch(
            "app.connectors.executors.gcp.delete_instance.get_project_id",
            return_value="my-project",
        ), patch(
            "google.cloud.compute_v1.InstancesClient",
            return_value=mock_client,
        ):
            from app.connectors.executors.gcp import delete_instance
            result = run(
                delete_instance.execute(
                    {"instance_name": "my-vm", "zone": "us-central1-a"},
                    [],
                    FakeConnector(),
                )
            )

        self.assertIn("pre_state", result)
        ps = result["pre_state"]
        self.assertEqual(ps["name"], "my-vm")
        self.assertEqual(ps["machine_type"], "n1-standard-2")
        self.assertEqual(ps["tags"], ["http-server"])
        self.assertEqual(ps["labels"], {"env": "prod"})
        self.assertEqual(ps["metadata"]["startup-script"], "#!/bin/bash")

    def test_rollback_recreates_instance(self):
        mock_client = MagicMock()
        mock_op = MagicMock()
        mock_op.operation.name = "rollback-op-456"
        mock_client.insert.return_value = mock_op

        pre_state = {
            "name": "my-vm",
            "machine_type": "n1-standard-2",
            "zone": "us-central1-a",
            "project": "my-project",
            "network_interfaces": [{"network": "default", "subnetwork": "default"}],
            "metadata": {"startup-script": "#!/bin/bash"},
            "tags": ["http-server"],
            "labels": {"env": "prod"},
        }

        with patch(
            "app.connectors.executors.gcp.delete_instance.get_credentials",
            return_value=MagicMock(),
        ), patch(
            "app.connectors.executors.gcp.delete_instance.get_project_id",
            return_value="my-project",
        ), patch(
            "google.cloud.compute_v1.InstancesClient",
            return_value=mock_client,
        ), patch(
            "google.cloud.compute_v1.Instance",
        ) as mock_instance_cls, patch(
            "google.cloud.compute_v1.AttachedDisk",
        ), patch(
            "google.cloud.compute_v1.AttachedDiskInitializeParams",
        ), patch(
            "google.cloud.compute_v1.NetworkInterface",
        ), patch(
            "google.cloud.compute_v1.Tags",
        ), patch(
            "google.cloud.compute_v1.Metadata",
        ), patch(
            "google.cloud.compute_v1.Items",
        ):
            from app.connectors.executors.gcp import delete_instance
            result = run(
                delete_instance.rollback(
                    {"instance_name": "my-vm", "zone": "us-central1-a"},
                    {"instance_name": "my-vm", "zone": "us-central1-a", "pre_state": pre_state},
                    FakeConnector(),
                )
            )

        self.assertTrue(result["rolled_back"])
        self.assertIn("blank boot disk", result["note"])
        mock_client.insert.assert_called_once()

    def test_rollback_no_pre_state_returns_false(self):
        with patch(
            "app.connectors.executors.gcp.delete_instance.get_credentials",
            return_value=MagicMock(),
        ), patch(
            "app.connectors.executors.gcp.delete_instance.get_project_id",
            return_value="my-project",
        ), patch("google.cloud.compute_v1.InstancesClient", return_value=MagicMock()):
            from app.connectors.executors.gcp import delete_instance
            result = run(
                delete_instance.rollback(
                    {}, {"pre_state": {}}, FakeConnector()
                )
            )
        self.assertFalse(result["rolled_back"])
        self.assertIn("reason", result)


if __name__ == "__main__":
    unittest.main()
