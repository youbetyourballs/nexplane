import pytest


def test_discover_vms_mock_returns_empty():
    import asyncio
    from app.connectors.executors.azure.discover_vms import execute
    result = asyncio.run(execute({}, [], None))
    assert result == []


def test_deallocate_vm_mock():
    import asyncio
    from app.connectors.executors.azure.deallocate_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "deallocate_vm"
    assert result["mock"] is True


def test_start_vm_mock():
    import asyncio
    from app.connectors.executors.azure.start_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "start_vm"
    assert result["mock"] is True


def test_deallocate_vm_rollback_calls_start():
    import asyncio
    from app.connectors.executors.azure.deallocate_vm import rollback
    result = asyncio.run(rollback({"resource_group": "rg", "vm_name": "vm1"}, {}, None))
    assert result["action"] == "start_vm"
    assert result["mock"] is True


def test_start_vm_rollback_calls_deallocate():
    import asyncio
    from app.connectors.executors.azure.start_vm import rollback
    result = asyncio.run(rollback({"resource_group": "rg", "vm_name": "vm1"}, {}, None))
    assert result["action"] == "deallocate_vm"
    assert result["mock"] is True


def test_capture_vm_state_mock():
    import asyncio
    from app.connectors.executors.azure.capture_vm_state import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "capture_vm_state"
    assert result["vm_name"] == "vm1"
    assert result["mock"] is True


def test_health_check_mock():
    import asyncio
    from app.connectors.executors.azure.health_check import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "health_check"
    assert result["power_state"] == "PowerState/running"


def test_wait_vm_state_mock():
    import asyncio
    from app.connectors.executors.azure.wait_vm_state import execute
    result = asyncio.run(execute(
        {"resource_group": "rg", "vm_name": "vm1", "target_state": "running"}, [], None
    ))
    assert result["action"] == "wait_vm_state"
    assert result["reached"] is True


def test_reboot_vm_mock():
    import asyncio
    from app.connectors.executors.azure.reboot_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "reboot_vm"
    assert result["vm_name"] == "vm1"


def test_terminate_vm_mock():
    import asyncio
    from app.connectors.executors.azure.terminate_vm import execute
    result = asyncio.run(execute({"resource_group": "rg", "vm_name": "vm1"}, [], None))
    assert result["action"] == "terminate_vm"
    assert result["deleted"] is True


def test_create_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.azure.create_disk_snapshot import execute
    result = asyncio.run(execute(
        {"resource_group": "rg", "vm_name": "vm1", "snapshot_name": "snap-001"}, [], None
    ))
    assert result["action"] == "create_disk_snapshot"
    assert result["snapshot_name"] == "snap-001"


def test_delete_disk_snapshot_mock():
    import asyncio
    from app.connectors.executors.azure.delete_disk_snapshot import execute
    result = asyncio.run(execute({"resource_group": "rg", "snapshot_name": "snap-001"}, [], None))
    assert result["action"] == "delete_disk_snapshot"
    assert result["deleted"] is True


def test_run_command_mock():
    import asyncio
    from app.connectors.executors.azure.run_command import execute
    result = asyncio.run(execute(
        {"resource_group": "rg", "vm_name": "vm1", "command": "uptime"}, [], None
    ))
    assert result["action"] == "run_command"
    assert result["vm_name"] == "vm1"


def test_launch_vm_mock_agent_extension():
    import asyncio
    from app.connectors.executors.azure.launch_vm import execute
    result = asyncio.run(execute({
        "vm_name": "nexplane-smoke-azure-01",
        "resource_group": "nexplane-smoke-rg",
        "location": "eastus",
        "vm_size": "Standard_B1s",
        "connection_mode": "agent_extension",
        "nexplane_url": "http://localhost:8000",
        "nexplane_secret": "test-secret",
    }, [], None))
    assert result["action"] == "launch_vm"
    assert result["vm_name"] == "nexplane-smoke-azure-01"
    assert "_auto_asset" in result
    assert result["_auto_asset"]["asset_type"] == "server"
    assert result["mock"] is True


def test_launch_vm_mock_ssh_mode():
    import asyncio
    from app.connectors.executors.azure.launch_vm import execute
    result = asyncio.run(execute({
        "vm_name": "nexplane-smoke-azure-02",
        "resource_group": "nexplane-smoke-rg",
        "location": "eastus",
        "vm_size": "Standard_B1s",
        "connection_mode": "ssh",
        "ssh_public_key": "ssh-rsa AAAA...",
    }, [], None))
    assert result["connection_mode"] == "ssh"
    assert result["mock"] is True
