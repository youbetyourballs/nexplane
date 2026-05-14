def test_campaign_failure_policy_schema():
    """Campaign model accepts failure_policy field."""
    from app.models.patch_campaign import PatchCampaign
    assert hasattr(PatchCampaign, 'failure_policy')
    assert hasattr(PatchCampaign, 'failed_cr_count')
    assert hasattr(PatchCampaign, 'succeeded_cr_count')
