from app.models.patch_campaign import PatchCampaign, CampaignStatus

def test_campaign_status_enum_values():
    assert CampaignStatus.draft == "draft"
    assert CampaignStatus.running == "running"
    assert CampaignStatus.paused == "paused"
    assert CampaignStatus.complete == "complete"
    assert CampaignStatus.failed == "failed"
    assert CampaignStatus.aborted == "aborted"

def test_patch_campaign_has_required_fields():
    cols = {c.key for c in PatchCampaign.__table__.columns}
    for field in ("id", "organization_id", "title", "cve_id", "target_asset_ids",
                  "batch_size", "health_gate_seconds", "health_endpoint",
                  "abort_threshold", "rollout_strategy", "status", "batches"):
        assert field in cols, f"Missing column: {field}"
