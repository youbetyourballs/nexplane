import uuid
import pytest
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request, SafetyReviewResult


def make_asset(env=Environment.prod, crit=Criticality.high):
    return Asset(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), name="Test Asset",
        asset_type=AssetType.dns_zone, environment=env, criticality=crit, metadata={}
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
    assert plan.generated_steps[0]["name"] == "Capture Current DNS Record"
    assert plan.generated_steps[2]["rollback_action"] == "dns.update_record"


def test_snapshot_generates_three_steps():
    cr, assets = make_cr(ChangeType.snapshot_asset, {"snapshot_tag": "test"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert len(plan.generated_steps) == 3


def test_key_rotation_generates_four_steps():
    cr, assets = make_cr(ChangeType.key_rotation, {"rollback_strategy": "cancel_revocation"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert len(plan.generated_steps) == 4
    assert any("Revocation" in s["name"] for s in plan.generated_steps)


def test_remote_command_plan_includes_template_validation():
    cr, assets = make_cr(ChangeType.remote_command, {"template_id": "restart_service", "parameters": {}, "rollback_strategy": "manual"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert any("Validate Command Template" in s["name"] for s in plan.generated_steps)


def test_blast_radius_includes_environments():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1", "rollback_strategy": "restore"})
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


def test_snapshot_rollback_is_unavailable():
    cr, assets = make_cr(ChangeType.snapshot_asset, {})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert plan.rollback_plan["strategy"] == "rollback_unavailable"


def test_verification_plan_has_checks():
    for ct in ChangeType:
        desired = {"rollback_strategy": "x"}
        if ct == ChangeType.remote_command:
            desired["template_id"] = "restart_service"
            desired["parameters"] = {}
        cr, assets = make_cr(ct, desired)
        safety = score_change_request(cr, assets)
        plan = generate_plan(cr, assets, safety)
        assert "checks" in plan.verification_plan
        assert len(plan.verification_plan["checks"]) >= 1
