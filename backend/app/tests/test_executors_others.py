import pytest
from app.connectors.executors.okta import generate_key, distribute_key, verify_consumers, schedule_revoke, cancel_revoke
from app.connectors.executors.ssh import validate_template, execute_template, collect_output, install_agent, uninstall_agent, check_prerequisites, download_package, start_service
from app.connectors.executors.paloalto import analyze_flows, generate_diff, stage_policy, remove_staged_policy, validate_staged


@pytest.mark.asyncio
async def test_generate_key_returns_new_key_id():
    result = await generate_key.execute({"service": "payment-api", "key_type": "api_key"}, [], None)
    assert result["action"] == "generate_key"
    assert result["new_key_id"].startswith("key-")


@pytest.mark.asyncio
async def test_schedule_revoke_rollback_cancels():
    result = await schedule_revoke.rollback({}, {"old_key_id": "key-old-123"}, None)
    assert result["rolled_back"] is True
    assert result["old_key_id"] == "key-old-123"


@pytest.mark.asyncio
async def test_execute_template_approved_succeeds():
    result = await execute_template.execute(
        {"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
        ["host-1"], None
    )
    assert result["action"] == "execute_template"
    assert result["host_results"][0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_execute_template_unapproved_raises():
    with pytest.raises(ValueError, match="not approved"):
        await execute_template.execute(
            {"template_id": "rm_everything", "parameters": {}},
            ["host-1"], None
        )


@pytest.mark.asyncio
async def test_execute_template_freeform_raises():
    with pytest.raises(ValueError, match="[Ff]reeform"):
        await execute_template.execute(
            {"template_id": "restart_service", "freeform_command": "rm -rf /"},
            ["host-1"], None
        )


@pytest.mark.asyncio
async def test_stage_policy_simulation_mode():
    result = await stage_policy.execute(
        {"policy_rules": [{"src": "web", "dst": "api", "port": 443}], "critical_flows": []},
        ["fw-1"], None
    )
    assert result["mode"] == "simulation"
    assert result["staged_policy_id"].startswith("pol-")


@pytest.mark.asyncio
async def test_stage_policy_rollback_removes():
    result = await stage_policy.rollback({}, {"staged_policy_id": "pol-abc123"}, None)
    assert result["rolled_back"] is True
    assert result["policy_id"] == "pol-abc123"


