import pytest
from app.models.change_request import ChangeType
from app.models.connector import ConnectorType
from app.services.connector_service import (
    execute_change,
    execute_rollback,
    run_preflight_checks,
    run_verification_checks,
    ConnectorError,
)


@pytest.mark.asyncio
async def test_dns_update_returns_previous_value():
    result = await execute_change(
        ChangeType.dns_update,
        {"record_name": "api.example.com", "new_value": "1.2.3.4", "ttl": 300},
        ["asset-1"],
        ConnectorType.cloudflare_mock,
    )
    assert result["action"] == "dns_update"
    assert result["previous_value"] is not None
    assert result["new_value"] == "1.2.3.4"
    assert "propagation_id" in result


@pytest.mark.asyncio
async def test_snapshot_returns_snapshot_ids():
    result = await execute_change(
        ChangeType.snapshot_asset,
        {"snapshot_tag": "test"},
        ["asset-1", "asset-2"],
        ConnectorType.aws_mock,
    )
    assert result["action"] == "snapshot_asset"
    assert len(result["snapshots"]) == 2
    for snap in result["snapshots"]:
        assert snap["snapshot_id"].startswith("snap-")


@pytest.mark.asyncio
async def test_remote_command_approved_template_succeeds():
    result = await execute_change(
        ChangeType.remote_command,
        {"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
        ["host-1"],
        ConnectorType.ssh_runner_mock,
    )
    assert result["action"] == "remote_command"
    assert result["host_results"][0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_remote_command_unapproved_template_raises():
    with pytest.raises(ConnectorError) as exc_info:
        await execute_change(
            ChangeType.remote_command,
            {"template_id": "rm_everything", "parameters": {}},
            ["host-1"],
            ConnectorType.ssh_runner_mock,
        )
    assert "not approved" in str(exc_info.value)


@pytest.mark.asyncio
async def test_remote_command_freeform_raises():
    with pytest.raises(ConnectorError) as exc_info:
        await execute_change(
            ChangeType.remote_command,
            {"template_id": "restart_service", "freeform_command": "rm -rf /"},
            ["host-1"],
            ConnectorType.ssh_runner_mock,
        )
    assert "freeform" in str(exc_info.value).lower() or "permitted" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_microsegmentation_staged_only():
    result = await execute_change(
        ChangeType.microsegmentation_policy,
        {"policy_rules": [{"src": "a", "dst": "b", "port": 443, "action": "allow"}]},
        ["asset-1"],
        ConnectorType.paloalto_mock,
    )
    assert result["mode"] == "simulation"
    assert "staged_policy_id" in result


@pytest.mark.asyncio
async def test_preflight_all_pass():
    checks = [
        {"name": "check_1", "description": "test check", "check_type": "connectivity", "expected_result": "ok"},
    ]
    result = await run_preflight_checks(checks)
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_verification_passes_for_mock():
    plan = {
        "checks": [{"name": "dns_resolves", "description": "DNS check", "method": "dns_lookup"}],
        "success_criteria": "DNS resolves",
    }
    result = await run_verification_checks(plan, {"action": "dns_update"})
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_rollback_dns_restores_previous_value():
    rollback_plan = {"strategy": "restore_previous_record", "automatic": True}
    execution_result = {"previous_value": "10.0.0.1"}
    result = await execute_rollback(None, rollback_plan, execution_result)
    assert result["rolled_back"] is True
    assert result["restored_value"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_rollback_unavailable_not_rolled_back():
    rollback_plan = {"strategy": "rollback_unavailable", "description": "No rollback"}
    result = await execute_rollback(None, rollback_plan, {})
    assert result["rolled_back"] is False
    assert result["manual_steps_required"] is True
