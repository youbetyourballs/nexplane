import pytest
from app.connectors.executors.cloudflare_mock import (
    capture_dns_record,
    validate_dns_target,
    update_dns_record,
    wait_dns_propagation,
    restore_dns_record,
)


@pytest.mark.asyncio
async def test_capture_dns_record_returns_current_value():
    result = await capture_dns_record.execute(
        {"record_name": "api.example.com", "record_type": "A"}, [], None
    )
    assert result["action"] == "capture_dns_record"
    assert "current_value" in result
    assert result["record_name"] == "api.example.com"


@pytest.mark.asyncio
async def test_validate_dns_target_passes():
    result = await validate_dns_target.execute({"new_value": "1.2.3.4"}, [], None)
    assert result["action"] == "validate_dns_target"
    assert result["reachable"] is True


@pytest.mark.asyncio
async def test_update_dns_record_returns_previous_and_new():
    result = await update_dns_record.execute(
        {"record_name": "api.example.com", "record_type": "A", "new_value": "1.2.3.4", "ttl": 300},
        [], None
    )
    assert result["action"] == "update_dns_record"
    assert result["new_value"] == "1.2.3.4"
    assert "previous_value" in result
    assert "propagation_id" in result


@pytest.mark.asyncio
async def test_update_dns_record_rollback_restores():
    result = await update_dns_record.rollback(
        {"record_name": "api.example.com"},
        {"previous_value": "10.0.0.1"},
        None
    )
    assert result["rolled_back"] is True
    assert result["restored_value"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_restore_dns_record_uses_previous_value():
    result = await restore_dns_record.execute(
        {"record_name": "api.example.com", "previous_value": "10.0.0.1"}, [], None
    )
    assert result["action"] == "restore_dns_record"
    assert result["restored_value"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_wait_dns_propagation_succeeds():
    result = await wait_dns_propagation.execute({"ttl": 300}, [], None)
    assert result["action"] == "wait_dns_propagation"
    assert result["propagated"] is True
