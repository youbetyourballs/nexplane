from app.schemas.change_request import ChangeRequestCreate


def test_cr_create_accepts_finding_ids():
    cr = ChangeRequestCreate(
        title="Patch CVE-2024-1234",
        change_type="agent_linux_patch",
        target_asset_ids=["00000000-0000-0000-0000-000000000001"],
        desired_outcome={},
        finding_ids=["finding-uuid-1", "finding-uuid-2"],
    )
    assert len(cr.finding_ids) == 2
