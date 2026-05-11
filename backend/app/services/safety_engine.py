from dataclasses import dataclass, field
from typing import Any

from app.models.asset import Asset, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel


APPROVED_COMMAND_TEMPLATES = {
    "restart_service": {
        "description": "Restart a named system service",
        "parameters": ["service_name"],
        "allowed_services": ["nginx", "apache2", "sshd", "auditd", "filebeat", "telegraf"],
    },
    "check_disk_usage": {
        "description": "Report disk usage for a mount point",
        "parameters": ["mount_point"],
    },
    "rotate_log": {
        "description": "Force log rotation for a named log",
        "parameters": ["log_name"],
    },
    "flush_dns_cache": {
        "description": "Flush the system DNS resolver cache",
        "parameters": [],
    },
    "collect_support_bundle": {
        "description": "Collect a diagnostic support bundle",
        "parameters": ["output_path"],
    },
}


# Change types with implicit rollbacks (well-known reverse operations)
# Includes all change types whose CT definition has a rollback_action specified
_IMPLICIT_ROLLBACK_TYPES = {
    ChangeType.ec2_stop, ChangeType.ec2_start, ChangeType.ec2_reboot,
    ChangeType.ec2_stop_start, ChangeType.ec2_launch, ChangeType.ssm_command,
    ChangeType.key_pair_create,
    ChangeType.tailscale_join, ChangeType.tailscale_remove, ChangeType.deploy_nexplane_agent,
    ChangeType.terraform_local_apply,
    ChangeType.ansible_local_playbook,
    # Change types with CT-defined rollback_action (rollback is built into the change type):
    ChangeType.snapshot_asset,
    ChangeType.security_group_update,
    ChangeType.iam_user_create,
    ChangeType.s3_bucket_create,
    ChangeType.s3_bucket_delete,
    ChangeType.s3_lifecycle_configure,
    ChangeType.route53_zone_create,
    ChangeType.route53_record_upsert,
    ChangeType.route53_record_delete,
    ChangeType.cloudwatch_alarm_create,
    ChangeType.cloudwatch_alarm_delete,
    ChangeType.rds_instance_create,
    ChangeType.rds_instance_delete,
    ChangeType.rds_snapshot_create,
    ChangeType.promote_db_replica,
    ChangeType.verify_backup,
    ChangeType.block_s3_public_access,
    ChangeType.restore_s3_public_access,
    ChangeType.capture_instance_state,
    # New change types added for Plans 2-4 (string-based, not in enum yet):
    "attach_iam_policy", "detach_iam_policy", "disable_iam_user", "enable_iam_user",
    "rotate_iam_key", "put_bucket_policy", "tag_resource", "remove_nexplane_agent",
    "dr_dns_failover_route53", "rds_replica_create",
    "gcp_firewall_create", "gcp_firewall_delete", "gcp_block_public_bucket_access",
    "gcp_disable_service_account", "gcp_rotate_service_account_key",
    "azure_update_nsg_rule", "azure_restore_nsg_rule",
    "azure_disable_public_blob_access", "azure_enable_public_blob_access",
    "azure_rotate_storage_key",
    "gce_instance_create", "gce_instance_delete", "gce_stop", "gce_start", "gce_instance_reboot",
    "gce_disk_snapshot", "azure_vm_create", "azure_vm_delete", "azure_vm_stop",
    "azure_vm_start", "azure_vm_reboot", "azure_vm_snapshot",
    "azure_vnet_create", "azure_vnet_delete",
    "azure_role_assignment_create", "azure_role_assignment_delete",
    "azure_blob_container_create", "azure_blob_container_delete",
    "azure_dns_zone_create", "azure_dns_zone_delete",
    "azure_dns_record_create", "azure_dns_record_delete",
    "azure_managed_identity_create", "azure_managed_identity_delete",
    "azure_metric_alert_create", "azure_metric_alert_delete",
    "azure_sql_server_create", "azure_sql_server_delete",
    "azure_sql_database_create", "azure_sql_database_delete",
    "azure_storage_account_create", "azure_storage_account_delete",
    "oci_instance_create", "oci_instance_stop", "oci_instance_start",
    "oci_instance_reboot", "oci_instance_delete", "oci_block_volume_snapshot",
    "oci_vcn_create", "oci_subnet_create",
    "oci_bucket_create", "oci_bucket_delete",
    "oci_bucket_lifecycle_set", "oci_bucket_block_public",
    "oci_block_volume_create", "oci_block_volume_attach",
    "oci_block_volume_detach", "oci_block_volume_delete",
    "oci_block_volume_backup",
    ChangeType.agent_linux_patch, ChangeType.agent_ossecurity,
    ChangeType.agent_linuxauth, ChangeType.agent_crossplatform,
    ChangeType.agent_compliance, ChangeType.agent_forensics,
    ChangeType.agent_fleet, ChangeType.agent_backup,
    ChangeType.agent_reboot, ChangeType.agent_credrotation,
    ChangeType.agent_iac, ChangeType.agent_linuxupgrade,
    ChangeType.agent_win_patch, ChangeType.agent_winharden,
    ChangeType.alb_create, ChangeType.alb_delete,
    ChangeType.target_group_create, ChangeType.target_group_delete,
    ChangeType.listener_create, ChangeType.listener_modify, ChangeType.listener_delete,
    ChangeType.register_targets, ChangeType.deregister_targets,
    ChangeType.agent_appdiscovery,
    ChangeType.agent_containerize_build,
    ChangeType.k8s_workload_deploy,
    ChangeType.agent_containerize_retire,
    ChangeType.agent_containerize_auto,
}


@dataclass
class RiskFactor:
    name: str
    description: str
    score: int


@dataclass
class SafetyReviewResult:
    risk_level: RiskLevel
    risk_score: int
    risk_factors: list[RiskFactor] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    approved_template: dict[str, Any] | None = None

    @property
    def is_blocked(self) -> bool:
        return len(self.blocking_issues) > 0


def score_change_request(change_request: ChangeRequest, assets: list[Asset]) -> SafetyReviewResult:
    score = 0
    risk_factors: list[RiskFactor] = []
    blocking_issues: list[str] = []
    warnings: list[str] = []

    prod_assets = [a for a in assets if a.environment == Environment.prod]
    critical_assets = [a for a in assets if a.criticality == Criticality.critical]
    high_assets = [a for a in assets if a.criticality == Criticality.high]

    if prod_assets:
        factor_score = 30 * len(prod_assets)
        score += factor_score
        risk_factors.append(RiskFactor(
            name="production_environment",
            description=f"{len(prod_assets)} production asset(s) targeted",
            score=factor_score,
        ))

    if critical_assets:
        factor_score = 35 * len(critical_assets)
        score += factor_score
        risk_factors.append(RiskFactor(
            name="critical_asset",
            description=f"{len(critical_assets)} critical asset(s) targeted",
            score=factor_score,
        ))
    elif high_assets:
        factor_score = 20 * len(high_assets)
        score += factor_score
        risk_factors.append(RiskFactor(
            name="high_criticality_asset",
            description=f"{len(high_assets)} high-criticality asset(s) targeted",
            score=factor_score,
        ))

    if change_request.change_type == ChangeType.remote_command:
        desired = change_request.desired_outcome or {}
        template_id = desired.get("template_id")
        if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
            blocking_issues.append(
                "Remote command requests must use an approved command template. "
                f"Available templates: {', '.join(APPROVED_COMMAND_TEMPLATES.keys())}"
            )
        elif desired.get("freeform_command"):
            blocking_issues.append("Freeform shell commands are not permitted. Use a parameterized template.")
        else:
            score += 25
            risk_factors.append(RiskFactor(
                name="remote_command_execution",
                description=f"Remote command using template '{template_id}'",
                score=25,
            ))

    elif change_request.change_type == ChangeType.ssm_command:
        # SSM uses AWS IAM trust — freeform commands are permitted but scored as medium risk
        score += 20
        risk_factors.append(RiskFactor(
            name="ssm_command_execution",
            description="SSM command execution via AWS Systems Manager (IAM-gated)",
            score=20,
        ))

    if change_request.change_type == ChangeType.microsegmentation_policy:
        score += 20
        risk_factors.append(RiskFactor(
            name="microsegmentation_policy",
            description="Policy change affects network segmentation",
            score=20,
        ))
        if len(change_request.target_asset_ids) > 10:
            score += 25
            risk_factors.append(RiskFactor(
                name="large_blast_radius",
                description=f"{len(change_request.target_asset_ids)} assets targeted (>10)",
                score=25,
            ))

    if change_request.change_type == ChangeType.security_group_update and prod_assets:
        warnings.append("Security group changes in production require careful verification of inbound/outbound rules.")

    if change_request.change_type == ChangeType.key_rotation:
        warnings.append("Ensure all consumers of the rotated key are updated before revoking the old key.")

    desired = change_request.desired_outcome or {}
    rollback_strategy = desired.get("rollback_strategy")
    if not rollback_strategy and change_request.change_type not in _IMPLICIT_ROLLBACK_TYPES:
        score += 30
        risk_factors.append(RiskFactor(
            name="no_rollback_strategy",
            description="No rollback strategy specified in desired outcome",
            score=30,
        ))
        if prod_assets or critical_assets:
            blocking_issues.append(
                "A rollback strategy is required for changes targeting production or critical assets."
            )

    dev_staging_only = all(a.environment in (Environment.dev, Environment.staging) for a in assets)
    low_med_crit = all(a.criticality in (Criticality.low, Criticality.medium) for a in assets)
    if dev_staging_only and low_med_crit and not assets:
        score = max(score - 20, 0)

    if score >= 90:
        risk_level = RiskLevel.critical
    elif score >= 60:
        risk_level = RiskLevel.high
    elif score >= 30:
        risk_level = RiskLevel.medium
    else:
        risk_level = RiskLevel.low

    return SafetyReviewResult(
        risk_level=risk_level,
        risk_score=score,
        risk_factors=risk_factors,
        blocking_issues=blocking_issues,
        warnings=warnings,
    )


def check_approval_requirements(risk_level: RiskLevel, approvals: list) -> dict:
    approved_decisions = [a for a in approvals if a.decision == "approved"]
    approver_roles = {a.approver.role for a in approved_decisions}

    requirements = {
        RiskLevel.low: {
            "min_approvals": 1,
            "required_roles": ["security_operator", "admin"],
            "description": "Requires approval from security operator or admin",
        },
        RiskLevel.medium: {
            "min_approvals": 1,
            "required_roles": ["approver", "admin"],
            "description": "Requires approver or admin",
        },
        RiskLevel.high: {
            "min_approvals": 2,
            "required_roles": ["approver", "admin"],
            "description": "Requires approver AND admin",
            "must_include": ["approver", "admin"],
        },
        RiskLevel.critical: {
            "min_approvals": 2,
            "required_roles": ["approver", "admin"],
            "description": "Requires two approvals; does not auto-execute",
            "must_include": ["approver", "admin"],
            "no_auto_execute": True,
        },
    }

    req = requirements[risk_level]
    met = len(approved_decisions) >= req["min_approvals"]

    if met and "must_include" not in req:
        required_roles = req.get("required_roles", [])
        if required_roles and not any(r in approver_roles for r in required_roles):
            met = False

    if "must_include" in req:
        for role in req["must_include"]:
            if role not in approver_roles:
                met = False
                break

    return {
        "satisfied": met,
        "approvals_received": len(approved_decisions),
        "approvals_required": req["min_approvals"],
        "description": req["description"],
        "no_auto_execute": req.get("no_auto_execute", False),
    }


def get_approved_command_templates() -> dict:
    return APPROVED_COMMAND_TEMPLATES


def adjust_for_execution_plan(
    safety_result: SafetyReviewResult,
    generated_steps: list[dict],
) -> SafetyReviewResult:
    """Adjusts risk score upward if any step uses execution tier 5 (raw remote command)."""
    from dataclasses import replace
    max_tier = max((s.get("execution_tier", 1) for s in generated_steps), default=1)
    if max_tier < 5:
        return safety_result

    new_factors = list(safety_result.risk_factors) + [
        RiskFactor(
            name="raw_remote_command_tier",
            description="One or more steps use raw remote command execution (tier 5)",
            score=25,
        )
    ]
    new_score = safety_result.risk_score + 25
    new_level = _score_to_level(new_score)
    return replace(safety_result, risk_score=new_score, risk_level=new_level, risk_factors=new_factors)


def _score_to_level(score: int) -> RiskLevel:
    if score >= 90:
        return RiskLevel.critical
    if score >= 60:
        return RiskLevel.high
    if score >= 30:
        return RiskLevel.medium
    return RiskLevel.low
