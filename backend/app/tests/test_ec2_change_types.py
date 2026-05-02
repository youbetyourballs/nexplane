# backend/app/tests/test_ec2_change_types.py
import pytest


def _mock_connector(creds=None):
    class C:
        credentials = creds or {}
    return C()


@pytest.mark.asyncio
async def test_capture_instance_state_mock():
    from app.connectors.executors.aws.capture_instance_state import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "capture_instance_state"
    assert "instance_id" in result
    assert "state" in result


@pytest.mark.asyncio
async def test_stop_instance_mock():
    from app.connectors.executors.aws.stop_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "stop_instance"
    assert result["instance_id"] == "i-abc123"


@pytest.mark.asyncio
async def test_stop_instance_rollback_calls_start():
    from app.connectors.executors.aws.stop_instance import rollback
    result = await rollback({"instance_id": "i-abc123"}, {}, _mock_connector())
    assert result["action"] == "start_instance"


@pytest.mark.asyncio
async def test_start_instance_mock():
    from app.connectors.executors.aws.start_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "start_instance"
    assert result["instance_id"] == "i-abc123"


@pytest.mark.asyncio
async def test_start_instance_rollback_calls_stop():
    from app.connectors.executors.aws.start_instance import rollback
    result = await rollback({"instance_id": "i-abc123"}, {}, _mock_connector())
    assert result["action"] == "stop_instance"


@pytest.mark.asyncio
async def test_reboot_instance_mock():
    from app.connectors.executors.aws.reboot_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert result["action"] == "reboot_instance"
    assert result["rebooted"] is True
