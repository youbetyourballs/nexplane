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


def test_wait_instance_state_mock():
    import asyncio
    from app.connectors.executors.gcp.wait_instance_state import execute
    result = asyncio.run(execute(
        {"instance_name": "test-vm", "zone": "us-central1-a", "target_state": "RUNNING"}, [], None
    ))
    assert result["action"] == "wait_instance_state"
    assert result["target_state"] == "RUNNING"
