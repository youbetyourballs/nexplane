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
    assert result["instance_type"] == "t3.micro"
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


import pathlib
import uuid
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.connectors.catalog_service import init_catalog_service

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def setup_module(module):
    init_catalog_service(CATALOG_DIR)


def _make_server_asset():
    return Asset(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), name="web-server-1",
        asset_type=AssetType.server, environment=Environment.prod,
        criticality=Criticality.high, asset_metadata={},
    )


def _make_cr(change_type, desired):
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=change_type,
        target_asset_ids=[], desired_outcome=desired, status=ChangeRequestStatus.draft,
    )
    return cr, [_make_server_asset()]


def test_ec2_stop_generates_three_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_stop, {"instance_id": "i-abc123", "snapshot_tag": "pre-stop"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 3
    assert plan.generated_steps[0]["generic_action"] == "capture_instance_state"
    assert plan.generated_steps[1]["generic_action"] == "stop_instance"
    assert plan.generated_steps[2]["generic_action"] == "wait_instance_state"


def test_ec2_start_generates_two_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_start, {"instance_id": "i-abc123"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 2
    assert plan.generated_steps[0]["generic_action"] == "start_instance"
    assert plan.generated_steps[1]["generic_action"] == "wait_instance_state"


def test_ec2_stop_start_generates_five_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_stop_start, {"instance_id": "i-abc123"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 5
    actions = [s["generic_action"] for s in plan.generated_steps]
    assert actions == ["capture_instance_state", "stop_instance", "wait_instance_state", "start_instance", "wait_instance_state"]


def test_ec2_launch_generates_three_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_launch, {"mode": "quick", "name": "new-server", "os": "amazon_linux"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 3
    assert plan.generated_steps[0]["generic_action"] == "resolve_launch_config"
    assert plan.generated_steps[1]["generic_action"] == "launch_instance"


def test_ec2_launch_rollback_strategy_is_automatic():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_launch, {"mode": "quick", "name": "test"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert plan.rollback_plan["automatic"] is True


def test_ec2_terminate_generates_three_steps():
    from app.models.change_request import ChangeType
    cr, assets = _make_cr(ChangeType.ec2_terminate, {"instance_id": "i-abc123", "confirm_terminate": True, "snapshot_tag": "pre-terminate"})
    plan = generate_plan(cr, assets, score_change_request(cr, assets))
    assert len(plan.generated_steps) == 3
    assert plan.generated_steps[2]["generic_action"] == "terminate_instance"
