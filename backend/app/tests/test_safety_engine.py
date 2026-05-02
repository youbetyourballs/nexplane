import uuid
import pytest
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.services.safety_engine import score_change_request, RiskLevel as SRiskLevel


def make_asset(env=Environment.prod, crit=Criticality.critical):
    return Asset(
        id=uuid.uuid4(), organization_id=uuid.uuid4(),
        name="test", asset_type=AssetType.application,
        environment=env, criticality=crit, asset_metadata={}
    )


def make_cr(change_type=ChangeType.dns_update, desired=None, asset_ids=None):
    return ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=change_type,
        target_asset_ids=[str(a) for a in (asset_ids or [])],
        desired_outcome=desired if desired is not None else {"rollback_strategy": "restore_previous_record"},
        status=ChangeRequestStatus.draft,
    )


def test_prod_critical_asset_scores_high():
    asset = make_asset(Environment.prod, Criticality.critical)
    cr = make_cr(desired={"rollback_strategy": "restore_previous_record"})
    result = score_change_request(cr, [asset])
    assert result.risk_level in (SRiskLevel.high, SRiskLevel.critical)
    assert result.risk_score >= 60


def test_dev_low_criticality_scores_low():
    asset = make_asset(Environment.dev, Criticality.low)
    cr = make_cr(desired={"rollback_strategy": "some_strategy"})
    result = score_change_request(cr, [asset])
    assert result.risk_level == SRiskLevel.low
    assert result.risk_score < 30


def test_remote_command_without_template_is_blocked():
    cr = make_cr(change_type=ChangeType.remote_command, desired={"freeform_command": "rm -rf /"})
    result = score_change_request(cr, [])
    assert result.is_blocked
    assert any("template" in issue.lower() for issue in result.blocking_issues)


def test_remote_command_freeform_is_blocked():
    cr = make_cr(
        change_type=ChangeType.remote_command,
        desired={"template_id": "restart_service", "freeform_command": "rm -rf /"},
    )
    result = score_change_request(cr, [])
    assert result.is_blocked


def test_remote_command_approved_template_passes():
    cr = make_cr(
        change_type=ChangeType.remote_command,
        desired={"template_id": "restart_service", "parameters": {"service_name": "nginx"}, "rollback_strategy": "manual"},
    )
    result = score_change_request(cr, [])
    assert not result.is_blocked


def test_missing_rollback_on_prod_critical_is_blocked():
    asset = make_asset(Environment.prod, Criticality.critical)
    cr = make_cr(desired={})  # No rollback_strategy
    result = score_change_request(cr, [asset])
    assert result.is_blocked
    assert any("rollback" in issue.lower() for issue in result.blocking_issues)


def test_missing_rollback_no_assets_not_blocked():
    cr = make_cr(desired={})  # No rollback, but no prod/critical assets
    result = score_change_request(cr, [])
    assert not result.is_blocked
    assert any(f.name == "no_rollback_strategy" for f in result.risk_factors)


def test_microsegmentation_large_blast_radius():
    asset = make_asset(Environment.prod, Criticality.medium)
    cr = make_cr(
        change_type=ChangeType.microsegmentation_policy,
        desired={"rollback_strategy": "remove_staged_policy"},
        asset_ids=[uuid.uuid4() for _ in range(12)],
    )
    result = score_change_request(cr, [asset])
    assert any(f.name == "large_blast_radius" for f in result.risk_factors)


def test_risk_level_enum_values():
    assert SRiskLevel.low.value == "low"
    assert SRiskLevel.critical.value == "critical"


def test_ssh_tier5_steps_add_risk():
    from app.connectors.catalog_service import init_catalog_service
    import pathlib
    init_catalog_service(pathlib.Path(__file__).parent.parent / "connectors" / "catalog")

    from app.services.safety_engine import adjust_for_execution_plan

    tier5_steps = [
        {"generic_action": "execute_template", "execution_tier": 5, "connector_type": "ssh"}
    ]
    tier1_steps = [
        {"generic_action": "update_dns_record", "execution_tier": 1, "connector_type": "cloudflare"}
    ]

    base_result = score_change_request(
        make_cr(ChangeType.remote_command, {"template_id": "restart_service", "parameters": {}, "rollback_strategy": "manual"}),
        [make_asset()],
    )
    adjusted_tier5 = adjust_for_execution_plan(base_result, tier5_steps)
    adjusted_tier1 = adjust_for_execution_plan(base_result, tier1_steps)
    assert adjusted_tier5.risk_score >= adjusted_tier1.risk_score


