def test_cr_priority_schema():
    from app.schemas.change_request import ChangeRequestCreate
    cr = ChangeRequestCreate(
        title="Emergency patch",
        change_type="agent_linux_patch",
        target_asset_ids=["550e8400-e29b-41d4-a716-446655440000"],
        desired_outcome={},
        priority="emergency",
        emergency_reason="Zero-day exploit",
    )
    assert cr.priority == "emergency"
    assert cr.emergency_reason == "Zero-day exploit"
