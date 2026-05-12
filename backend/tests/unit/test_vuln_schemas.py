from app.schemas.patch_campaign import CampaignCreate, CampaignRead
from app.schemas.vulnerability import FindingPatchRequest, FindingMitigateRequest, FindingAssignRequest, FindingAcceptRiskRequest, SLAConfigExtended
import uuid

def test_campaign_create_defaults():
    c = CampaignCreate(title="Test", target_asset_ids=[uuid.uuid4()])
    assert c.batch_size == 5
    assert c.health_gate_seconds == 120
    assert c.abort_threshold == 0.2
    assert c.rollout_strategy == "rolling"

def test_finding_patch_request_defaults():
    r = FindingPatchRequest()
    assert r.rollback_on_failure is True
    assert r.verify_seconds == 60

def test_finding_mitigate_request():
    r = FindingMitigateRequest(selected_controls=["network_isolation", "seccomp"])
    assert "network_isolation" in r.selected_controls

def test_sla_config_extended_defaults():
    c = SLAConfigExtended()
    assert c.critical == 72
    assert c.auto_execute_on_breach is False
    assert c.severity_upgrade_hours == 48
