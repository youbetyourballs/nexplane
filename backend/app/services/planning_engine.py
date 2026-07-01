# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass
from typing import Any

from app.models.asset import Asset
from app.models.change_request import ChangeRequest, ChangeType
from app.services.safety_engine import SafetyReviewResult

CHANGE_TYPE_DEFS_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "change_type_definitions"


def _load_change_type_def(change_type: ChangeType) -> dict:
    path = CHANGE_TYPE_DEFS_DIR / f"{change_type.value}.json"
    return json.loads(path.read_text())


def _resolve_parameters(generic_action: str, desired: dict, assets: list[Asset]) -> dict:
    resolvers: dict[str, dict] = {
        "capture_dns_record": {"record_name": desired.get("record_name", ""), "record_type": desired.get("record_type", "A")},
        "validate_dns_target": {"new_value": desired.get("new_value", "")},
        # update_dns_record: no explicit entry — fallback passthrough sends all desired keys including zone_name, dc_hostname
        "wait_dns_propagation": {"ttl": desired.get("ttl", 300)},
        "restore_dns_record": {"record_name": desired.get("record_name", "")},
        "health_check": {"instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", ""), "instance_id": desired.get("instance_id", ""), "vm_name": desired.get("vm_name", desired.get("instance_name", "")), "resource_group": desired.get("resource_group", "")},
        "wait_vm_state": {"vm_name": desired.get("vm_name", desired.get("instance_name", "")), "resource_group": desired.get("resource_group", ""), "target_state": desired.get("target_state", "running")},
        "capture_vm_state": {"vm_name": desired.get("vm_name", desired.get("instance_name", "")), "resource_group": desired.get("resource_group", "")},
        "create_snapshot": {"snapshot_tag": desired.get("snapshot_tag", "nexplane-managed"), "volume_id": desired.get("volume_id", ""), "instance_id": desired.get("instance_id", "")},
        "create_ebs_snapshot": {"volume_id": "", "backup_name": desired.get("snapshot_tag", "nexplane-pre-stop")},
        "verify_snapshot": {},
        "export_security_group": {"group_id": desired.get("group_id", "")},
        "validate_security_rules": {"rules": desired.get("rules", [])},
        "update_security_group": {"group_id": desired.get("group_id", ""), "rules": desired.get("rules", [])},
        "restore_security_group": {},
        "generate_key": {"service": desired.get("service", ""), "key_type": desired.get("key_type", "api_key")},
        "distribute_key": {"consumers": desired.get("consumers", [])},
        "verify_consumers": {"consumers": desired.get("consumers", [])},
        "schedule_revoke": {"grace_period_hours": desired.get("grace_period_hours", 24)},
        "cancel_revoke": {},
        "check_prerequisites": {},
        "download_package": {"agent_type": desired.get("agent_type", ""), "agent_version": desired.get("agent_version", "8.12.0")},
        "install_agent": {"agent_type": desired.get("agent_type", ""), "agent_config": desired.get("agent_config", {})},
        "uninstall_agent": {},
        "start_service": {"agent_type": desired.get("agent_type", "")},
        "validate_template": {"template_id": desired.get("template_id", ""), "parameters": desired.get("parameters", {})},
        "execute_template": {"template_id": desired.get("template_id", ""), "parameters": desired.get("parameters", {})},
        "collect_output": {},
        "analyze_flows": {},
        "generate_diff": {"policy_rules": desired.get("policy_rules", [])},
        "stage_policy": {"policy_rules": desired.get("policy_rules", []), "critical_flows": desired.get("critical_flows", [])},
        "remove_staged_policy": {},
        "validate_staged": {"critical_flows": desired.get("critical_flows", [])},
        "capture_instance_state": {"instance_id": desired.get("instance_id", ""), "instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", "")},
        "stop_instance":          {"instance_id": desired.get("instance_id", ""), "instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", "")},
        "start_instance":         {"instance_id": desired.get("instance_id", ""), "instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", "")},
        "reboot_instance":        {"instance_id": desired.get("instance_id", ""), "instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", "")},
        "terminate_instance":     {"instance_id": desired.get("instance_id", ""), "instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", ""), "confirm_terminate": desired.get("confirm_terminate", False)},
        "wait_instance_state":    {"instance_id": desired.get("instance_id", ""), "instance_name": desired.get("instance_name", ""), "zone": desired.get("zone", ""), "target_state": desired.get("target_state", "running")},
        "resolve_launch_config":  {
            "mode": desired.get("mode", "quick"),
            "name": desired.get("name", "nexplane-instance"),
            "os": desired.get("os", "amazon_linux"),
            "source_instance_id": desired.get("source_instance_id"),
            "ami_id": desired.get("ami_id"),
            "instance_type": desired.get("instance_type"),
            "subnet_id": desired.get("subnet_id"),
            "security_group_ids": desired.get("security_group_ids", []),
            "iam_instance_profile": desired.get("iam_instance_profile", ""),
            "key_name": desired.get("key_name", ""),
        },
        "launch_instance":        {
            "ami_id": desired.get("ami_id", ""),
            "instance_type": desired.get("instance_type", ""),
            "subnet_id": desired.get("subnet_id", ""),
            "security_group_ids": desired.get("security_group_ids", []),
            "name": desired.get("name", "nexplane-instance"),
            "iam_instance_profile": desired.get("iam_instance_profile", ""),
            "key_name": desired.get("key_name", ""),
        },
        "create_key_pair":        {"key_name": desired.get("key_name", "nexplane-key")},
        "delete_key_pair":        {"key_name": desired.get("key_name", "")},
        "run_ssm_command":        {
            "instance_id": desired.get("instance_id", ""),
            "document_name": desired.get("document_name", "AWS-RunShellScript"),
            "parameters": {"commands": [desired.get("command", "echo hello")]},
        },
        "tailscale_join":         {
            "instance_id": desired.get("instance_id", ""),
            "auth_key": desired.get("auth_key", ""),
            "hostname": desired.get("hostname", ""),
        },
        "tailscale_remove":       {"instance_id": desired.get("instance_id", "")},
        "tailscale_generate_auth_key": {
            "expiry_seconds":  desired.get("expiry_seconds", 86400),
            "reusable":        desired.get("reusable", False),
            "ephemeral":       desired.get("ephemeral", False),
            "tags":            desired.get("tags", []),
            "force_generate":  desired.get("force_generate", False),
        },
        "deploy_nexplane_agent":  {
            "instance_id": desired.get("instance_id", ""),
            "nexplane_url": desired.get("nexplane_url", ""),
            "nexplane_secret": desired.get("nexplane_secret", ""),
        },
        "remove_nexplane_agent":  {"instance_id": desired.get("instance_id", "")},
        "terraform_plan_local":    {
            "tf_content": desired.get("tf_content", ""),
            "working_dir": desired.get("working_dir", ""),
        },
        "terraform_apply_local":   {
            "working_dir": desired.get("working_dir", ""),
            "plan_file": desired.get("plan_file", ""),
        },
        "terraform_destroy_local": {
            "working_dir": desired.get("working_dir", ""),
            "cleanup_dir": desired.get("cleanup_dir", True),
        },
        "ansible_check_local":    {
            "instance_id": desired.get("instance_id", ""),
            "playbook_content": desired.get("playbook_content", ""),
            "extra_vars": desired.get("extra_vars", {}),
        },
        "ansible_run_local":      {
            "instance_id": desired.get("instance_id", ""),
            "playbook_content": desired.get("playbook_content", ""),
            "extra_vars": desired.get("extra_vars", {}),
        },
    }
    # If no specific resolver, pass through all desired_outcome keys except meta-fields.
    # This ensures executors added after this resolver was written still receive their parameters.
    _DESIRED_META_KEYS = {"rollback_strategy", "rollback_connector_type"}
    return resolvers.get(
        generic_action,
        {k: v for k, v in desired.items() if k not in _DESIRED_META_KEYS},
    )


def _resolve_step(step_def: dict, step_number: int, desired: dict, assets: list[Asset], catalog) -> dict:
    generic_action = step_def["generic_action"]
    asset_types = [a.asset_type.value for a in assets]

    # Determine if all assets share a single connector
    connector_ids = {a.connector_id for a in assets if a.connector_id is not None}
    locked_connector_type = None
    locked_connector_id = None
    if len(connector_ids) == 1:
        asset_with_connector = next(a for a in assets if a.connector_id is not None)
        # Use the FK value directly — connector relationship may not be loaded in async context
        locked_connector_id = str(asset_with_connector.connector_id)
        if asset_with_connector.connector is not None:
            locked_connector_type = asset_with_connector.connector.connector_type.value

    # Allow callers to hint a specific connector via desired_outcome._locked_connector_type / _locked_connector_id.
    # This is used by smoke tests and programmatic CR creation to bypass asset-based inference.
    if not locked_connector_id and desired.get("_locked_connector_id"):
        locked_connector_id = desired["_locked_connector_id"]
    if not locked_connector_type and desired.get("_locked_connector_type"):
        locked_connector_type = desired["_locked_connector_type"]

    options = catalog.get_options_for_action(generic_action, asset_types=asset_types)
    if not options:
        options = catalog.get_options_for_action(generic_action)

    # Filter to locked connector type when determined
    if locked_connector_type and options:
        locked_options = [o for o in options if o.connector_type == locked_connector_type]
        if locked_options:
            options = locked_options

    if not options:
        return {
            "step_number": step_number,
            "name": generic_action.replace("_", " ").title(),
            "description": f"No connector found for action '{generic_action}'",
            "generic_action": generic_action,
            "connector_type": "unknown",
            "action_id": generic_action,
            "execution_tier": 99,
            "connector_options": [],
            "parameters": {**_resolve_parameters(generic_action, desired, assets), **step_def.get("param_overrides", {})},
            "rollback_action": None,
            "rollback_connector_type": None,
            "connector_id": locked_connector_id,
            "estimated_duration_seconds": 30,
            "blast_radius_hint": None,
        }

    best = options[0]
    action_def = best.action_def
    rollback_action = action_def.get("rollback_action")
    return {
        "step_number": step_number,
        "name": action_def.get("display_name", generic_action.replace("_", " ").title()),
        "description": action_def.get("description", ""),
        "generic_action": generic_action,
        "connector_type": best.connector_type,
        "action_id": best.action_id,
        "execution_tier": best.execution_tier,
        "connector_options": [
            {"connector_type": o.connector_type, "action_id": o.action_id, "execution_tier": o.execution_tier}
            for o in options
        ],
        "parameters": {**_resolve_parameters(generic_action, desired, assets), **step_def.get("param_overrides", {})},
        "rollback_action": rollback_action,
        "rollback_connector_type": best.connector_type if rollback_action else None,
        "connector_id": locked_connector_id,
        "estimated_duration_seconds": action_def.get("estimated_duration_seconds", 30),
        "blast_radius_hint": action_def.get("blast_radius_hint"),
    }


@dataclass
class ChangePlanData:
    generated_steps: list[dict]
    preflight_checks: list[dict]
    blast_radius: dict
    rollback_plan: dict
    verification_plan: dict


def generate_plan(
    change_request: ChangeRequest,
    assets: list[Asset],
    safety_result: SafetyReviewResult,
    catalog=None,
) -> ChangePlanData:
    from app.connectors.catalog_service import get_catalog_service
    if catalog is None:
        catalog = get_catalog_service()

    ct = change_request.change_type
    desired = change_request.desired_outcome or {}

    if ct == ChangeType.catalog_action:
        connector_type = desired.get("connector_type", "")
        action_id = desired.get("action_id", "")
        params = desired.get("params", {}) or {}
        # Validate the action exists in the (core+commercial) catalog.
        catalog.get_action_def(connector_type, action_id)  # raises KeyError if unknown
        step = {
            "step_number": 1,
            "connector_type": connector_type,
            "action_id": action_id,
            "parameters": params,
            "purpose": "execute",
            "options": [{"connector_type": connector_type, "action_id": action_id, "execution_tier": 0}],
            "rollback_connector_type": None,
        }
        return ChangePlanData(
            generated_steps=[step],
            preflight_checks=[],
            blast_radius=_calculate_blast_radius(change_request, assets, safety_result),
            rollback_plan={},
            verification_plan={},
        )

    change_def = _load_change_type_def(ct)
    steps = [
        _resolve_step(step_def, i + 1, desired, assets, catalog)
        for i, step_def in enumerate(change_def["steps"])
    ]
    return ChangePlanData(
        generated_steps=steps,
        preflight_checks=_generate_preflight_checks(change_def),
        blast_radius=_calculate_blast_radius(change_request, assets, safety_result),
        rollback_plan=_generate_rollback_plan(ct, desired),
        verification_plan=_generate_verification_plan(change_def),
    )


def _generate_preflight_checks(change_def: dict) -> list[dict]:
    return [
        {"name": name, "description": name.replace("_", " ").title(), "check_type": "standard", "expected_result": "pass"}
        for name in change_def.get("preflight_checks", [])
    ]


def _calculate_blast_radius(cr: ChangeRequest, assets: list[Asset], safety: SafetyReviewResult) -> dict:
    envs = list({a.environment.value for a in assets})
    return {
        "affected_assets": [{"id": str(a.id), "name": a.name, "type": a.asset_type.value, "env": a.environment.value, "criticality": a.criticality.value} for a in assets],
        "affected_environments": envs,
        "estimated_impact": f"Risk level: {safety.risk_level.value}. Score: {safety.risk_score}.",
        "affected_services": [],
        "recovery_time_estimate": "5-30 minutes",
        "rollback_available": safety.risk_level.value not in ["critical"],
    }


def _generate_rollback_plan(ct: ChangeType, desired: dict) -> dict:
    strategies = {
        ChangeType.dns_update: ("restore_previous_record", True),
        ChangeType.snapshot_asset: ("rollback_unavailable", False),
        ChangeType.security_group_update: ("restore_rule_snapshot", True),
        ChangeType.key_rotation: ("cancel_revocation", True),
        ChangeType.telemetry_agent_deploy: ("uninstall_agent", True),
        ChangeType.remote_command: ("manual", False),
        ChangeType.microsegmentation_policy: ("remove_staged_policy", True),
        ChangeType.ec2_stop:       ("start_instance",     True),
        ChangeType.ec2_start:      ("stop_instance",      True),
        ChangeType.ec2_reboot:     ("none",               False),
        ChangeType.ec2_stop_start: ("stop_if_running",    True),
        ChangeType.ec2_launch:     ("terminate_instance", True),
        ChangeType.ec2_terminate:  ("manual",             False),
    }
    strategy, automatic = strategies.get(ct, ("manual", False))
    return {"strategy": strategy, "description": f"Rollback via {strategy}", "estimated_duration_seconds": 30, "automatic": automatic}


def _generate_verification_plan(change_def: dict) -> dict:
    methods = change_def.get("verification_methods", ["api_check"])
    return {
        "checks": [{"name": m, "description": f"Verify via {m}", "method": m} for m in methods],
        "success_criteria": "All verification checks must pass",
    }
