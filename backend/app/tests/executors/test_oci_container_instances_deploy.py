# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "user": "ocid1.user.test", "private_key": "key",
        "fingerprint": "fp", "tenancy": "ocid1.tenancy.test", "region": "us-ashburn-1",
    }
    return c


async def test_deploy_captures_previous_image(connector):
    old_container = MagicMock()
    old_container.image_url = "ocir.io/namespace/app:v1"
    mock_instance = MagicMock()
    mock_instance.containers = [old_container]
    mock_instance.id = "ocid1.containerinstance.old"
    mock_instance.lifecycle_state = "ACTIVE"
    mock_instance.availability_domain = "ad1"
    mock_instance.shape = "CI.Standard.E4.Flex"

    new_instance = MagicMock()
    new_instance.id = "ocid1.containerinstance.new"
    new_instance.lifecycle_state = "ACTIVE"

    call_count = [0]
    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_instance
        if call_count[0] == 2:
            return MagicMock()  # delete
        return new_instance

    with patch("app.connectors.executors.oci.oci_container_instances_deploy._run", side_effect=fake_run):
        with patch("app.connectors.executors.oci.oci_container_instances_deploy._wait_active",
                   new=AsyncMock(return_value="ocid1.containerinstance.new")):
            from app.connectors.executors.oci.oci_container_instances_deploy import execute
            result = await execute(
                {"compartment_id": "ocid1.compartment.test",
                 "instance_id": "ocid1.containerinstance.old",
                 "image": "ocir.io/namespace/app:v2"},
                [], connector,
            )
    assert result["previous_image"] == "ocir.io/namespace/app:v1"
    assert result["status"] == "deployed"
