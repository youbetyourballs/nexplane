# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""CR Manifest Builder — infers planning metadata from change type JSON definitions."""
from __future__ import annotations

import json
import pathlib
from typing import Optional

_DEFINITIONS_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "change_type_definitions"

# Module-level cache — built once at startup via build_manifest(), reused by get_manifest()
_MANIFEST: list[dict] | None = None


# ---------------------------------------------------------------------------
# Domain inference
# ---------------------------------------------------------------------------

_DOMAIN_PREFIXES: list[tuple[list[str], str]] = [
    (["iam_", "attach_iam", "detach_iam", "disable_iam", "enable_iam"], "aws_identity"),
    (["ec2_", "rds_", "s3_", "route53_", "alb_", "elb_", "eks_", "ecr_",
      "cloudwatch_", "cloudfront_", "elasticache_", "lambda_", "sqs_", "sns_",
      "key_pair_", "ebs_"], "aws_compute"),
    (["gce_", "gcp_"], "gcp"),
    (["azure_ad_", "azure_"], "azure"),
    (["oci_"], "oci"),
    (["rotate_", "revoke_", "credential_"], "credential_rotation"),
    (["configure_selinux", "configure_seccomp", "configure_apparmor",
      "configure_windows", "deploy_applocker", "wdac_", "asr_", "sysmon_",
      "selinux_learn", "apparmor_learn", "seccomp_learn",
      "apply_protocol_control", "disable_kernel_feature", "apply_registry_fix",
      "remove_vulnerable_package"], "hardening"),
    (["isolate_", "lockdown_", "phishing_", "preserve_evidence"], "incident_response"),
    (["patch_", "enforce_cis", "trivy_", "lynis_", "openscap_",
      "authorized_keys_", "sudoers_", "collect_evidence", "revoke_exposed_"], "compliance"),
    (["offboard_", "onboard_", "emergency_user_", "ldap_", "okta_",
      "keycloak_", "gitlab_", "enforce_mfa"], "identity"),
    (["terraform_", "ansible_", "helm_"], "iac"),
    (["backup_", "create_backup", "verify_backup", "restore_", "dr_"], "backup_dr"),
    (["microsegmentation_", "security_group_", "dns_",
      "deploy_nexplane_", "tailscale_"], "networking"),
]


def _infer_domain(change_type: str) -> str:
    for prefixes, domain in _DOMAIN_PREFIXES:
        for prefix in prefixes:
            if change_type.startswith(prefix) or change_type == prefix.rstrip("_"):
                return domain
    return "infrastructure"


# ---------------------------------------------------------------------------
# Action class inference
# ---------------------------------------------------------------------------

_ACTION_TOKENS: list[tuple[list[str], str]] = [
    (["create", "launch", "provision", "deploy", "onboard", "add"], "create"),
    (["delete", "terminate", "destroy", "remove", "offboard", "revoke"], "delete"),
    (["configure", "apply", "enforce", "set", "enable", "disable", "update",
      "attach", "detach", "block", "restore"], "configure"),
    (["rotate"], "rotate"),
    (["patch"], "patch"),
    (["scan", "trivy", "openscap", "lynis"], "scan"),
    (["audit", "authorized_keys", "sudoers"], "audit"),
    (["isolate", "lockdown", "phishing", "preserve"], "isolate"),
    (["learn"], "observe"),
    (["verify", "collect"], "verify"),
    (["rollback"], "rollback"),
]


def _infer_action_class(change_type: str) -> str:
    tokens = change_type.split("_")
    first = tokens[0] if tokens else ""
    last = tokens[-1] if tokens else ""
    for keywords, action_class in _ACTION_TOKENS:
        for kw in keywords:
            if first == kw or last == kw or change_type.startswith(kw):
                return action_class
    return "execute"


# ---------------------------------------------------------------------------
# Touches inference
# ---------------------------------------------------------------------------

_TOUCHES_PREFIXES: list[tuple[list[str], list[str]]] = [
    (["ec2_"], ["ec2_instance"]),
    (["rds_"], ["rds_instance"]),
    (["s3_", "block_s3_"], ["s3_bucket"]),
    (["iam_user", "disable_iam_user", "enable_iam_user"], ["iam_user"]),
    (["iam_", "attach_iam", "detach_iam"], ["iam_policy"]),
    (["rotate_iam_key", "rotate_iam"], ["iam_user", "iam_access_key"]),
    (["rotate_ssh", "rotate_ssh_keys"], ["ssh_key", "linux_host"]),
    (["rotate_db_", "rotate_postgres_", "rotate_redis_"], ["database_credential"]),
    (["rotate_vault_", "rotate_secrets_manager_", "rotate_jwt_"], ["secret"]),
    (["configure_selinux", "selinux_learn"], ["selinux_policy", "linux_host"]),
    (["configure_seccomp", "seccomp_learn"], ["seccomp_profile", "linux_host"]),
    (["configure_apparmor", "apparmor_learn"], ["apparmor_profile", "linux_host"]),
    (["configure_windows_firewall", "deploy_applocker", "wdac_", "asr_", "sysmon_"], ["windows_host"]),
    (["azure_vm_"], ["azure_vm"]),
    (["azure_nsg", "azure_update_nsg", "azure_restore_nsg"], ["azure_nsg"]),
    (["azure_storage", "azure_blob", "azure_rotate_storage"], ["azure_storage"]),
    (["azure_sql_"], ["azure_sql"]),
    (["gce_", "gcp_"], ["gcp_compute"]),
    (["gcp_firewall_"], ["gcp_firewall"]),
    (["gcp_rotate_service_account", "gcp_disable_service_account"], ["gcp_service_account"]),
    (["oci_"], ["oci_resource"]),
    (["eks_", "ecr_", "helm_"], ["kubernetes"]),
    (["patch_", "remove_vulnerable_package"], ["linux_host", "package"]),
    (["isolate_host", "lockdown_"], ["host", "network"]),
    (["offboard_", "onboard_", "ldap_", "okta_", "keycloak_", "gitlab_"], ["user_account"]),
    (["dns_", "route53_"], ["dns_record"]),
    (["security_group_"], ["security_group"]),
    (["backup_", "create_backup", "verify_backup"], ["backup_artifact"]),
    (["restore_"], ["backup_artifact", "target_resource"]),
    (["microsegmentation_"], ["network_policy"]),
    (["terraform_"], ["infrastructure"]),
    (["ansible_"], ["host"]),
]


def _infer_touches(change_type: str) -> list[str]:
    for prefixes, touches in _TOUCHES_PREFIXES:
        for prefix in prefixes:
            if change_type.startswith(prefix) or change_type == prefix.rstrip("_"):
                return touches
    return ["resource"]


# ---------------------------------------------------------------------------
# Rollback type inference
# ---------------------------------------------------------------------------

def _infer_rollback_type(defn: dict) -> str:
    rollback = defn.get("rollback_action", "")
    if not rollback:
        return "permanent"
    if any(w in rollback for w in ("restore", "snapshot", "backup")):
        return "snapshot_based"
    return "reversible"


# ---------------------------------------------------------------------------
# Preconditions inference
# ---------------------------------------------------------------------------

_PARAM_PRECONDITION_MAP: dict[str, str] = {
    "service_name": "target service identified",
    "instance_id": "target instance identified",
    "cve_id": "CVE ID known",
    "package_name": "target package name known",
    "user_id": "target user identified",
    "username": "target user identified",
    "key_id": "access key ID known",
    "module_source": "SELinux policy module source available",
    "profile_content": "AppArmor profile content available",
    "bucket_name": "target bucket name known",
    "cluster_name": "target cluster name known",
    "secret_id": "secret ID known",
    "vault_path": "Vault secret path known",
    "zone_name": "DNS zone name known",
    "resource_group": "Azure resource group identified",
    "subscription_id": "Azure subscription identified",
    "project_id": "GCP project identified",
    "compartment_id": "OCI compartment identified",
    "playbook": "Ansible playbook path known",
    "template_body": "CloudFormation template available",
    "module_path": "Terraform module path known",
}


def _infer_preconditions(defn: dict) -> list[str]:
    preconditions: list[str] = []
    preflight = defn.get("preflight_checks", [])
    if "asset_exists" in preflight:
        preconditions.append("target asset reachable")
    if "connector_reachable" in preflight:
        preconditions.append("connector credential available")
    params = defn.get("parameters", {})
    for param_name, param_meta in params.items():
        if not param_meta.get("required", False):
            continue
        mapped = _PARAM_PRECONDITION_MAP.get(param_name)
        if mapped:
            preconditions.append(mapped)
        else:
            preconditions.append(f"{param_name} provided")
    return preconditions if preconditions else ["target resource identified"]


# ---------------------------------------------------------------------------
# Effects inference
# ---------------------------------------------------------------------------

def _infer_effects(defn: dict) -> list[str]:
    change_type = defn.get("change_type", "")
    action_class = _infer_action_class(change_type)
    rollback_type = _infer_rollback_type(defn)
    display = defn.get("display_name", change_type.replace("_", " "))

    suffix = " (reversible)" if rollback_type == "reversible" else \
             " (snapshot-based recovery)" if rollback_type == "snapshot_based" else \
             " (permanent)"

    if action_class == "create":
        return [f"{display} created{suffix}"]
    if action_class == "delete":
        return [f"{display} deleted (permanent)"]
    if action_class == "configure":
        return [f"configuration applied{suffix}"]
    if action_class == "rotate":
        return ["credential replaced — prior credential invalidated (permanent)"]
    if action_class == "patch":
        return ["vulnerable packages updated on affected hosts (permanent)"]
    if action_class == "scan":
        return ["vulnerability findings recorded (read-only, no system change)"]
    if action_class == "audit":
        return ["audit findings recorded (read-only, no system change)"]
    if action_class == "isolate":
        return [f"host isolated from network{suffix}"]
    if action_class == "observe":
        return ["observations collected (no persistent state created)"]
    if action_class == "verify":
        return ["verification results recorded (read-only)"]
    return [f"{display} executed{suffix}"]


# ---------------------------------------------------------------------------
# Full entry builder
# ---------------------------------------------------------------------------

def _build_entry(defn: dict) -> dict:
    change_type = defn.get("change_type", "")
    return {
        "change_type": change_type,
        "display_name": defn.get("display_name", ""),
        "description": defn.get("description", ""),
        "domain": _infer_domain(change_type),
        "action_class": _infer_action_class(change_type),
        "touches": _infer_touches(change_type),
        "preconditions": _infer_preconditions(defn),
        "effects": _infer_effects(defn),
        "rollback_type": _infer_rollback_type(defn),
        "parameters": defn.get("parameters", {}),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_manifest() -> list[dict]:
    """Walk all CR definition files and return the enriched manifest list. Caches result."""
    global _MANIFEST
    entries = []
    for path in sorted(_DEFINITIONS_DIR.glob("**/*.json")):
        try:
            defn = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(defn, dict):
                continue
            # Some definitions use "name" instead of "change_type"
            if "change_type" not in defn and "name" in defn:
                defn = dict(defn)
                defn["change_type"] = defn["name"]
            if "change_type" in defn:
                entries.append(_build_entry(defn))
        except Exception:
            continue
    _MANIFEST = entries
    return entries


def get_manifest(
    domain: Optional[str] = None,
    action_class: Optional[str] = None,
    touches: Optional[str] = None,
    rollback_type: Optional[str] = None,
) -> list[dict]:
    """Return filtered manifest entries. Builds manifest on first call if not cached."""
    global _MANIFEST
    if _MANIFEST is None:
        build_manifest()
    entries = _MANIFEST
    if domain:
        entries = [e for e in entries if e["domain"] == domain]
    if action_class:
        entries = [e for e in entries if e["action_class"] == action_class]
    if touches:
        entries = [e for e in entries if touches in e["touches"]]
    if rollback_type:
        entries = [e for e in entries if e["rollback_type"] == rollback_type]
    return entries
