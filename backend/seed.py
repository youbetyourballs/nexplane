# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Seed script - idempotent (skips if org already exists).
Run: python seed.py
"""
import asyncio
import os
import uuid

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.connector import Connector, ConnectorType, ConnectorStatus
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.approval import Approval, ApprovalDecision
from app.models.audit_event import AuditEvent
from app.services.auth_service import hash_password
from app.models.project import Project, ProjectStatus


ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

USER_IDS = {
    "admin": uuid.UUID("00000000-0000-0000-0000-000000000010"),
    "operator": uuid.UUID("00000000-0000-0000-0000-000000000011"),
    "approver": uuid.UUID("00000000-0000-0000-0000-000000000012"),
    "auditor": uuid.UUID("00000000-0000-0000-0000-000000000013"),
}

ASSET_IDS = {
    "finance_app": uuid.UUID("00000000-0000-0000-0001-000000000001"),
    "dns_zone": uuid.UUID("00000000-0000-0000-0001-000000000002"),
    "aws_account": uuid.UUID("00000000-0000-0000-0001-000000000003"),
    "paloalto_fw": uuid.UUID("00000000-0000-0000-0001-000000000004"),
    "linux_servers": uuid.UUID("00000000-0000-0000-0001-000000000005"),
}

CONNECTOR_IDS = {
    "aws": uuid.UUID("00000000-0000-0000-0002-000000000001"),
    "cloudflare": uuid.UUID("00000000-0000-0000-0002-000000000002"),
    "paloalto": uuid.UUID("00000000-0000-0000-0002-000000000003"),
    "ssh_runner": uuid.UUID("00000000-0000-0000-0002-000000000004"),
}

NEW_CONNECTOR_IDS = {
    "active_directory": uuid.UUID("00000000-0000-0000-0002-000000000005"),
    "crowdstrike": uuid.UUID("00000000-0000-0000-0002-000000000006"),
    "tenable": uuid.UUID("00000000-0000-0000-0002-000000000007"),
    "azure": uuid.UUID("00000000-0000-0000-0002-000000000008"),
}

PROJECT_IDS = {
    "isolate_investigate": uuid.UUID("00000000-0000-0000-0004-000000000001"),
    "deploy_edr": uuid.UUID("00000000-0000-0000-0004-000000000002"),
    "remediate_cve": uuid.UUID("00000000-0000-0000-0004-000000000003"),
    "offboard_employee": uuid.UUID("00000000-0000-0000-0004-000000000004"),
    "azure_storage": uuid.UUID("00000000-0000-0000-0004-000000000005"),
    "tighten_firewall": uuid.UUID("00000000-0000-0000-0004-000000000006"),
    "mfa_enforcement": uuid.UUID("00000000-0000-0000-0004-000000000007"),
    "microsegmentation": uuid.UUID("00000000-0000-0000-0004-000000000008"),
    "workload_harden": uuid.UUID("00000000-0000-0000-0004-000000000009"),
    "firewall_chokepoints": uuid.UUID("00000000-0000-0000-0004-000000000010"),
}

CR_IDS = {
    "dns_awaiting": uuid.UUID("00000000-0000-0000-0003-000000000001"),
    "snapshot_completed": uuid.UUID("00000000-0000-0000-0003-000000000002"),
    "remote_rejected": uuid.UUID("00000000-0000-0000-0003-000000000003"),
    "microseg_planned": uuid.UUID("00000000-0000-0000-0003-000000000004"),
}


async def seed():
    async with AsyncSessionLocal() as db:
        existing = await db.get(Organization, ORG_ID)
        if existing:
            print("Seed data already exists - skipping.")
            return

        org = Organization(id=ORG_ID, name="Acme Security Corp")
        db.add(org)

        users = [
            User(id=USER_IDS["admin"], organization_id=ORG_ID, email="admin@acme.example",
                 name="Alex Admin", role=UserRole.admin, hashed_password=hash_password("admin123")),
            User(id=USER_IDS["operator"], organization_id=ORG_ID, email="operator@acme.example",
                 name="Sam Operator", role=UserRole.security_operator, hashed_password=hash_password("operator123")),
            User(id=USER_IDS["approver"], organization_id=ORG_ID, email="approver@acme.example",
                 name="Pat Approver", role=UserRole.approver, hashed_password=hash_password("approver123")),
            User(id=USER_IDS["auditor"], organization_id=ORG_ID, email="auditor@acme.example",
                 name="Dana Auditor", role=UserRole.auditor, hashed_password=hash_password("auditor123")),
        ]
        db.add_all(users)

        assets = [
            Asset(id=ASSET_IDS["finance_app"], organization_id=ORG_ID, name="Prod Finance Application",
                  asset_type=AssetType.application, environment=Environment.prod, criticality=Criticality.critical,
                  asset_metadata={"owner": "finance-team", "why_exists": "Core financial reporting and ERP system", "region": "us-east-1", "tier": "tier-1", "depends_on": ["Linux Server Group (App Tier)", "AWS · 123456789012 (demo)"]}),
            Asset(id=ASSET_IDS["dns_zone"], organization_id=ORG_ID, name="Prod DNS Zone (acme.example)",
                  asset_type=AssetType.dns_zone, environment=Environment.prod, criticality=Criticality.high,
                  asset_metadata={"owner": "platform-ops", "why_exists": "Authoritative DNS for acme.example domain", "zone": "acme.example", "provider": "cloudflare", "records": 247, "depends_on": []}),
            Asset(id=ASSET_IDS["aws_account"], organization_id=ORG_ID, name="AWS Prod Account",
                  asset_type=AssetType.cloud_account, environment=Environment.prod, criticality=Criticality.critical,
                  asset_metadata={"owner": "cloud-eng", "why_exists": "Primary cloud account hosting all production infrastructure", "account_id": "123456789012", "region": "us-east-1", "depends_on": []}),
            Asset(id=ASSET_IDS["paloalto_fw"], organization_id=ORG_ID, name="Palo Alto Prod Firewall",
                  asset_type=AssetType.firewall, environment=Environment.prod, criticality=Criticality.critical,
                  asset_metadata={"owner": "network-sec", "why_exists": "Perimeter firewall enforcing north-south traffic policy", "model": "PA-5220", "software": "PAN-OS 11.0", "depends_on": []}),
            Asset(id=ASSET_IDS["linux_servers"], organization_id=ORG_ID, name="Linux Server Group (App Tier)",
                  asset_type=AssetType.server, environment=Environment.prod, criticality=Criticality.high,
                  asset_metadata={"owner": "platform-ops", "why_exists": "App tier servers running the finance application workloads", "count": 12, "os": "Ubuntu 22.04", "purpose": "app-tier", "depends_on": ["AWS · 123456789012 (demo)", "Palo Alto Prod Firewall"]}),
        ]
        db.add_all(assets)

        connectors = [
            Connector(id=CONNECTOR_IDS["aws"], organization_id=ORG_ID, connector_type=ConnectorType.aws,
                      name="AWS Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"ec2": ["describe", "snapshot"], "iam": ["rotate-keys"], "sg": ["read", "write"]}),
            Connector(id=CONNECTOR_IDS["cloudflare"], organization_id=ORG_ID, connector_type=ConnectorType.cloudflare,
                      name="Cloudflare Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"dns": ["read", "write"], "zones": ["read"]}),
            Connector(id=CONNECTOR_IDS["paloalto"], organization_id=ORG_ID, connector_type=ConnectorType.paloalto,
                      name="Palo Alto Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"policy": ["read", "stage"], "flows": ["read"]}),
            Connector(id=CONNECTOR_IDS["ssh_runner"], organization_id=ORG_ID, connector_type=ConnectorType.ssh,
                      name="SSH Runner Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"templates": ["restart_service", "check_disk_usage", "flush_dns_cache"]}),
        ]
        db.add_all(connectors)

        # CR 1: DNS update - awaiting approval
        cr1 = ChangeRequest(
            id=CR_IDS["dns_awaiting"],
            organization_id=ORG_ID,
            requester_id=USER_IDS["operator"],
            title="Update DNS A record for api.acme.example",
            description="The API service has been migrated to a new IP. DNS A record must be updated.",
            change_type=ChangeType.dns_update,
            target_asset_ids=[str(ASSET_IDS["dns_zone"])],
            desired_outcome={
                "record_name": "api.acme.example",
                "record_type": "A",
                "new_value": "203.0.113.42",
                "ttl": 300,
                "rollback_strategy": "restore_previous_record",
            },
            risk_level=RiskLevel.high,
            status=ChangeRequestStatus.awaiting_approval,
        )
        db.add(cr1)

        plan1 = ChangePlan(
            change_request_id=CR_IDS["dns_awaiting"],
            generated_steps=[
                {"step_number": 1, "name": "Capture Current DNS Record", "connector_action": "dns.get_record", "parameters": {"record_name": "api.acme.example", "record_type": "A"}, "estimated_duration_seconds": 5},
                {"step_number": 2, "name": "Validate New Record Value", "connector_action": "dns.validate_target", "parameters": {"target": "203.0.113.42"}, "estimated_duration_seconds": 10},
                {"step_number": 3, "name": "Apply DNS Update", "connector_action": "dns.update_record", "parameters": {"record_name": "api.acme.example", "new_value": "203.0.113.42", "ttl": 300}, "rollback_action": "dns.update_record", "rollback_parameters": {"restore_previous": True}, "estimated_duration_seconds": 15},
                {"step_number": 4, "name": "Propagation Wait", "connector_action": "dns.wait_propagation", "parameters": {"ttl_seconds": 300}, "estimated_duration_seconds": 60},
            ],
            preflight_checks=[
                {"name": "connector_reachable", "description": "Verify connector endpoint is reachable", "check_type": "connectivity", "expected_result": "HTTP 200"},
                {"name": "dns_record_exists", "description": "Verify DNS record 'api.acme.example' exists", "check_type": "dns", "expected_result": "Record found in zone"},
                {"name": "new_value_reachable", "description": "Confirm new DNS target is reachable", "check_type": "connectivity", "expected_result": "Target IP responds"},
            ],
            blast_radius={
                "affected_assets": [{"id": str(ASSET_IDS["dns_zone"]), "name": "Prod DNS Zone (acme.example)", "env": "prod", "criticality": "high"}],
                "affected_environments": ["prod"],
                "estimated_impact": "DNS resolution failure for affected zone during propagation window",
                "affected_services": ["DNS consumers", "Web applications", "API clients"],
                "recovery_time_estimate": "5-60 minutes (TTL dependent)",
                "rollback_available": True,
            },
            rollback_plan={"strategy": "restore_previous_record", "description": "Restore DNS record to captured pre-change value", "estimated_duration_seconds": 30, "automatic": True},
            verification_plan={
                "checks": [
                    {"name": "dns_resolves_new_value", "description": "Confirm DNS query returns new value", "method": "dns_lookup"},
                    {"name": "application_health", "description": "HTTP health check on affected endpoints", "method": "http_probe"},
                ],
                "success_criteria": "DNS resolves to new value AND health check returns 2xx",
            },
            generated_by=PlanGeneratedBy.system,
        )
        db.add(plan1)

        # CR 2: Snapshot - completed
        cr2 = ChangeRequest(
            id=CR_IDS["snapshot_completed"],
            organization_id=ORG_ID,
            requester_id=USER_IDS["operator"],
            title="Create pre-deployment snapshot of AWS Prod Account",
            description="Pre-deployment snapshot before Q1 infrastructure upgrade.",
            change_type=ChangeType.snapshot_asset,
            target_asset_ids=[str(ASSET_IDS["aws_account"])],
            desired_outcome={"snapshot_tag": "pre-q1-upgrade-2026", "rollback_strategy": "rollback_unavailable"},
            risk_level=RiskLevel.low,
            status=ChangeRequestStatus.completed,
        )
        db.add(cr2)

        plan2 = ChangePlan(
            change_request_id=CR_IDS["snapshot_completed"],
            generated_steps=[
                {"step_number": 1, "name": "Pre-snapshot Health Check", "connector_action": "cloud.health_check", "parameters": {}, "estimated_duration_seconds": 10},
                {"step_number": 2, "name": "Create Snapshot", "connector_action": "cloud.create_snapshot", "parameters": {"snapshot_tag": "pre-q1-upgrade-2026"}, "estimated_duration_seconds": 120},
                {"step_number": 3, "name": "Verify Snapshot Integrity", "connector_action": "cloud.verify_snapshot", "parameters": {}, "estimated_duration_seconds": 30},
            ],
            preflight_checks=[{"name": "sufficient_storage", "description": "Verify snapshot storage quota available", "check_type": "capacity", "expected_result": "Available storage > 110%"}],
            blast_radius={
                "affected_assets": [{"id": str(ASSET_IDS["aws_account"]), "name": "AWS Prod Account", "env": "prod", "criticality": "critical"}],
                "affected_environments": ["prod"],
                "estimated_impact": "Potential I/O performance degradation during snapshot",
                "affected_services": ["Storage I/O"],
                "recovery_time_estimate": "5-30 minutes",
                "rollback_available": False,
            },
            rollback_plan={"strategy": "rollback_unavailable", "description": "Snapshots are additive; no rollback needed.", "automatic": False},
            verification_plan={"checks": [{"name": "snapshot_exists", "description": "Snapshot ID present and complete", "method": "api_check"}], "success_criteria": "Snapshot status = complete"},
            generated_by=PlanGeneratedBy.system,
        )
        db.add(plan2)

        approval2 = Approval(
            change_request_id=CR_IDS["snapshot_completed"],
            approver_id=USER_IDS["approver"],
            decision=ApprovalDecision.approved,
            comment="Pre-deployment snapshot approved. Low risk.",
        )
        db.add(approval2)

        # CR 3: Remote command - rejected
        cr3 = ChangeRequest(
            id=CR_IDS["remote_rejected"],
            organization_id=ORG_ID,
            requester_id=USER_IDS["operator"],
            title="Restart nginx on Linux Server Group",
            description="nginx service needs restart after config change.",
            change_type=ChangeType.remote_command,
            target_asset_ids=[str(ASSET_IDS["linux_servers"])],
            desired_outcome={"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
            risk_level=RiskLevel.high,
            status=ChangeRequestStatus.rejected,
        )
        db.add(cr3)

        approval3 = Approval(
            change_request_id=CR_IDS["remote_rejected"],
            approver_id=USER_IDS["approver"],
            decision=ApprovalDecision.rejected,
            comment="Nginx restart requires verification that config changes have been peer-reviewed. Resubmit with config diff attached.",
        )
        db.add(approval3)

        # CR 4: Microsegmentation - planned
        cr4 = ChangeRequest(
            id=CR_IDS["microseg_planned"],
            organization_id=ORG_ID,
            requester_id=USER_IDS["operator"],
            title="Stage microsegmentation policy for finance app tier",
            description="Apply zero-trust microsegmentation between finance app tier and database tier.",
            change_type=ChangeType.microsegmentation_policy,
            target_asset_ids=[str(ASSET_IDS["finance_app"]), str(ASSET_IDS["paloalto_fw"])],
            desired_outcome={
                "policy_rules": [
                    {"src": "finance-app-tier", "dst": "finance-db-tier", "port": 5432, "action": "allow"},
                    {"src": "finance-app-tier", "dst": "internet", "port": "any", "action": "deny"},
                ],
                "critical_flows": [{"name": "app-to-db", "src": "finance-app-tier", "dst": "finance-db-tier", "port": 5432}],
                "rollback_strategy": "remove_staged_policy",
            },
            risk_level=RiskLevel.high,
            status=ChangeRequestStatus.planned,
        )
        db.add(cr4)

        plan4 = ChangePlan(
            change_request_id=CR_IDS["microseg_planned"],
            generated_steps=[
                {"step_number": 1, "name": "Analyze Current Flows", "connector_action": "firewall.analyze_flows", "parameters": {}, "estimated_duration_seconds": 30},
                {"step_number": 2, "name": "Generate Policy Diff", "connector_action": "firewall.generate_diff", "parameters": {}, "estimated_duration_seconds": 15},
                {"step_number": 3, "name": "Stage Policy (Simulation Mode)", "connector_action": "firewall.stage_policy", "parameters": {"mode": "simulation"}, "rollback_action": "firewall.remove_staged_policy", "estimated_duration_seconds": 20},
                {"step_number": 4, "name": "Validate Staged Policy", "connector_action": "firewall.validate_staged", "parameters": {}, "estimated_duration_seconds": 30},
            ],
            preflight_checks=[
                {"name": "connector_reachable", "description": "Verify firewall connector reachable", "check_type": "connectivity", "expected_result": "HTTP 200"},
                {"name": "policy_syntax_valid", "description": "Policy rules pass syntax validation", "check_type": "validation", "expected_result": "Zero syntax errors"},
            ],
            blast_radius={
                "affected_assets": [
                    {"id": str(ASSET_IDS["finance_app"]), "name": "Prod Finance Application", "env": "prod", "criticality": "critical"},
                    {"id": str(ASSET_IDS["paloalto_fw"]), "name": "Palo Alto Prod Firewall", "env": "prod", "criticality": "critical"},
                ],
                "affected_environments": ["prod"],
                "estimated_impact": "Simulation mode only - no live traffic impact",
                "affected_services": [],
                "recovery_time_estimate": "None (staged)",
                "rollback_available": True,
            },
            rollback_plan={"strategy": "remove_staged_policy", "description": "Remove staged simulation policy from firewall", "estimated_duration_seconds": 15, "automatic": True},
            verification_plan={
                "checks": [
                    {"name": "policy_staged", "description": "Policy is staged in simulation mode", "method": "api_check"},
                    {"name": "no_critical_flows_blocked", "description": "No critical flows blocked in simulation", "method": "flow_test"},
                ],
                "success_criteria": "Policy staged AND no critical flow violations",
            },
            generated_by=PlanGeneratedBy.system,
        )
        db.add(plan4)

        # Audit trail for completed snapshot
        audit_events = [
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="change_request.created", event_payload={"title": "Create pre-deployment snapshot of AWS Prod Account"}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="change_plan.generated", event_payload={"risk_level": "low", "risk_score": 30}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="change_request.submitted_for_approval", event_payload={}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["approver"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="change_request.approved", event_payload={"comment": "Pre-deployment snapshot approved. Low risk."}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="workflow.started", event_payload={"workflow": "ExecuteChangeWorkflow"}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="preflight.passed", event_payload={"checks": ["sufficient_storage"]}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="execution.completed", event_payload={"snapshots": [{"snapshot_id": "snap-abc123def456", "status": "completed"}]}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="verification.passed", event_payload={"success_criteria": "Snapshot status = complete"}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["snapshot_completed"],
                       event_type="workflow.completed", event_payload={"outcome": "success"}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["approver"], change_request_id=CR_IDS["dns_awaiting"],
                       event_type="change_request.created", event_payload={"title": "Update DNS A record for api.acme.example"}),
            AuditEvent(organization_id=ORG_ID, actor_id=USER_IDS["operator"], change_request_id=CR_IDS["remote_rejected"],
                       event_type="change_request.rejected", event_payload={"reason": "Config diff required"}),
        ]
        db.add_all(audit_events)

        await db.commit()
        print("Seed data created successfully.")
        print("\nDemo credentials:")
        print("  admin@acme.example     / admin123")
        print("  operator@acme.example  / operator123")
        print("  approver@acme.example  / approver123")
        print("  auditor@acme.example   / auditor123")


async def seed_expansion():
    async with AsyncSessionLocal() as db:
        existing = await db.get(Connector, NEW_CONNECTOR_IDS["crowdstrike"])
        existing_project = await db.get(Project, PROJECT_IDS["isolate_investigate"])
        if existing or existing_project:
            print("Expansion seed already exists - skipping.")
            return

        new_connectors = [
            Connector(id=NEW_CONNECTOR_IDS["active_directory"], organization_id=ORG_ID,
                      connector_type=ConnectorType.active_directory,
                      name="Active Directory Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"ldap": ["read"], "accounts": ["disable", "enable", "reset"], "groups": ["read", "write"]}),
            Connector(id=NEW_CONNECTOR_IDS["crowdstrike"], organization_id=ORG_ID,
                      connector_type=ConnectorType.crowdstrike,
                      name="CrowdStrike Falcon Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"hosts": ["read", "isolate", "restore"], "sensors": ["deploy", "remove"], "rtr": ["execute"]}),
            Connector(id=NEW_CONNECTOR_IDS["tenable"], organization_id=ORG_ID,
                      connector_type=ConnectorType.tenable,
                      name="Tenable Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"scans": ["read", "launch"], "assets": ["read"], "vulnerabilities": ["read"]}),
            Connector(id=NEW_CONNECTOR_IDS["azure"], organization_id=ORG_ID,
                      connector_type=ConnectorType.azure,
                      name="Azure Mock Connector", status=ConnectorStatus.active,
                      scoped_permissions={"compute": ["read"], "storage": ["read", "write"], "network": ["read", "write"]}),
        ]
        db.add_all(new_connectors)

        projects = [
            Project(id=PROJECT_IDS["isolate_investigate"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Isolate and Investigate Compromised Endpoint",
                    description="Contain a suspected compromise on a production host.",
                    goal="Isolate the compromised endpoint via CrowdStrike, disable the associated user account in Active Directory, and snapshot the machine in AWS for forensic review."),
            Project(id=PROJECT_IDS["deploy_edr"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Deploy EDR to Unprotected Hosts",
                    description="Close sensor coverage gaps identified in the CrowdStrike inventory.",
                    goal="Discover all hosts missing CrowdStrike Falcon sensor coverage and deploy the sensor to every unprotected endpoint in the production environment."),
            Project(id=PROJECT_IDS["remediate_cve"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Remediate Critical CVE Across Fleet",
                    description="Patch a critical vulnerability identified in the latest Tenable scan.",
                    goal="Identify all assets affected by a critical CVE, apply the patch via SSH remote command, and verify remediation with a follow-up Tenable scan."),
            Project(id=PROJECT_IDS["offboard_employee"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Offboard Departed Employee",
                    description="Remove access for a departed employee across all identity systems.",
                    goal="Disable the user's Active Directory account, remove them from all security groups, and revoke their Okta API credentials."),
            Project(id=PROJECT_IDS["azure_storage"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Remediate Public Azure Storage",
                    description="Fix public blob access misconfiguration found in Azure posture scan.",
                    goal="Discover all Azure storage accounts with public blob access enabled and disable public access on all affected accounts."),
            Project(id=PROJECT_IDS["tighten_firewall"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Tighten Firewall After Vulnerability Scan",
                    description="Reduce attack surface based on Tenable open port findings.",
                    goal="Use Tenable scan findings to identify unnecessary open ports and tighten the corresponding AWS security group and Azure NSG rules."),
            Project(id=PROJECT_IDS["mfa_enforcement"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="MFA Enforcement for Non-Compliant Accounts",
                    description="Enforce MFA across accounts identified as non-compliant in the AD audit.",
                    goal="Discover all Active Directory identity assets without MFA enabled and enforce MFA enrollment across every non-compliant account."),
            Project(id=PROJECT_IDS["microsegmentation"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Microsegmentation for Payments Subnet",
                    description="Apply zero-trust segmentation between the payments app tier and database tier.",
                    goal="Analyze current traffic flows to the payments subnet, generate a microsegmentation policy diff, stage and apply the new PaloAlto policy, and update AWS security groups to match."),
            Project(id=PROJECT_IDS["workload_harden"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Harden Payments-API Workload Isolation",
                    description="Reduce the blast radius of payments-api by applying OS-level controls.",
                    goal="Snapshot the payments-api host, apply a SELinux policy to restrict process permissions, and containerize the application for workload isolation."),
            Project(id=PROJECT_IDS["firewall_chokepoints"], organization_id=ORG_ID,
                    created_by=USER_IDS["operator"], status=ProjectStatus.draft,
                    name="Identify and Respond to Firewall Chokepoints",
                    description="Use PaloAlto traffic data to find and remediate policy bottlenecks.",
                    goal="Enable enhanced traffic logging on the production firewall, ingest flow data to identify high-volume chokepoints, analyze flows, and update policy rules at identified bottlenecks."),
        ]
        db.add_all(projects)
        await db.commit()
        print("Expansion seed (new connectors + example projects) created successfully.")


async def seed_runbooks():
    from app.models.runbook import Runbook
    from app.seed.runbook_templates import load_seed_templates
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Runbook).limit(1))
        if existing.scalar_one_or_none():
            print("Runbook templates already seeded - skipping.")
            return
        await load_seed_templates(db, ORG_ID, USER_IDS["admin"])
        await db.commit()
        print("Runbook seed templates created.")


async def main():
    await seed()
    await seed_expansion()
    await seed_runbooks()
    if os.getenv("DEMO_MODE") == "true":
        from app.seed.scenarios.saas import seed as seed_saas
        from app.seed.scenarios.finserv import seed as seed_finserv
        from app.seed.scenarios.defense import seed as seed_defense
        async with AsyncSessionLocal() as db:
            await seed_saas(db)
            await seed_finserv(db)
            await seed_defense(db)
        from app.seed.seed_asset_graph import seed_asset_graph
        async with AsyncSessionLocal() as db:
            await seed_asset_graph(db)
    else:
        print("DEMO_MODE not set — skipping demo org seeds.")

if __name__ == "__main__":
    asyncio.run(main())

