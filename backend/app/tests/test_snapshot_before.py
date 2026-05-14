import uuid
import pytest


@pytest.mark.asyncio
async def test_snapshot_before_flag_is_accepted():
    """Verify snapshot_before round-trips through the schema."""
    from app.schemas.change_request import ChangeRequestCreate
    cr = ChangeRequestCreate(
        title="Patch with snapshot",
        change_type="agent_linux_patch",
        target_asset_ids=[uuid.uuid4()],
        desired_outcome={},
        snapshot_before=True,
    )
    assert cr.snapshot_before is True
