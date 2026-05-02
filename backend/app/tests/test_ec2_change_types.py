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


@pytest.mark.asyncio
async def test_wait_instance_state_mock_returns_target():
    from app.connectors.executors.aws.wait_instance_state import execute
    result = await execute({"instance_id": "i-abc123", "target_state": "running"}, [], _mock_connector())
    assert result["action"] == "wait_instance_state"
    assert result["reached_state"] == "running"


@pytest.mark.asyncio
async def test_resolve_launch_config_quick_mode():
    from app.connectors.executors.aws.resolve_launch_config import execute
    result = await execute({"mode": "quick", "name": "my-server", "os": "amazon_linux"}, [], _mock_connector())
    assert result["action"] == "resolve_launch_config"
    assert result["instance_type"] == "t2.micro"
    assert "ami_id" in result
    assert result["name"] == "my-server"


@pytest.mark.asyncio
async def test_resolve_launch_config_spec_mode():
    from app.connectors.executors.aws.resolve_launch_config import execute
    params = {
        "mode": "spec",
        "name": "spec-server",
        "ami_id": "ami-0abc123",
        "instance_type": "t3.small",
        "subnet_id": "subnet-abc",
        "security_group_ids": ["sg-abc"],
    }
    result = await execute(params, [], _mock_connector())
    assert result["ami_id"] == "ami-0abc123"
    assert result["instance_type"] == "t3.small"


@pytest.mark.asyncio
async def test_launch_instance_mock():
    from app.connectors.executors.aws.launch_instance import execute
    params = {"ami_id": "ami-0abc", "instance_type": "t2.micro", "subnet_id": "subnet-0", "security_group_ids": ["sg-0"], "name": "test"}
    result = await execute(params, [], _mock_connector())
    assert result["action"] == "launch_instance"
    assert result["instance_id"].startswith("i-")


@pytest.mark.asyncio
async def test_launch_instance_rollback_terminates():
    from app.connectors.executors.aws.launch_instance import rollback
    result = await rollback({}, {"instance_id": "i-abc123"}, _mock_connector())
    assert result["action"] == "terminate_instance"


@pytest.mark.asyncio
async def test_terminate_instance_blocked_without_confirm():
    from app.connectors.executors.aws.terminate_instance import execute
    result = await execute({"instance_id": "i-abc123"}, [], _mock_connector())
    assert "error" in result


@pytest.mark.asyncio
async def test_terminate_instance_mock_with_confirm():
    from app.connectors.executors.aws.terminate_instance import execute
    result = await execute({"instance_id": "i-abc123", "confirm_terminate": True}, [], _mock_connector())
    assert result["action"] == "terminate_instance"
    assert result["instance_id"] == "i-abc123"
