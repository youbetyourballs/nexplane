from dataclasses import dataclass, field
from typing import Any
import uuid

from app.models.asset import Asset
from app.models.change_request import ChangeRequest, ChangeType
from app.services.safety_engine import score_change_request, SafetyReviewResult


@dataclass
class GeneratedStep:
    step_number: int
    name: str
    description: str
    connector_action: str
    parameters: dict[str, Any]
    rollback_action: str | None = None
    rollback_parameters: dict[str, Any] = field(default_factory=dict)
    estimated_duration_seconds: int = 30


@dataclass
class PreflightCheck:
    name: str
    description: str
    check_type: str
    expected_result: str


@dataclass
class BlastRadius:
    affected_assets: list[dict]
    affected_environments: list[str]
    estimated_impact: str
    affected_services: list[str]
    recovery_time_estimate: str
    rollback_available: bool


@dataclass
class ChangePlanData:
    generated_steps: list[dict]
    preflight_checks: list[dict]
    blast_radius: dict
    rollback_plan: dict
    verification_plan: dict


def generate_plan(change_request: ChangeRequest, assets: list[Asset], safety_result: SafetyReviewResult) -> ChangePlanData:
    ct = change_request.change_type
    desired = change_request.desired_outcome or {}

    steps = _generate_steps(ct, desired, assets)
    preflight = _generate_preflight_checks(ct, desired, assets)
    blast_radius = _calculate_blast_radius(change_request, assets, safety_result)
    rollback_plan = _generate_rollback_plan(ct, desired, assets)
    verification_plan = _generate_verification_plan(ct, desired, assets)

    return ChangePlanData(
        generated_steps=[_step_to_dict(s) for s in steps],
        preflight_checks=[_preflight_to_dict(p) for p in preflight],
        blast_radius=_blast_to_dict(blast_radius),
        rollback_plan=rollback_plan,
        verification_plan=verification_plan,
    )


def _step_to_dict(s: GeneratedStep) -> dict:
    return {
        "step_number": s.step_number,
        "name": s.name,
        "description": s.description,
        "connector_action": s.connector_action,
        "parameters": s.parameters,
        "rollback_action": s.rollback_action,
        "rollback_parameters": s.rollback_parameters,
        "estimated_duration_seconds": s.estimated_duration_seconds,
    }


def _preflight_to_dict(p: PreflightCheck) -> dict:
    return {
        "name": p.name,
        "description": p.description,
        "check_type": p.check_type,
        "expected_result": p.expected_result,
    }


def _blast_to_dict(b: BlastRadius) -> dict:
    return {
        "affected_assets": b.affected_assets,
        "affected_environments": b.affected_environments,
        "estimated_impact": b.estimated_impact,
        "affected_services": b.affected_services,
        "recovery_time_estimate": b.recovery_time_estimate,
        "rollback_available": b.rollback_available,
    }


def _generate_steps(change_type: ChangeType, desired: dict, assets: list[Asset]) -> list[GeneratedStep]:
    if change_type == ChangeType.dns_update:
        return [
            GeneratedStep(1, "Capture Current DNS Record", "Read existing DNS record value before modification",
                          "dns.get_record", {"record_name": desired.get("record_name"), "record_type": desired.get("record_type", "A")},
                          estimated_duration_seconds=5),
            GeneratedStep(2, "Validate New Record Value", "Verify new IP/value is reachable and valid",
                          "dns.validate_target", {"target": desired.get("new_value")},
                          estimated_duration_seconds=10),
            GeneratedStep(3, "Apply DNS Update", "Update the DNS record to the new value",
                          "dns.update_record",
                          {"record_name": desired.get("record_name"), "new_value": desired.get("new_value"), "ttl": desired.get("ttl", 300)},
                          rollback_action="dns.update_record",
                          rollback_parameters={"record_name": desired.get("record_name"), "restore_previous": True},
                          estimated_duration_seconds=15),
            GeneratedStep(4, "Propagation Wait", "Wait for DNS propagation (TTL-aware)",
                          "dns.wait_propagation", {"ttl_seconds": desired.get("ttl", 300)},
                          estimated_duration_seconds=60),
        ]

    elif change_type == ChangeType.snapshot_asset:
        return [
            GeneratedStep(1, "Pre-snapshot Health Check", "Verify asset is healthy before snapshotting",
                          "cloud.health_check", {"asset_ids": [str(a.id) for a in assets]},
                          estimated_duration_seconds=10),
            GeneratedStep(2, "Create Snapshot", "Initiate asset snapshot",
                          "cloud.create_snapshot",
                          {"asset_ids": [str(a.id) for a in assets], "snapshot_tag": desired.get("snapshot_tag", "nexplane-managed")},
                          estimated_duration_seconds=120),
            GeneratedStep(3, "Verify Snapshot Integrity", "Confirm snapshot completed successfully",
                          "cloud.verify_snapshot", {"verify_checksum": True},
                          estimated_duration_seconds=30),
        ]

    elif change_type == ChangeType.security_group_update:
        return [
            GeneratedStep(1, "Export Current Rules", "Capture current security group rules for rollback",
                          "cloud.export_security_group", {"group_id": desired.get("group_id")},
                          estimated_duration_seconds=5),
            GeneratedStep(2, "Validate Rule Syntax", "Validate proposed rules before applying",
                          "cloud.validate_security_rules", {"rules": desired.get("rules", [])},
                          estimated_duration_seconds=10),
            GeneratedStep(3, "Apply Security Group Update", "Apply the new security group rules",
                          "cloud.update_security_group",
                          {"group_id": desired.get("group_id"), "rules": desired.get("rules", [])},
                          rollback_action="cloud.restore_security_group",
                          rollback_parameters={"group_id": desired.get("group_id"), "restore_snapshot": True},
                          estimated_duration_seconds=20),
        ]

    elif change_type == ChangeType.key_rotation:
        return [
            GeneratedStep(1, "Generate New Key", "Create new API key / credential",
                          "identity.generate_key", {"key_type": desired.get("key_type", "api_key"), "service": desired.get("service")},
                          estimated_duration_seconds=5),
            GeneratedStep(2, "Distribute New Key", "Update all registered consumers with new key",
                          "identity.distribute_key", {"consumers": desired.get("consumers", [])},
                          estimated_duration_seconds=30),
            GeneratedStep(3, "Verify Consumer Health", "Confirm all consumers are operating with new key",
                          "identity.verify_consumers", {"consumers": desired.get("consumers", [])},
                          estimated_duration_seconds=60),
            GeneratedStep(4, "Mark Old Key for Revocation", "Flag old key as pending-revoke (24h grace period)",
                          "identity.schedule_revoke",
                          {"grace_period_hours": desired.get("grace_period_hours", 24)},
                          rollback_action="identity.cancel_revoke",
                          rollback_parameters={"restore_old_key": True},
                          estimated_duration_seconds=5),
        ]

    elif change_type == ChangeType.telemetry_agent_deploy:
        return [
            GeneratedStep(1, "Check Agent Prerequisites", "Verify target hosts meet agent requirements",
                          "ssh.check_prerequisites",
                          {"hosts": [str(a.id) for a in assets], "agent": desired.get("agent_type", "filebeat")},
                          estimated_duration_seconds=15),
            GeneratedStep(2, "Download Agent Package", "Fetch agent installer to staging location",
                          "ssh.download_package",
                          {"package": desired.get("agent_type", "filebeat"), "version": desired.get("agent_version", "latest")},
                          estimated_duration_seconds=30),
            GeneratedStep(3, "Install Agent", "Run agent installer on target hosts",
                          "ssh.install_agent",
                          {"hosts": [str(a.id) for a in assets], "config": desired.get("agent_config", {})},
                          rollback_action="ssh.uninstall_agent",
                          rollback_parameters={"hosts": [str(a.id) for a in assets]},
                          estimated_duration_seconds=60),
            GeneratedStep(4, "Start and Enable Service", "Start agent service and enable at boot",
                          "ssh.start_service",
                          {"service_name": desired.get("agent_type", "filebeat")},
                          estimated_duration_seconds=10),
        ]

    elif change_type == ChangeType.remote_command:
        template_id = desired.get("template_id")
        params = desired.get("parameters", {})
        return [
            GeneratedStep(1, "Validate Command Template", "Verify command template and parameters are approved",
                          "ssh.validate_template", {"template_id": template_id, "parameters": params},
                          estimated_duration_seconds=2),
            GeneratedStep(2, "Execute Approved Command", f"Run approved command template: {template_id}",
                          "ssh.execute_template",
                          {"template_id": template_id, "parameters": params, "hosts": [str(a.id) for a in assets]},
                          estimated_duration_seconds=30),
            GeneratedStep(3, "Collect Execution Output", "Gather stdout/stderr from all hosts",
                          "ssh.collect_output", {},
                          estimated_duration_seconds=5),
        ]

    elif change_type == ChangeType.microsegmentation_policy:
        return [
            GeneratedStep(1, "Analyze Current Flows", "Map existing allowed traffic flows",
                          "firewall.analyze_flows", {"assets": [str(a.id) for a in assets]},
                          estimated_duration_seconds=30),
            GeneratedStep(2, "Generate Policy Diff", "Calculate delta between current and proposed policy",
                          "firewall.generate_diff", {"proposed_policy": desired.get("policy_rules", [])},
                          estimated_duration_seconds=15),
            GeneratedStep(3, "Stage Policy (Simulation Mode)", "Apply policy in simulation mode — no traffic blocked",
                          "firewall.stage_policy",
                          {"policy_rules": desired.get("policy_rules", []), "mode": "simulation"},
                          rollback_action="firewall.remove_staged_policy",
                          rollback_parameters={},
                          estimated_duration_seconds=20),
            GeneratedStep(4, "Validate Staged Policy", "Confirm no critical flows blocked by simulation",
                          "firewall.validate_staged", {"critical_flows": desired.get("critical_flows", [])},
                          estimated_duration_seconds=30),
        ]

    return []


def _generate_preflight_checks(change_type: ChangeType, desired: dict, assets: list[Asset]) -> list[PreflightCheck]:
    checks = [
        PreflightCheck("connector_reachable", "Verify connector endpoint is reachable", "connectivity", "HTTP 200 from connector health endpoint"),
        PreflightCheck("asset_exists", "Confirm all target assets exist in inventory", "inventory", "All asset IDs resolve"),
        PreflightCheck("no_concurrent_changes", "No other change request is executing on target assets", "concurrency", "Zero active execution runs on target assets"),
    ]

    if change_type == ChangeType.dns_update:
        checks.append(PreflightCheck("dns_record_exists", f"Verify DNS record '{desired.get('record_name')}' exists", "dns", "Record found in zone"))
        checks.append(PreflightCheck("new_value_reachable", "Confirm new DNS target is reachable", "connectivity", "Target IP responds to ICMP or TCP probe"))

    elif change_type == ChangeType.snapshot_asset:
        checks.append(PreflightCheck("sufficient_storage", "Verify snapshot storage quota available", "capacity", "Available storage > 110% of asset size"))

    elif change_type == ChangeType.security_group_update:
        checks.append(PreflightCheck("group_exists", "Verify security group exists", "cloud", "Group ID resolves in cloud API"))
        checks.append(PreflightCheck("no_overlapping_rules", "Check for conflicting rules", "validation", "No DENY rules conflict with proposed rules"))

    elif change_type == ChangeType.key_rotation:
        checks.append(PreflightCheck("old_key_active", "Confirm old key is currently active", "identity", "Key status is ACTIVE"))

    elif change_type == ChangeType.remote_command:
        checks.append(PreflightCheck("template_approved", f"Template '{desired.get('template_id')}' is in approved list", "security", "Template found in approved command templates"))
        checks.append(PreflightCheck("hosts_reachable", "All target hosts are SSH-reachable", "connectivity", "SSH port open on all hosts"))

    elif change_type == ChangeType.microsegmentation_policy:
        checks.append(PreflightCheck("policy_syntax_valid", "Policy rules pass syntax validation", "validation", "Zero syntax errors"))
        checks.append(PreflightCheck("critical_flows_mapped", "All critical traffic flows identified", "discovery", "Flow map complete"))

    return checks


def _calculate_blast_radius(change_request: ChangeRequest, assets: list[Asset], safety: SafetyReviewResult) -> BlastRadius:
    environments = list({a.environment.value for a in assets})
    asset_dicts = [{"id": str(a.id), "name": a.name, "type": a.asset_type.value, "env": a.environment.value, "criticality": a.criticality.value} for a in assets]

    impact_map = {
        ChangeType.dns_update: ("DNS resolution failure for affected zone during propagation window", ["DNS consumers", "Web applications", "API clients"], "5-60 minutes (TTL dependent)"),
        ChangeType.snapshot_asset: ("Potential I/O performance degradation during snapshot", ["Storage I/O"], "5-30 minutes"),
        ChangeType.security_group_update: ("Network connectivity changes per rule delta", ["All services using affected security group"], "Immediate"),
        ChangeType.key_rotation: ("Service disruption if consumers not updated before old key revoked", ["API consumers", "CI/CD pipelines", "Scheduled jobs"], "Grace period: 24h"),
        ChangeType.telemetry_agent_deploy: ("Temporary CPU/memory overhead during install", ["Host performance"], "30-90 seconds per host"),
        ChangeType.remote_command: ("Depends on command template", ["Target hosts"], "Seconds to minutes"),
        ChangeType.microsegmentation_policy: ("Simulation mode only — no live traffic impact", [], "None (staged)"),
    }

    impact_info = impact_map.get(change_request.change_type, ("Unknown impact", [], "Unknown"))

    return BlastRadius(
        affected_assets=asset_dicts,
        affected_environments=environments,
        estimated_impact=impact_info[0],
        affected_services=impact_info[1],
        recovery_time_estimate=impact_info[2],
        rollback_available=not any(f.name == "no_rollback_strategy" for f in safety.risk_factors),
    )


def _generate_rollback_plan(change_type: ChangeType, desired: dict, assets: list[Asset]) -> dict:
    rollback_map = {
        ChangeType.dns_update: {
            "strategy": "restore_previous_record",
            "description": "Restore DNS record to captured pre-change value",
            "estimated_duration_seconds": 30,
            "automatic": True,
        },
        ChangeType.snapshot_asset: {
            "strategy": "rollback_unavailable",
            "description": "Snapshots are additive; no rollback needed. Delete snapshot manually if required.",
            "estimated_duration_seconds": 0,
            "automatic": False,
        },
        ChangeType.security_group_update: {
            "strategy": "restore_rule_snapshot",
            "description": "Restore security group to exported pre-change rule set",
            "estimated_duration_seconds": 20,
            "automatic": True,
        },
        ChangeType.key_rotation: {
            "strategy": "cancel_revocation",
            "description": "Cancel pending old key revocation; old key remains active",
            "estimated_duration_seconds": 10,
            "automatic": True,
        },
        ChangeType.telemetry_agent_deploy: {
            "strategy": "uninstall_agent",
            "description": "Run agent uninstaller on all target hosts and remove config",
            "estimated_duration_seconds": 60,
            "automatic": True,
        },
        ChangeType.remote_command: {
            "strategy": "rollback_unavailable",
            "description": "Remote commands cannot be automatically undone. Manual remediation required.",
            "estimated_duration_seconds": 0,
            "automatic": False,
        },
        ChangeType.microsegmentation_policy: {
            "strategy": "remove_staged_policy",
            "description": "Remove staged simulation policy from firewall",
            "estimated_duration_seconds": 15,
            "automatic": True,
        },
    }
    return rollback_map.get(change_type, {"strategy": "manual", "description": "Manual rollback required", "automatic": False})


def _generate_verification_plan(change_type: ChangeType, desired: dict, assets: list[Asset]) -> dict:
    verifications_map = {
        ChangeType.dns_update: {
            "checks": [
                {"name": "dns_resolves_new_value", "description": "Confirm DNS query returns new value", "method": "dns_lookup"},
                {"name": "application_health", "description": "HTTP health check on affected endpoints", "method": "http_probe"},
            ],
            "success_criteria": "DNS resolves to new value AND health check returns 2xx",
        },
        ChangeType.snapshot_asset: {
            "checks": [
                {"name": "snapshot_exists", "description": "Snapshot ID is present and status is complete", "method": "api_check"},
                {"name": "asset_healthy", "description": "Asset is operating normally post-snapshot", "method": "health_check"},
            ],
            "success_criteria": "Snapshot status = complete AND asset health = healthy",
        },
        ChangeType.security_group_update: {
            "checks": [
                {"name": "rules_applied", "description": "Security group reflects new rules", "method": "api_check"},
                {"name": "connectivity_test", "description": "Allowed flows succeed; denied flows fail", "method": "connectivity_probe"},
            ],
            "success_criteria": "All rule tests pass",
        },
        ChangeType.key_rotation: {
            "checks": [
                {"name": "new_key_auth", "description": "New key authenticates successfully", "method": "auth_test"},
                {"name": "consumer_health", "description": "All consumers healthy with new key", "method": "health_check"},
            ],
            "success_criteria": "New key auth passes AND all consumers healthy",
        },
        ChangeType.telemetry_agent_deploy: {
            "checks": [
                {"name": "agent_running", "description": "Agent service is active on all hosts", "method": "service_check"},
                {"name": "data_flowing", "description": "Agent is sending telemetry data", "method": "data_probe"},
            ],
            "success_criteria": "Agent running AND first data packet received",
        },
        ChangeType.remote_command: {
            "checks": [
                {"name": "exit_code_zero", "description": "All hosts returned exit code 0", "method": "output_check"},
                {"name": "host_healthy", "description": "Target hosts remain reachable post-command", "method": "health_check"},
            ],
            "success_criteria": "All hosts exit code 0 AND hosts reachable",
        },
        ChangeType.microsegmentation_policy: {
            "checks": [
                {"name": "policy_staged", "description": "Policy is staged in simulation mode", "method": "api_check"},
                {"name": "no_critical_flows_blocked", "description": "No critical flows blocked in simulation", "method": "flow_test"},
            ],
            "success_criteria": "Policy staged AND no critical flow violations",
        },
    }
    return verifications_map.get(change_type, {"checks": [], "success_criteria": "No automated verification"})
