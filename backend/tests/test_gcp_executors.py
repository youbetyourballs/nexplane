# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


def test_capture_instance_state_mock():
    import asyncio
    from app.connectors.executors.gcp.capture_instance_state import execute
    result = asyncio.run(execute({"instance_name": "test-vm", "zone": "us-central1-a"}, [], None))
    assert result["action"] == "capture_instance_state"
    assert result["instance_name"] == "test-vm"
    assert result["mock"] is True


def test_health_check_mock():
    import asyncio
    from app.connectors.executors.gcp.health_check import execute
    result = asyncio.run(execute({"instance_name": "test-vm", "zone": "us-central1-a"}, [], None))
    assert result["action"] == "health_check"
    assert result["status"] == "RUNNING"


def test_gce_change_types_in_enum():
    from app.models.change_request import ChangeType
    assert ChangeType.gce_instance_create == "gce_instance_create"
    assert ChangeType.gce_stop == "gce_stop"
    assert ChangeType.gce_start == "gce_start"
    assert ChangeType.gce_instance_reboot == "gce_instance_reboot"
    assert ChangeType.gce_instance_delete == "gce_instance_delete"
    assert ChangeType.gce_disk_snapshot == "gce_disk_snapshot"


def test_asset_read_has_connector_type_field():
    from app.schemas.asset import AssetRead
    fields = AssetRead.model_fields
    assert "connector_type" in fields, "AssetRead must expose connector_type for UI filtering"


def test_wait_instance_state_mock():
    import asyncio
    from app.connectors.executors.gcp.wait_instance_state import execute
    result = asyncio.run(execute(
        {"instance_name": "test-vm", "zone": "us-central1-a", "target_state": "RUNNING"}, [], None
    ))
    assert result["action"] == "wait_instance_state"
    assert result["target_state"] == "RUNNING"


def test_reboot_instance_mock():
    import asyncio
    from app.connectors.executors.gcp.reboot_instance import execute
    result = asyncio.run(execute({"instance_name": "test-vm", "zone": "us-central1-a"}, [], None))
    assert result["action"] == "reboot_instance"
    assert result["instance_name"] == "test-vm"


def test_create_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.gcp.create_disk_snapshot import execute
    result = asyncio.run(execute(
        {"instance_name": "test-vm", "zone": "us-central1-a", "snapshot_name": "snap-001"}, [], None
    ))
    assert result["action"] == "create_disk_snapshot"
    assert result["snapshot_name"] == "snap-001"


def test_delete_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.gcp.delete_disk_snapshot import execute
    result = asyncio.run(execute({"snapshot_name": "snap-001"}, [], None))
    assert result["action"] == "delete_disk_snapshot"
    assert result["snapshot_name"] == "snap-001"


def test_launch_instance_mock_agent_startup():
    import asyncio
    from app.connectors.executors.gcp.launch_instance import execute
    result = asyncio.run(execute({
        "name": "nexplane-smoke-test-gcp-01",
        "machine_type": "e2-micro",
        "zone": "us-central1-a",
        "image_family": "ubuntu-2204-lts",
        "image_project": "ubuntu-os-cloud",
        "connection_mode": "agent_startup",
    }, [], None))
    assert result["action"] == "launch_instance"
    assert result["instance_name"] == "nexplane-smoke-test-gcp-01"
    assert "_auto_asset" in result
    assert result["_auto_asset"]["asset_type"] == "server"
    assert result["mock"] is True


def test_launch_instance_mock_ssh_mode():
    import asyncio
    from app.connectors.executors.gcp.launch_instance import execute
    result = asyncio.run(execute({
        "name": "nexplane-smoke-test-gcp-02",
        "machine_type": "e2-micro",
        "zone": "us-central1-a",
        "image_family": "ubuntu-2204-lts",
        "image_project": "ubuntu-os-cloud",
        "connection_mode": "ssh",
        "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc...",
    }, [], None))
    assert result["connection_mode"] == "ssh"
    assert result["mock"] is True
