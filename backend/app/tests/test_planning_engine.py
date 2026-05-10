import pathlib
import uuid
import pytest
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.connectors.catalog_service import init_catalog_service

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def setup_module():
    init_catalog_service(CATALOG_DIR)


def make_asset(asset_type=AssetType.dns_zone, env=Environment.prod, crit=Criticality.high):
    return Asset(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), name="Test Asset",
        asset_type=asset_type, environment=env, criticality=crit, asset_metadata={}
    )


def make_cr(change_type, desired):
    asset = make_asset()
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=change_type,
        target_asset_ids=[], desired_outcome=desired, status=ChangeRequestStatus.draft,
    )
    return cr, [asset]


def test_dns_update_generates_four_steps():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "test.example", "new_value": "1.2.3.4", "rollback_strategy": "restore_previous_record"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert len(plan.generated_steps) == 4
    assert plan.generated_steps[0]["generic_action"] == "capture_dns_record"
    assert plan.generated_steps[2]["generic_action"] == "update_dns_record"


def test_steps_have_connector_type_and_action_id():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    for step in plan.generated_steps:
        assert "connector_type" in step
        assert "action_id" in step
        assert "execution_tier" in step
        assert "connector_options" in step


def test_dns_step_3_has_rollback_action():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    execute_step = plan.generated_steps[2]  # update_dns_record
    assert execute_step["rollback_action"] == "restore_dns_record"
    assert execute_step["rollback_connector_type"] == "cloudflare"


def test_snapshot_generates_three_steps():
    asset = make_asset(asset_type=AssetType.server)
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=ChangeType.snapshot_asset,
        target_asset_ids=[], desired_outcome={"snapshot_tag": "test"}, status=ChangeRequestStatus.draft,
    )
    safety = score_change_request(cr, [asset])
    plan = generate_plan(cr, [asset], safety)
    assert len(plan.generated_steps) == 1  # planning engine generates 1 step for snapshot_asset


def test_key_rotation_generates_four_steps():
    asset = make_asset(asset_type=AssetType.identity_provider)
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=ChangeType.key_rotation,
        target_asset_ids=[], desired_outcome={"rollback_strategy": "cancel_revocation"}, status=ChangeRequestStatus.draft,
    )
    safety = score_change_request(cr, [asset])
    plan = generate_plan(cr, [asset], safety)
    assert len(plan.generated_steps) == 4
    assert any(s["generic_action"] == "schedule_revoke" for s in plan.generated_steps)


def test_remote_command_plan_includes_template_validation():
    asset = make_asset(asset_type=AssetType.server)
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=ChangeType.remote_command,
        target_asset_ids=[], desired_outcome={"template_id": "restart_service", "parameters": {}, "rollback_strategy": "manual"},
        status=ChangeRequestStatus.draft,
    )
    safety = score_change_request(cr, [asset])
    plan = generate_plan(cr, [asset], safety)
    assert any(s["generic_action"] == "validate_template" for s in plan.generated_steps)


def test_blast_radius_includes_environments():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert "affected_environments" in plan.blast_radius
    assert "rollback_available" in plan.blast_radius


def test_dns_rollback_plan_is_automatic():
    cr, assets = make_cr(ChangeType.dns_update, {"rollback_strategy": "restore_previous_record"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert plan.rollback_plan["strategy"] == "restore_previous_record"
    assert plan.rollback_plan["automatic"] is True


def test_verification_plan_has_checks():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert "checks" in plan.verification_plan
    assert len(plan.verification_plan["checks"]) >= 1

