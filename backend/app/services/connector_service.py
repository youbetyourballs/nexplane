import asyncio
import uuid
import random
import string
from datetime import datetime, timezone
from typing import Any

from app.models.change_request import ChangeType
from app.models.connector import ConnectorType
from app.services.safety_engine import APPROVED_COMMAND_TEMPLATES


class ConnectorError(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


async def test_connector(connector_type: ConnectorType) -> dict:
    await asyncio.sleep(0.1)
    endpoint_map = {
        ConnectorType.aws_mock: "https://mock.aws.nexplane.local",
        ConnectorType.azure_mock: "https://mock.azure.nexplane.local",
        ConnectorType.cloudflare_mock: "https://mock.cloudflare.nexplane.local",
        ConnectorType.okta_mock: "https://mock.okta.nexplane.local",
        ConnectorType.paloalto_mock: "https://mock.paloalto.nexplane.local",
        ConnectorType.ssh_runner_mock: "ssh://mock.runner.nexplane.local",
    }
    return {
        "success": True,
        "latency_ms": random.randint(12, 85),
        "message": f"Mock connector '{connector_type.value}' is reachable",
        "details": {
            "endpoint": endpoint_map.get(connector_type, "mock://local"),
            "auth_method": "mock_token",
            "permissions_verified": True,
        },
    }


async def execute_change(
    change_type: ChangeType,
    desired_outcome: dict,
    asset_ids: list[str],
    connector_type: ConnectorType,
) -> dict[str, Any]:
    await asyncio.sleep(0.5)

    executor_map = {
        ChangeType.dns_update: _execute_dns_update,
        ChangeType.snapshot_asset: _execute_snapshot,
        ChangeType.security_group_update: _execute_security_group_update,
        ChangeType.key_rotation: _execute_key_rotation,
        ChangeType.telemetry_agent_deploy: _execute_telemetry_deploy,
        ChangeType.remote_command: _execute_remote_command,
        ChangeType.microsegmentation_policy: _execute_microsegmentation,
    }

    executor = executor_map.get(change_type)
    if not executor:
        raise ConnectorError(f"No executor registered for change type: {change_type}")

    return await executor(desired_outcome, asset_ids)


async def execute_rollback(
    change_type: "ChangeType | None",
    rollback_plan: dict,
    execution_result: dict,
) -> dict[str, Any]:
    await asyncio.sleep(0.3)

    strategy = rollback_plan.get("strategy", "manual")

    if strategy == "rollback_unavailable":
        return {
            "rolled_back": False,
            "reason": rollback_plan.get("description", "Rollback not available for this change type"),
            "manual_steps_required": True,
        }

    rollback_executors = {
        "restore_previous_record": _rollback_dns,
        "restore_rule_snapshot": _rollback_security_group,
        "cancel_revocation": _rollback_key_rotation,
        "uninstall_agent": _rollback_agent,
        "remove_staged_policy": _rollback_microsegmentation,
    }

    executor = rollback_executors.get(strategy)
    if executor:
        return await executor(rollback_plan, execution_result)

    return {
        "rolled_back": True,
        "strategy": strategy,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def run_preflight_checks(preflight_checks: list[dict]) -> dict[str, Any]:
    await asyncio.sleep(0.2)
    results = []
    all_passed = True

    for check in preflight_checks:
        passed = True
        detail = "Check passed"
        results.append({
            "name": check["name"],
            "passed": passed,
            "detail": detail,
        })
        if not passed:
            all_passed = False

    return {"all_passed": all_passed, "results": results}


async def run_verification_checks(verification_plan: dict, execution_result: dict) -> dict[str, Any]:
    await asyncio.sleep(0.3)
    checks = verification_plan.get("checks", [])
    results = []
    all_passed = True

    for check in checks:
        passed = True
        results.append({
            "name": check["name"],
            "passed": passed,
            "detail": f"Mock verification passed: {check['description']}",
        })

    return {
        "all_passed": all_passed,
        "results": results,
        "success_criteria": verification_plan.get("success_criteria", ""),
    }


def _fake_id(prefix: str = "") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{prefix}{suffix}"


async def _execute_dns_update(desired: dict, asset_ids: list[str]) -> dict:
    previous_value = "203.0.113.10"
    new_value = desired.get("new_value", "203.0.113.20")
    return {
        "action": "dns_update",
        "record_name": desired.get("record_name"),
        "record_type": desired.get("record_type", "A"),
        "previous_value": previous_value,
        "new_value": new_value,
        "ttl": desired.get("ttl", 300),
        "propagation_id": _fake_id("prop-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _execute_snapshot(desired: dict, asset_ids: list[str]) -> dict:
    snapshots = []
    for asset_id in asset_ids:
        snapshots.append({
            "asset_id": asset_id,
            "snapshot_id": _fake_id("snap-"),
            "size_gb": random.randint(20, 500),
            "status": "completed",
        })
    return {
        "action": "snapshot_asset",
        "snapshots": snapshots,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _execute_security_group_update(desired: dict, asset_ids: list[str]) -> dict:
    rules = desired.get("rules", [])
    return {
        "action": "security_group_update",
        "group_id": desired.get("group_id", _fake_id("sg-")),
        "rules_applied": len(rules),
        "rules_added": [r for r in rules if r.get("action") == "add"],
        "rules_removed": [r for r in rules if r.get("action") == "remove"],
        "pre_change_snapshot_id": _fake_id("sgsnap-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _execute_key_rotation(desired: dict, asset_ids: list[str]) -> dict:
    return {
        "action": "key_rotation",
        "new_key_id": _fake_id("key-"),
        "old_key_id": desired.get("old_key_id", _fake_id("key-old-")),
        "key_type": desired.get("key_type", "api_key"),
        "revocation_scheduled_at": datetime.now(timezone.utc).isoformat(),
        "grace_period_hours": desired.get("grace_period_hours", 24),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _execute_telemetry_deploy(desired: dict, asset_ids: list[str]) -> dict:
    hosts = []
    for asset_id in asset_ids:
        hosts.append({
            "asset_id": asset_id,
            "agent_installed": True,
            "agent_version": desired.get("agent_version", "8.12.0"),
            "service_status": "active",
        })
    return {
        "action": "telemetry_agent_deploy",
        "agent_type": desired.get("agent_type", "filebeat"),
        "hosts": hosts,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _execute_remote_command(desired: dict, asset_ids: list[str]) -> dict:
    template_id = desired.get("template_id")
    if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
        raise ConnectorError(
            f"Command template '{template_id}' is not approved",
            {"allowed_templates": list(APPROVED_COMMAND_TEMPLATES.keys())},
        )

    if desired.get("freeform_command"):
        raise ConnectorError("Freeform shell commands are not permitted")

    host_results = []
    for asset_id in asset_ids:
        host_results.append({
            "asset_id": asset_id,
            "exit_code": 0,
            "stdout": f"[mock] Successfully executed template '{template_id}'",
            "stderr": "",
            "duration_ms": random.randint(50, 500),
        })

    return {
        "action": "remote_command",
        "template_id": template_id,
        "parameters": desired.get("parameters", {}),
        "host_results": host_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _execute_microsegmentation(desired: dict, asset_ids: list[str]) -> dict:
    policy_rules = desired.get("policy_rules", [])
    return {
        "action": "microsegmentation_policy",
        "mode": "simulation",
        "staged_policy_id": _fake_id("pol-"),
        "rules_staged": len(policy_rules),
        "simulation_result": "no_violations",
        "critical_flows_checked": len(desired.get("critical_flows", [])),
        "note": "Policy staged in simulation mode only. Full enforcement requires separate approval.",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _rollback_dns(rollback_plan: dict, execution_result: dict) -> dict:
    previous_value = execution_result.get("previous_value", "unknown")
    return {
        "rolled_back": True,
        "action": "dns_restore",
        "restored_value": previous_value,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _rollback_security_group(rollback_plan: dict, execution_result: dict) -> dict:
    return {
        "rolled_back": True,
        "action": "security_group_restore",
        "restored_from_snapshot": execution_result.get("pre_change_snapshot_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _rollback_key_rotation(rollback_plan: dict, execution_result: dict) -> dict:
    return {
        "rolled_back": True,
        "action": "revocation_cancelled",
        "old_key_id": execution_result.get("old_key_id"),
        "old_key_status": "active",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _rollback_agent(rollback_plan: dict, execution_result: dict) -> dict:
    hosts = execution_result.get("hosts", [])
    return {
        "rolled_back": True,
        "action": "agent_uninstalled",
        "hosts_cleaned": [h["asset_id"] for h in hosts],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _rollback_microsegmentation(rollback_plan: dict, execution_result: dict) -> dict:
    return {
        "rolled_back": True,
        "action": "staged_policy_removed",
        "policy_id": execution_result.get("staged_policy_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
