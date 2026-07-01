# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Seed: CloudRun Technologies — SaaS scenario."""
from __future__ import annotations
import uuid
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.connector import Connector, ConnectorType, ConnectorStatus
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.approval import Approval, ApprovalDecision
from app.services.auth_service import hash_password

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

USERS = {
    "admin":    uuid.UUID("00000000-0000-0002-0000-000000000010"),
    "operator": uuid.UUID("00000000-0000-0002-0000-000000000011"),
    "approver": uuid.UUID("00000000-0000-0002-0000-000000000012"),
    "auditor":  uuid.UUID("00000000-0000-0002-0000-000000000013"),
}

CONNECTORS = {
    "aws":        uuid.UUID("00000000-0000-0002-0002-000000000001"),
    "kubernetes": uuid.UUID("00000000-0000-0002-0002-000000000002"),
    "github":     uuid.UUID("00000000-0000-0002-0002-000000000003"),
    "cloudflare": uuid.UUID("00000000-0000-0002-0002-000000000004"),
    "datadog":    uuid.UUID("00000000-0000-0002-0002-000000000005"),
    "snyk":       uuid.UUID("00000000-0000-0002-0002-000000000006"),
}

ASSETS = {
    "k8s_cluster":          uuid.UUID("00000000-0000-0002-0001-000000000001"),
    "app_server_01":        uuid.UUID("00000000-0000-0002-0001-000000000002"),
    "app_server_02":        uuid.UUID("00000000-0000-0002-0001-000000000003"),
    "app_server_03":        uuid.UUID("00000000-0000-0002-0001-000000000004"),
    "prod_postgres":        uuid.UUID("00000000-0000-0002-0001-000000000005"),
    "prod_redis":           uuid.UUID("00000000-0000-0002-0001-000000000006"),
    "cloudrun_dns":         uuid.UUID("00000000-0000-0002-0001-000000000007"),
    "aws_prod_account":     uuid.UUID("00000000-0000-0002-0001-000000000008"),
    "prod_lb":              uuid.UUID("00000000-0000-0002-0001-000000000009"),
    "github_org":           uuid.UUID("00000000-0000-0002-0001-000000000010"),
    "payment_image":        uuid.UUID("00000000-0000-0002-0001-000000000011"),
    "auth_image":           uuid.UUID("00000000-0000-0002-0001-000000000012"),
    "api_image":            uuid.UUID("00000000-0000-0002-0001-000000000013"),
    "snyk_project":         uuid.UUID("00000000-0000-0002-0001-000000000014"),
    "datadog_workspace":    uuid.UUID("00000000-0000-0002-0001-000000000015"),
    "s3_audit_bucket":      uuid.UUID("00000000-0000-0002-0001-000000000016"),
}

CRS = {
    "kernel_01_02":         uuid.UUID("00000000-0000-0002-0003-000000000001"),
    "kernel_03_rollback":   uuid.UUID("00000000-0000-0002-0003-000000000002"),
    "containerize_payment": uuid.UUID("00000000-0000-0002-0003-000000000003"),
    "seccomp_k8s":          uuid.UUID("00000000-0000-0002-0003-000000000004"),
    "rotate_iam_keys":      uuid.UUID("00000000-0000-0002-0003-000000000005"),
    "enforce_pss":          uuid.UUID("00000000-0000-0002-0003-000000000006"),
    "patch_base_image":     uuid.UUID("00000000-0000-0002-0003-000000000007"),
    "rotate_postgres":      uuid.UUID("00000000-0000-0002-0003-000000000008"),
    "migrate_api_dns":      uuid.UUID("00000000-0000-0002-0003-000000000009"),
    "k8s_audit_logging":    uuid.UUID("00000000-0000-0002-0003-000000000010"),
    "rotate_tls_cert":      uuid.UUID("00000000-0000-0002-0003-000000000011"),
    "cloudflare_waf":       uuid.UUID("00000000-0000-0002-0003-000000000012"),
}


def _plan(cr_id: uuid.UUID, steps: list[dict]) -> ChangePlan:
    return ChangePlan(
        change_request_id=cr_id,
        generated_steps=steps,
        preflight_checks=[{"name": "connector_reachable", "check_type": "connectivity", "expected_result": "pass"}],
        blast_radius={"affected_environments": ["prod"], "rollback_available": True},
        rollback_plan={"strategy": "automatic", "automatic": True},
        verification_plan={"checks": [], "success_criteria": "All checks pass"},
        generated_by=PlanGeneratedBy.system,
    )


def _approval(cr_id: uuid.UUID, comment: str, decision=ApprovalDecision.approved) -> Approval:
    return Approval(
        change_request_id=cr_id,
        approver_id=USERS["approver"],
        decision=decision,
        comment=comment,
    )


async def seed(db) -> None:
    existing = await db.get(Organization, ORG_ID)
    if existing:
        print("CloudRun seed already exists - skipping.")
        return

    db.add(Organization(id=ORG_ID, name="CloudRun Technologies"))

    db.add_all([
        User(id=USERS["admin"],    organization_id=ORG_ID, email="admin@cloudrun.example",
             name="Alex Chen",      role=UserRole.admin,             hashed_password=hash_password("admin123")),
        User(id=USERS["operator"], organization_id=ORG_ID, email="operator@cloudrun.example",
             name="Sam Rivera",     role=UserRole.security_operator, hashed_password=hash_password("admin123")),
        User(id=USERS["approver"], organization_id=ORG_ID, email="approver@cloudrun.example",
             name="Jordan Kim",     role=UserRole.approver,          hashed_password=hash_password("admin123")),
        User(id=USERS["auditor"],  organization_id=ORG_ID, email="auditor@cloudrun.example",
             name="Dana Patel",     role=UserRole.auditor,           hashed_password=hash_password("admin123")),
    ])

    db.add_all([
        Connector(id=CONNECTORS["aws"],        organization_id=ORG_ID, name="AWS Production",    connector_type=ConnectorType.aws,        status=ConnectorStatus.active, scoped_permissions={"ec2": ["describe", "snapshot"], "iam": ["rotate-keys"], "s3": ["read", "write"]}),
        Connector(id=CONNECTORS["kubernetes"], organization_id=ORG_ID, name="Kubernetes Prod",   connector_type=ConnectorType.kubernetes,  status=ConnectorStatus.active, scoped_permissions={"workloads": ["read", "patch", "restart"], "namespaces": ["read"], "rbac": ["read", "write"]}),
        Connector(id=CONNECTORS["github"],     organization_id=ORG_ID, name="GitHub",            connector_type=ConnectorType.github,      status=ConnectorStatus.active, scoped_permissions={"repos": ["read"], "workflows": ["read", "trigger"], "packages": ["read", "write"]}),
        Connector(id=CONNECTORS["cloudflare"], organization_id=ORG_ID, name="Cloudflare",        connector_type=ConnectorType.cloudflare,  status=ConnectorStatus.active, scoped_permissions={"dns": ["read", "write"], "zones": ["read"], "waf": ["read", "write"]}),
        Connector(id=CONNECTORS["datadog"],    organization_id=ORG_ID, name="Datadog",           connector_type=ConnectorType.datadog,     status=ConnectorStatus.active, scoped_permissions={"monitors": ["read"], "dashboards": ["read"], "metrics": ["read"]}),
        Connector(id=CONNECTORS["snyk"],       organization_id=ORG_ID, name="Snyk",              connector_type=ConnectorType.snyk,        status=ConnectorStatus.active, scoped_permissions={"projects": ["read"], "vulnerabilities": ["read"], "reports": ["read"]}),
    ])

    db.add_all([
        Asset(id=ASSETS["k8s_cluster"],       organization_id=ORG_ID, name="prod-k8s-cluster",       asset_type=AssetType.kubernetes_cluster, environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "platform-eng", "why_exists": "Runs all production microservices", "depends_on": ["AWS Production account"]}),
        Asset(id=ASSETS["app_server_01"],     organization_id=ORG_ID, name="app-server-01",           asset_type=AssetType.server,             environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "platform-eng", "why_exists": "Hosts payment service (pre-containerization)", "depends_on": [], "os": "ubuntu-22.04", "kernel": "5.15.0-91"}),
        Asset(id=ASSETS["app_server_02"],     organization_id=ORG_ID, name="app-server-02",           asset_type=AssetType.server,             environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "platform-eng", "why_exists": "Hosts auth service (pre-containerization)", "depends_on": [], "os": "ubuntu-22.04", "kernel": "5.15.0-91"}),
        Asset(id=ASSETS["app_server_03"],     organization_id=ORG_ID, name="app-server-03",           asset_type=AssetType.server,             environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "platform-eng", "why_exists": "Hosts notification service", "depends_on": [], "os": "ubuntu-22.04", "kernel": "5.15.0-91"}),
        Asset(id=ASSETS["prod_postgres"],     organization_id=ORG_ID, name="prod-postgres-rds",       asset_type=AssetType.database,           environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "data-eng", "why_exists": "Primary transactional database for all product data", "depends_on": ["app-server-01", "app-server-02"], "engine": "postgres-15", "size_gb": 500}),
        Asset(id=ASSETS["prod_redis"],        organization_id=ORG_ID, name="prod-redis-cache",        asset_type=AssetType.database,           environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "platform-eng", "why_exists": "Session cache and rate limiting store", "depends_on": ["prod-postgres-rds"]}),
        Asset(id=ASSETS["cloudrun_dns"],      organization_id=ORG_ID, name="cloudrun-dns-zone",       asset_type=AssetType.dns_zone,           environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "platform-eng", "why_exists": "Authoritative DNS for cloudrun.io", "depends_on": [], "provider": "cloudflare", "records": 24}),
        Asset(id=ASSETS["aws_prod_account"],  organization_id=ORG_ID, name="aws-prod-account",        asset_type=AssetType.cloud_account,      environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "platform-eng", "why_exists": "Primary cloud account for all production workloads", "depends_on": [], "account_id": "123456789012", "region": "us-east-1"}),
        Asset(id=ASSETS["prod_lb"],           organization_id=ORG_ID, name="prod-load-balancer",      asset_type=AssetType.load_balancer,      environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "platform-eng", "why_exists": "Terminates TLS and routes to K8s ingress", "depends_on": ["prod-k8s-cluster"]}),
        Asset(id=ASSETS["github_org"],        organization_id=ORG_ID, name="github-cloudrun-org",     asset_type=AssetType.application,        environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "eng-leads", "why_exists": "Source of truth for all application code and CI/CD pipelines", "depends_on": []}),
        Asset(id=ASSETS["payment_image"],     organization_id=ORG_ID, name="payment-service-image",   asset_type=AssetType.container_image,    environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "payments-team", "why_exists": "Container image for payment processing service", "depends_on": ["prod-k8s-cluster"], "base_image": "ubuntu:22.04"}),
        Asset(id=ASSETS["auth_image"],        organization_id=ORG_ID, name="auth-service-image",      asset_type=AssetType.container_image,    environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "platform-eng", "why_exists": "Container image for authentication service", "depends_on": [], "base_image": "ubuntu:22.04"}),
        Asset(id=ASSETS["api_image"],         organization_id=ORG_ID, name="api-service-image",       asset_type=AssetType.container_image,    environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "platform-eng", "why_exists": "Container image for public API gateway", "depends_on": [], "base_image": "ubuntu:22.04"}),
        Asset(id=ASSETS["snyk_project"],      organization_id=ORG_ID, name="snyk-cloudrun-project",   asset_type=AssetType.application,        environment=Environment.prod, criticality=Criticality.medium,   asset_metadata={"owner": "security-eng", "why_exists": "Tracks SCA vulnerabilities across all repos", "depends_on": []}),
        Asset(id=ASSETS["datadog_workspace"], organization_id=ORG_ID, name="datadog-workspace",       asset_type=AssetType.application,        environment=Environment.prod, criticality=Criticality.medium,   asset_metadata={"owner": "platform-eng", "why_exists": "Observability for prod infrastructure and services", "depends_on": []}),
        Asset(id=ASSETS["s3_audit_bucket"],   organization_id=ORG_ID, name="prod-s3-audit-bucket",    asset_type=AssetType.storage_bucket,     environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "security-eng", "why_exists": "Immutable audit log storage for CloudTrail and K8s audit logs", "depends_on": []}),
    ])

    # CR 1: Kernel upgrade servers 01+02 — completed
    db.add(ChangeRequest(id=CRS["kernel_01_02"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Kernel upgrade: app-server-01 and app-server-02",
        description="Upgrade kernel from 5.15 to 6.1. Pre-change snapshot taken.",
        change_type=ChangeType.ec2_stop_start, risk_level=RiskLevel.high, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["app_server_01"]), str(ASSETS["app_server_02"])],
        desired_outcome={"instance_name": "app-server-01", "rollback_strategy": "stop_if_running"}))
    db.add(_plan(CRS["kernel_01_02"], [
        {"step_number": 1, "name": "Create Pre-Change Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Stop Instance",              "generic_action": "stop_instance",   "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Start Instance",             "generic_action": "start_instance",  "estimated_duration_seconds": 60},
    ]))
    db.add(_approval(CRS["kernel_01_02"], "Kernel upgrade approved. Snapshot confirmed."))

    # CR 2: Kernel upgrade server 03 — rolled_back
    db.add(ChangeRequest(id=CRS["kernel_03_rollback"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Kernel upgrade: app-server-03",
        description="Node panic on first boot after upgrade. Rolled back to pre-change snapshot.",
        change_type=ChangeType.ec2_stop_start, risk_level=RiskLevel.high, status=ChangeRequestStatus.rolled_back,
        target_asset_ids=[str(ASSETS["app_server_03"])],
        desired_outcome={"instance_name": "app-server-03", "rollback_strategy": "stop_if_running"}))
    db.add(_plan(CRS["kernel_03_rollback"], [
        {"step_number": 1, "name": "Create Pre-Change Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Stop Instance",              "generic_action": "stop_instance",   "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Start Instance",             "generic_action": "start_instance",  "estimated_duration_seconds": 60},
    ]))
    db.add(_approval(CRS["kernel_03_rollback"], "Approved for maintenance window."))

    # CR 3: Containerize payment service — awaiting_approval
    db.add(ChangeRequest(id=CRS["containerize_payment"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Containerize payment service",
        description="Move payment service from app-server-01 to prod-k8s-cluster. Requires CTO approval.",
        change_type=ChangeType.tailscale_generate_auth_key, risk_level=RiskLevel.critical, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["app_server_01"]), str(ASSETS["k8s_cluster"])],
        desired_outcome={"reusable": False, "ephemeral": False, "expiry_seconds": 86400}))
    db.add(_plan(CRS["containerize_payment"], [
        {"step_number": 1, "name": "Generate Tailscale Auth Key", "generic_action": "tailscale_generate_auth_key", "estimated_duration_seconds": 10},
    ]))

    # CR 4: Apply seccomp-default to K8s — draft
    db.add(ChangeRequest(id=CRS["seccomp_k8s"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Apply seccomp-default profile to K8s workloads",
        description="Apply RuntimeDefault seccomp profile to all pods in prod namespace.",
        change_type=ChangeType.security_group_update, risk_level=RiskLevel.medium, status=ChangeRequestStatus.draft,
        target_asset_ids=[str(ASSETS["k8s_cluster"])],
        desired_outcome={"group_id": "k8s-prod-namespace", "rules": [{"type": "seccomp", "profile": "RuntimeDefault"}]}))

    # CR 5: Rotate AWS IAM keys — completed
    db.add(ChangeRequest(id=CRS["rotate_iam_keys"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate AWS IAM keys — CI/CD service account",
        description="Old key revoked 30 days after new key distributed.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["aws_prod_account"])],
        desired_outcome={"service": "aws-iam", "key_type": "api_key", "grace_period_hours": 720, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_iam_keys"], [
        {"step_number": 1, "name": "Generate New Key",     "generic_action": "generate_key",     "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Distribute New Key",   "generic_action": "distribute_key",   "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Schedule Old Revoke",  "generic_action": "schedule_revoke",  "estimated_duration_seconds": 5},
    ]))
    db.add(_approval(CRS["rotate_iam_keys"], "90-day rotation policy. Approved."))

    # CR 6: Enforce Pod Security Standards — planned
    db.add(ChangeRequest(id=CRS["enforce_pss"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Enforce Pod Security Standards (restricted)",
        description="Enforce restricted PSS on prod namespace. Blocks privileged containers.",
        change_type=ChangeType.microsegmentation_policy, risk_level=RiskLevel.high, status=ChangeRequestStatus.planned,
        target_asset_ids=[str(ASSETS["k8s_cluster"])],
        desired_outcome={"policy_rules": [{"namespace": "prod", "level": "restricted"}], "critical_flows": [], "rollback_strategy": "remove_staged_policy"}))
    db.add(_plan(CRS["enforce_pss"], [
        {"step_number": 1, "name": "Analyze Current Policy", "generic_action": "analyze_flows",   "estimated_duration_seconds": 20},
        {"step_number": 2, "name": "Stage PSS Policy",       "generic_action": "stage_policy",    "estimated_duration_seconds": 15},
        {"step_number": 3, "name": "Validate Staged",        "generic_action": "validate_staged", "estimated_duration_seconds": 30},
    ]))

    # CR 7: Patch base image CVE — awaiting_approval
    db.add(ChangeRequest(id=CRS["patch_base_image"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Patch base image CVE: ubuntu:22.04",
        description="Critical CVE in base image used by payment-service-image, auth-service-image, api-service-image. Linked to Snyk finding.",
        change_type=ChangeType.snapshot_asset, risk_level=RiskLevel.critical, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["payment_image"]), str(ASSETS["auth_image"]), str(ASSETS["api_image"])],
        desired_outcome={"snapshot_tag": "pre-cve-patch", "rollback_strategy": "rollback_unavailable"}))
    db.add(_plan(CRS["patch_base_image"], [
        {"step_number": 1, "name": "Capture Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Verify Snapshot",  "generic_action": "verify_snapshot", "estimated_duration_seconds": 20},
    ]))

    # CR 8: Rotate PostgreSQL master credentials — draft
    db.add(ChangeRequest(id=CRS["rotate_postgres"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate PostgreSQL master credentials",
        description="Master password rotation for prod-postgres-rds. Reconstitution rollback: snapshot before rotation.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.high, status=ChangeRequestStatus.draft,
        target_asset_ids=[str(ASSETS["prod_postgres"])],
        desired_outcome={"service": "postgres", "key_type": "password", "grace_period_hours": 24, "rollback_strategy": "cancel_revocation"}))

    # CR 9: Migrate API subdomain DNS — completed
    db.add(ChangeRequest(id=CRS["migrate_api_dns"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Migrate API subdomain to new load balancer",
        description="api.cloudrun.io CNAME updated. TTL-based rollback. Zero downtime.",
        change_type=ChangeType.dns_update, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["cloudrun_dns"])],
        desired_outcome={"record_name": "api.cloudrun.io", "record_type": "CNAME", "new_value": "prod-lb.cloudrun.io", "ttl": 300, "rollback_strategy": "restore_previous_record"}))
    db.add(_plan(CRS["migrate_api_dns"], [
        {"step_number": 1, "name": "Capture DNS Record",   "generic_action": "capture_dns_record",   "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Update DNS Record",    "generic_action": "update_dns_record",    "estimated_duration_seconds": 10},
        {"step_number": 3, "name": "Wait Propagation",     "generic_action": "wait_dns_propagation", "estimated_duration_seconds": 60},
    ]))
    db.add(_approval(CRS["migrate_api_dns"], "CNAME migration approved. Zero-downtime confirmed."))

    # CR 10: Enable K8s audit logging to S3 — completed
    db.add(ChangeRequest(id=CRS["k8s_audit_logging"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Enable K8s audit logging to S3",
        description="Audit log stream to prod-s3-audit-bucket configured.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["k8s_cluster"]), str(ASSETS["s3_audit_bucket"])],
        desired_outcome={"template_id": "configure_audit_logging", "parameters": {"destination": "s3://prod-s3-audit-bucket"}}))
    db.add(_plan(CRS["k8s_audit_logging"], [
        {"step_number": 1, "name": "Validate Template", "generic_action": "validate_template", "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Execute Template",  "generic_action": "execute_template",  "estimated_duration_seconds": 30},
    ]))
    db.add(_approval(CRS["k8s_audit_logging"], "Audit logging required for SOC 2. Approved."))

    # CR 11: Rotate wildcard TLS cert — awaiting_approval
    db.add(ChangeRequest(id=CRS["rotate_tls_cert"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate wildcard TLS certificate",
        description="*.cloudrun.io cert expires in 28 days.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.high, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["prod_lb"])],
        desired_outcome={"service": "cloudflare-tls", "key_type": "certificate", "grace_period_hours": 24, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_tls_cert"], [
        {"step_number": 1, "name": "Generate New Certificate", "generic_action": "generate_key",   "estimated_duration_seconds": 30},
        {"step_number": 2, "name": "Distribute Certificate",   "generic_action": "distribute_key", "estimated_duration_seconds": 20},
        {"step_number": 3, "name": "Schedule Old Revoke",      "generic_action": "schedule_revoke","estimated_duration_seconds": 5},
    ]))

    # CR 12: Cloudflare WAF rules — completed
    db.add(ChangeRequest(id=CRS["cloudflare_waf"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Add Cloudflare WAF rules: SQLi patterns",
        description="3 new WAF rules deployed. Rollback: rule disable.",
        change_type=ChangeType.security_group_update, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["cloudrun_dns"])],
        desired_outcome={"group_id": "cloudflare-waf-prod", "rules": [{"id": "sqli-001", "action": "block"}, {"id": "sqli-002", "action": "block"}, {"id": "sqli-003", "action": "block"}]}))
    db.add(_plan(CRS["cloudflare_waf"], [
        {"step_number": 1, "name": "Export Current Rules",    "generic_action": "export_security_group",   "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Validate New Rules",      "generic_action": "validate_security_rules", "estimated_duration_seconds": 10},
        {"step_number": 3, "name": "Apply WAF Rules",         "generic_action": "update_security_group",   "estimated_duration_seconds": 15},
    ]))
    db.add(_approval(CRS["cloudflare_waf"], "SQLi WAF rules approved. Tested in staging first."))

    await db.commit()
    print("CloudRun Technologies seed created.")
