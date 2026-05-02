import pytest
from app.connectors.executors.aws import (
    health_check, create_snapshot, verify_snapshot,
    export_security_group, validate_security_rules,
    update_security_group, restore_security_group,
)


@pytest.mark.asyncio
async def test_health_check_passes():
    result = await health_check.execute({}, ["asset-1"], None)
    assert result["action"] == "health_check"
    assert result["healthy"] is True


@pytest.mark.asyncio
async def test_create_snapshot_returns_per_asset():
    result = await create_snapshot.execute(
        {"snapshot_tag": "test"}, ["asset-1", "asset-2"], None
    )
    assert result["action"] == "create_snapshot"
    assert len(result["snapshots"]) == 2
    assert all(s["snapshot_id"].startswith("snap-") for s in result["snapshots"])


@pytest.mark.asyncio
async def test_verify_snapshot_passes():
    result = await verify_snapshot.execute({}, ["asset-1"], None)
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_update_security_group_records_rules():
    rules = [{"action": "add", "protocol": "tcp", "port": 443, "source": "0.0.0.0/0"}]
    result = await update_security_group.execute(
        {"group_id": "sg-123", "rules": rules}, ["asset-1"], None
    )
    assert result["action"] == "update_security_group"
    assert result["rules_applied"] == 1
    assert "pre_change_snapshot_id" in result


@pytest.mark.asyncio
async def test_update_security_group_rollback():
    result = await update_security_group.rollback(
        {}, {"pre_change_snapshot_id": "sgsnap-abc"}, None
    )
    assert result["rolled_back"] is True
    assert result["restored_from_snapshot"] == "sgsnap-abc"

