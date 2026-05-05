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
