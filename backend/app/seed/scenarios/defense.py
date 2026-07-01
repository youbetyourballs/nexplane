# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Seed: Ironclad Systems Group — Defense / Regulated scenario."""
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

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")

USERS = {
    "admin":    uuid.UUID("00000000-0000-0004-0000-000000000010"),
    "operator": uuid.UUID("00000000-0000-0004-0000-000000000011"),
    "approver": uuid.UUID("00000000-0000-0004-0000-000000000012"),
    "auditor":  uuid.UUID("00000000-0000-0004-0000-000000000013"),
}

CONNECTORS = {
    "freeipa":     uuid.UUID("00000000-0000-0004-0002-000000000001"),
    "crowdstrike": uuid.UUID("00000000-0000-0004-0002-000000000002"),
    "sccm":        uuid.UUID("00000000-0000-0004-0002-000000000003"),
    "wazuh":       uuid.UUID("00000000-0000-0004-0002-000000000004"),
    "ansible":     uuid.UUID("00000000-0000-0004-0002-000000000005"),
    "bind_dns":    uuid.UUID("00000000-0000-0004-0002-000000000006"),
}

ASSETS = {
    "freeipa_idm":        uuid.UUID("00000000-0000-0004-0001-000000000001"),
    "enclave_01":         uuid.UUID("00000000-0000-0004-0001-000000000002"),
    "enclave_02":         uuid.UUID("00000000-0000-0004-0001-000000000003"),
    "enclave_03":         uuid.UUID("00000000-0000-0004-0001-000000000004"),
    "enclave_04":         uuid.UUID("00000000-0000-0004-0001-000000000005"),
    "enclave_05":         uuid.UUID("00000000-0000-0004-0001-000000000006"),
    "win_ws_01":          uuid.UUID("00000000-0000-0004-0001-000000000007"),
    "win_ws_02":          uuid.UUID("00000000-0000-0004-0001-000000000008"),
    "sccm_server":        uuid.UUID("00000000-0000-0004-0001-000000000009"),
    "wazuh_siem":         uuid.UUID("00000000-0000-0004-0001-000000000010"),
    "ansible_control":    uuid.UUID("00000000-0000-0004-0001-000000000011"),
    "bind_dns":           uuid.UUID("00000000-0000-0004-0001-000000000012"),
    "classified_01":      uuid.UUID("00000000-0000-0004-0001-000000000013"),
    "classified_02":      uuid.UUID("00000000-0000-0004-0001-000000000014"),
    "crowdstrike_fleet":  uuid.UUID("00000000-0000-0004-0001-000000000015"),
    "internal_ca":        uuid.UUID("00000000-0000-0004-0001-000000000016"),
    "jump_server":        uuid.UUID("00000000-0000-0004-0001-000000000017"),
}

CRS = {
    "kernel_enclave_01_02":    uuid.UUID("00000000-0000-0004-0003-000000000001"),
    "kernel_enclave_03_05":    uuid.UUID("00000000-0000-0004-0003-000000000002"),
    "seccomp_containers":      uuid.UUID("00000000-0000-0004-0003-000000000003"),
    "selinux_enforcing":       uuid.UUID("00000000-0000-0004-0003-000000000004"),
    "rotate_freeipa_admin":    uuid.UUID("00000000-0000-0004-0003-000000000005"),
    "cis_hardening_playbook":  uuid.UUID("00000000-0000-0004-0003-000000000006"),
    "patch_sccm_agent":        uuid.UUID("00000000-0000-0004-0003-000000000007"),
    "revoke_contractor_ldap":  uuid.UUID("00000000-0000-0004-0003-000000000008"),
    "enable_auditd":           uuid.UUID("00000000-0000-0004-0003-000000000009"),
    "apparmor_wazuh":          uuid.UUID("00000000-0000-0004-0003-000000000010"),
    "rotate_internal_ca":      uuid.UUID("00000000-0000-0004-0003-000000000011"),
    "kernel_module_signing":   uuid.UUID("00000000-0000-0004-0003-000000000012"),
    "bind_dnssec":             uuid.UUID("00000000-0000-0004-0003-000000000013"),
    "cmmc_attestation":        uuid.UUID("00000000-0000-0004-0003-000000000014"),
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
        print("Ironclad seed already exists - skipping.")
        return

    db.add(Organization(id=ORG_ID, name="Ironclad Systems Group"))

    db.add_all([
        User(id=USERS["admin"],    organization_id=ORG_ID, email="admin@ironclad.example",
             name="Col. David Park",  role=UserRole.admin,             hashed_password=hash_password("admin123")),
        User(id=USERS["operator"], organization_id=ORG_ID, email="operator@ironclad.example",
             name="Sarah Nguyen",     role=UserRole.security_operator, hashed_password=hash_password("admin123")),
        User(id=USERS["approver"], organization_id=ORG_ID, email="approver@ironclad.example",
             name="James Holloway",   role=UserRole.approver,          hashed_password=hash_password("admin123")),
        User(id=USERS["auditor"],  organization_id=ORG_ID, email="auditor@ironclad.example",
             name="Teresa Okafor",    role=UserRole.auditor,           hashed_password=hash_password("admin123")),
    ])

    db.add_all([
        Connector(id=CONNECTORS["freeipa"],     organization_id=ORG_ID, name="FreeIPA",    connector_type=ConnectorType.freeipa,     status=ConnectorStatus.active, scoped_permissions={"accounts": ["read", "disable", "enable", "reset"], "groups": ["read", "write"], "dns": ["read"]}),
        Connector(id=CONNECTORS["crowdstrike"], organization_id=ORG_ID, name="CrowdStrike", connector_type=ConnectorType.crowdstrike, status=ConnectorStatus.active, scoped_permissions={"hosts": ["read", "isolate"], "sensors": ["deploy", "update"], "rtr": ["execute"]}),
        Connector(id=CONNECTORS["sccm"],        organization_id=ORG_ID, name="SCCM",       connector_type=ConnectorType.sccm,        status=ConnectorStatus.active, scoped_permissions={"devices": ["read"], "patches": ["deploy", "verify"], "software": ["read"]}),
        Connector(id=CONNECTORS["wazuh"],       organization_id=ORG_ID, name="Wazuh",      connector_type=ConnectorType.wazuh,       status=ConnectorStatus.active, scoped_permissions={"agents": ["read"], "alerts": ["read"], "rules": ["read", "write"]}),
        Connector(id=CONNECTORS["ansible"],     organization_id=ORG_ID, name="Ansible",    connector_type=ConnectorType.ansible,     status=ConnectorStatus.active, scoped_permissions={"playbooks": ["execute"], "inventory": ["read", "write"], "facts": ["read"]}),
        Connector(id=CONNECTORS["bind_dns"],    organization_id=ORG_ID, name="BIND DNS",   connector_type=ConnectorType.bind_dns,    status=ConnectorStatus.active, scoped_permissions={"zones": ["read", "write"], "records": ["read", "write"], "dnssec": ["sign", "verify"]}),
    ])

    db.add_all([
        Asset(id=ASSETS["freeipa_idm"],      organization_id=ORG_ID, name="freeipa-idm-01",       asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Identity and DNS authority for the enclave — all auth flows through this", "depends_on": [], "os": "rhel-9.3", "cmmc_controls": ["AC.1.001", "IA.1.076"]}),
        Asset(id=ASSETS["enclave_01"],       organization_id=ORG_ID, name="enclave-server-01",     asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Primary CUI processing workload", "depends_on": ["freeipa-idm-01"], "os": "rhel-9.3", "kernel": "5.14.0", "classification_level": "CUI", "air_gapped": True, "cmmc_controls": ["SC.3.177", "SI.2.216"]}),
        Asset(id=ASSETS["enclave_02"],       organization_id=ORG_ID, name="enclave-server-02",     asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Secondary CUI processing — failover", "depends_on": ["enclave-server-01"], "os": "rhel-9.3", "kernel": "5.14.0", "classification_level": "CUI", "air_gapped": True}),
        Asset(id=ASSETS["enclave_03"],       organization_id=ORG_ID, name="enclave-server-03",     asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Internal tooling and automation for enclave ops", "depends_on": [], "os": "rhel-9.3", "kernel": "5.14.0"}),
        Asset(id=ASSETS["enclave_04"],       organization_id=ORG_ID, name="enclave-server-04",     asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Log aggregation and SIEM collection node", "depends_on": [], "os": "rhel-9.3", "kernel": "5.14.0"}),
        Asset(id=ASSETS["enclave_05"],       organization_id=ORG_ID, name="enclave-server-05",     asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Backup and data integrity verification node", "depends_on": [], "os": "rhel-9.3", "kernel": "5.14.0"}),
        Asset(id=ASSETS["win_ws_01"],        organization_id=ORG_ID, name="win-workstation-01",    asset_type=AssetType.endpoint,       environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Analyst workstation with CUI access — must be SCCM-managed", "depends_on": [], "os": "windows-11-22h2", "cmmc_controls": ["AC.1.001"]}),
        Asset(id=ASSETS["win_ws_02"],        organization_id=ORG_ID, name="win-workstation-02",    asset_type=AssetType.endpoint,       environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Engineering workstation for system administration", "depends_on": [], "os": "windows-11-22h2"}),
        Asset(id=ASSETS["sccm_server"],      organization_id=ORG_ID, name="sccm-server",           asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Patch management authority for all Windows endpoints in enclave", "depends_on": []}),
        Asset(id=ASSETS["wazuh_siem"],       organization_id=ORG_ID, name="wazuh-siem",            asset_type=AssetType.application,    environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "security-team", "why_exists": "Security event correlation and alert generation for CMMC audit logging", "depends_on": ["enclave-server-04"], "cmmc_controls": ["AU.2.041", "AU.2.042"]}),
        Asset(id=ASSETS["ansible_control"],  organization_id=ORG_ID, name="ansible-control",       asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Automation control node for configuration management and hardening playbooks", "depends_on": []}),
        Asset(id=ASSETS["bind_dns"],         organization_id=ORG_ID, name="bind-dns-internal",     asset_type=AssetType.dns_zone,       environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Authoritative DNS for ironclad.local — no external resolution", "depends_on": [], "provider": "bind", "records": 47}),
        Asset(id=ASSETS["classified_01"],    organization_id=ORG_ID, name="classified-storage-01", asset_type=AssetType.storage_bucket, environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "data-team", "why_exists": "Primary CUI data store — encrypted at rest, access logged", "depends_on": [], "classification_level": "CUI", "cmmc_controls": ["MP.2.119", "SC.3.177"]}),
        Asset(id=ASSETS["classified_02"],    organization_id=ORG_ID, name="classified-storage-02", asset_type=AssetType.storage_bucket, environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "data-team", "why_exists": "CUI backup store — air-gapped from primary", "depends_on": ["classified-storage-01"], "classification_level": "CUI", "air_gapped": True}),
        Asset(id=ASSETS["crowdstrike_fleet"],organization_id=ORG_ID, name="crowdstrike-fleet",      asset_type=AssetType.endpoint,       environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "security-team", "why_exists": "CrowdStrike sensor coverage across all RHEL hosts in enclave", "depends_on": [], "count": 5}),
        Asset(id=ASSETS["internal_ca"],      organization_id=ORG_ID, name="internal-ca",           asset_type=AssetType.application,    environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Internal certificate authority for all TLS and code signing in enclave", "depends_on": ["freeipa-idm-01"]}),
        Asset(id=ASSETS["jump_server"],      organization_id=ORG_ID, name="jump-server",           asset_type=AssetType.server,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Single ingress point for all administrative access to enclave — all sessions logged", "depends_on": ["freeipa-idm-01"], "cmmc_controls": ["AC.2.006", "AC.2.013"]}),
    ])

    # CR 1: Kernel upgrade enclave 01+02 — completed (dual approval)
    db.add(ChangeRequest(id=CRS["kernel_enclave_01_02"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Kernel upgrade enclave-server-01 and 02",
        description="Kernel 5.14 → 6.1. Dual approval: infra-lead + ISSO. Pre-change snapshot. Compliance evidence artifact attached.",
        change_type=ChangeType.ec2_stop_start, risk_level=RiskLevel.critical, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["enclave_01"]), str(ASSETS["enclave_02"])],
        desired_outcome={"instance_name": "enclave-server-01", "rollback_strategy": "stop_if_running"}))
    db.add(_plan(CRS["kernel_enclave_01_02"], [
        {"step_number": 1, "name": "Create Pre-Change Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Stop Instance",              "generic_action": "stop_instance",   "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Start Instance",             "generic_action": "start_instance",  "estimated_duration_seconds": 60},
    ]))
    db.add(_approval(CRS["kernel_enclave_01_02"], "Dual approval: infra-lead + ISSO. CMMC SI.2.216 compliance requirement."))

    # CR 2: Kernel upgrade enclave 03-05 — awaiting_approval
    db.add(ChangeRequest(id=CRS["kernel_enclave_03_05"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Kernel upgrade enclave-server-03, 04, 05",
        description="Same upgrade as phase 1. Dual approval required.",
        change_type=ChangeType.ec2_stop_start, risk_level=RiskLevel.high, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["enclave_03"]), str(ASSETS["enclave_04"]), str(ASSETS["enclave_05"])],
        desired_outcome={"instance_name": "enclave-server-03", "rollback_strategy": "stop_if_running"}))
    db.add(_plan(CRS["kernel_enclave_03_05"], [
        {"step_number": 1, "name": "Create Pre-Change Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Stop Instance",              "generic_action": "stop_instance",   "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Start Instance",             "generic_action": "start_instance",  "estimated_duration_seconds": 60},
    ]))

    # CR 3: Apply seccomp to containerized workloads — completed
    db.add(ChangeRequest(id=CRS["seccomp_containers"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Apply seccomp policy to containerized workloads",
        description="RuntimeDefault seccomp applied. Policy stored as compliance artifact. CMMC SC.3.177.",
        change_type=ChangeType.microsegmentation_policy, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["enclave_01"]), str(ASSETS["enclave_02"])],
        desired_outcome={"policy_rules": [{"profile": "RuntimeDefault", "scope": "all-containers"}], "critical_flows": [], "rollback_strategy": "remove_staged_policy"}))
    db.add(_plan(CRS["seccomp_containers"], [
        {"step_number": 1, "name": "Stage Seccomp Policy", "generic_action": "stage_policy",    "estimated_duration_seconds": 15},
        {"step_number": 2, "name": "Validate Staged",      "generic_action": "validate_staged", "estimated_duration_seconds": 20},
    ]))
    db.add(_approval(CRS["seccomp_containers"], "Seccomp policy approved. CMMC SC.3.177 evidence artifact generated."))

    # CR 4: Set SELinux enforcing on enclave-server-03 — planned
    db.add(ChangeRequest(id=CRS["selinux_enforcing"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Set SELinux enforcing mode on enclave-server-03",
        description="Currently permissive. Enforcing required for CMMC SI.2.216.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.high, status=ChangeRequestStatus.planned,
        target_asset_ids=[str(ASSETS["enclave_03"])],
        desired_outcome={"template_id": "set_selinux_enforcing", "parameters": {"host": "enclave-server-03"}}))
    db.add(_plan(CRS["selinux_enforcing"], [
        {"step_number": 1, "name": "Validate Playbook", "generic_action": "validate_template", "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Set SELinux Mode",  "generic_action": "execute_template",  "estimated_duration_seconds": 30},
    ]))

    # CR 5: Rotate FreeIPA admin credentials — completed
    db.add(ChangeRequest(id=CRS["rotate_freeipa_admin"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate FreeIPA admin credentials",
        description="Reconstitution rollback: break-glass procedure documented. Dual approval.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.critical, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["freeipa_idm"])],
        desired_outcome={"service": "freeipa-admin", "key_type": "password", "grace_period_hours": 0, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_freeipa_admin"], [
        {"step_number": 1, "name": "Generate New Credentials", "generic_action": "generate_key",    "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Distribute Credentials",   "generic_action": "distribute_key",  "estimated_duration_seconds": 15},
        {"step_number": 3, "name": "Revoke Old Credentials",   "generic_action": "schedule_revoke", "estimated_duration_seconds": 5},
    ]))
    db.add(_approval(CRS["rotate_freeipa_admin"], "Dual approval: infra-lead + ISSO. Break-glass procedure documented."))

    # CR 6: CIS Level 2 hardening playbook — awaiting_approval
    db.add(ChangeRequest(id=CRS["cis_hardening_playbook"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Ansible CIS Level 2 hardening playbook",
        description="Applies CIS RHEL 9 Level 2 benchmark across all enclave servers. ISSO approval required.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.high, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["enclave_01"]), str(ASSETS["enclave_02"]), str(ASSETS["enclave_03"]), str(ASSETS["enclave_04"]), str(ASSETS["enclave_05"])],
        desired_outcome={"template_id": "cis_rhel9_level2", "parameters": {"benchmark_version": "2.0.0"}}))
    db.add(_plan(CRS["cis_hardening_playbook"], [
        {"step_number": 1, "name": "Validate Playbook", "generic_action": "validate_template", "estimated_duration_seconds": 20},
        {"step_number": 2, "name": "Execute Playbook",  "generic_action": "execute_template",  "estimated_duration_seconds": 600},
        {"step_number": 3, "name": "Collect Output",    "generic_action": "collect_output",    "estimated_duration_seconds": 30},
    ]))

    # CR 7: Patch SCCM agent on Windows workstations — completed
    db.add(ChangeRequest(id=CRS["patch_sccm_agent"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Patch SCCM agent on Windows workstations",
        description="SCCM agent 5.0.9040 → 5.0.9106. Rollback: uninstall new agent.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["win_ws_01"]), str(ASSETS["win_ws_02"])],
        desired_outcome={"template_id": "update_sccm_agent", "parameters": {"target_version": "5.0.9106"}}))
    db.add(_plan(CRS["patch_sccm_agent"], [
        {"step_number": 1, "name": "Download Package", "generic_action": "download_package", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Install Agent",    "generic_action": "install_agent",    "estimated_duration_seconds": 120},
        {"step_number": 3, "name": "Start Service",    "generic_action": "start_service",    "estimated_duration_seconds": 30},
    ]))
    db.add(_approval(CRS["patch_sccm_agent"], "SCCM agent patch approved. Low risk."))

    # CR 8: Revoke departed contractor LDAP account — completed
    db.add(ChangeRequest(id=CRS["revoke_contractor_ldap"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Revoke departed contractor LDAP account",
        description="FreeIPA account disabled, Kerberos principal removed. CMMC AC.1.001.",
        change_type=ChangeType.dns_update, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["freeipa_idm"])],
        desired_outcome={"record_name": "contractor-bdavis", "record_type": "A", "new_value": "disabled", "ttl": 0, "rollback_strategy": "restore_previous_record"}))
    db.add(_plan(CRS["revoke_contractor_ldap"], [
        {"step_number": 1, "name": "Capture Account State", "generic_action": "capture_dns_record", "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Disable Account",       "generic_action": "update_dns_record",  "estimated_duration_seconds": 10},
    ]))
    db.add(_approval(CRS["revoke_contractor_ldap"], "Contract termination confirmed. CMMC AC.1.001 evidence archived."))

    # CR 9: Enable auditd rules — completed
    db.add(ChangeRequest(id=CRS["enable_auditd"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Enable auditd rules: privileged command logging",
        description="Auditd rules for sudo, su, ssh-keygen. CMMC AU.2.041.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["enclave_01"]), str(ASSETS["enclave_02"]), str(ASSETS["enclave_03"])],
        desired_outcome={"template_id": "configure_auditd", "parameters": {"rules": ["sudo", "su", "ssh-keygen"]}}))
    db.add(_plan(CRS["enable_auditd"], [
        {"step_number": 1, "name": "Validate Template",  "generic_action": "validate_template", "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Apply Auditd Rules", "generic_action": "execute_template",  "estimated_duration_seconds": 20},
    ]))
    db.add(_approval(CRS["enable_auditd"], "Auditd privileged command logging required for CMMC AU.2.041."))

    # CR 10: AppArmor for Wazuh agent — draft
    db.add(ChangeRequest(id=CRS["apparmor_wazuh"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Apply AppArmor profile to Wazuh agent",
        description="Restrict Wazuh agent binary to required paths and network access.",
        change_type=ChangeType.microsegmentation_policy, risk_level=RiskLevel.medium, status=ChangeRequestStatus.draft,
        target_asset_ids=[str(ASSETS["wazuh_siem"])],
        desired_outcome={"policy_rules": [{"process": "wazuh-agent", "profile": "apparmor-wazuh"}], "critical_flows": [], "rollback_strategy": "remove_staged_policy"}))

    # CR 11: Rotate internal CA — awaiting_approval
    db.add(ChangeRequest(id=CRS["rotate_internal_ca"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate internal CA",
        description="12-month rotation. Old CA kept valid 90 days post-rotation. Dual approval: infra-lead + ISSO.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.critical, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["internal_ca"])],
        desired_outcome={"service": "internal-ca", "key_type": "certificate", "grace_period_hours": 2160, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_internal_ca"], [
        {"step_number": 1, "name": "Generate New CA Certificate", "generic_action": "generate_key",    "estimated_duration_seconds": 30},
        {"step_number": 2, "name": "Distribute New CA",           "generic_action": "distribute_key",  "estimated_duration_seconds": 60},
        {"step_number": 3, "name": "Schedule Old CA Expiry",      "generic_action": "schedule_revoke", "estimated_duration_seconds": 5},
    ]))

    # CR 12: Kernel module signing — draft
    db.add(ChangeRequest(id=CRS["kernel_module_signing"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Deploy kernel module signing enforcement",
        description="Enforce UEFI Secure Boot + module signing on all enclave servers.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.high, status=ChangeRequestStatus.draft,
        target_asset_ids=[str(ASSETS["enclave_01"]), str(ASSETS["enclave_02"]), str(ASSETS["enclave_03"]), str(ASSETS["enclave_04"]), str(ASSETS["enclave_05"])],
        desired_outcome={"template_id": "enforce_kernel_module_signing", "parameters": {}}))

    # CR 13: BIND DNS DNSSEC signing — awaiting_approval
    db.add(ChangeRequest(id=CRS["bind_dnssec"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="BIND DNS DNSSEC signing",
        description="Enable DNSSEC for ironclad.local zone. Rollback: disable DNSSEC, revert SOA.",
        change_type=ChangeType.dns_update, risk_level=RiskLevel.medium, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["bind_dns"])],
        desired_outcome={"record_name": "ironclad.local", "record_type": "SOA", "new_value": "dnssec-enabled", "ttl": 3600, "rollback_strategy": "restore_previous_record"}))
    db.add(_plan(CRS["bind_dnssec"], [
        {"step_number": 1, "name": "Capture Zone SOA",      "generic_action": "capture_dns_record",   "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Enable DNSSEC Signing", "generic_action": "update_dns_record",    "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Wait Propagation",      "generic_action": "wait_dns_propagation", "estimated_duration_seconds": 60},
    ]))

    # CR 14: CMMC Level 2 attestation — completed
    db.add(ChangeRequest(id=CRS["cmmc_attestation"], organization_id=ORG_ID, requester_id=USERS["admin"],
        title="CMMC Level 2 compliance attestation CR",
        description="Verification of 110 practices. Evidence bundle attached as artifact. Assessment date logged.",
        change_type=ChangeType.snapshot_asset, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["enclave_01"]), str(ASSETS["enclave_02"]), str(ASSETS["freeipa_idm"])],
        desired_outcome={"snapshot_tag": "cmmc-level2-attestation-2026-q1", "rollback_strategy": "rollback_unavailable"}))
    db.add(_plan(CRS["cmmc_attestation"], [
        {"step_number": 1, "name": "Capture Compliance Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 30},
        {"step_number": 2, "name": "Verify Evidence Bundle",      "generic_action": "verify_snapshot", "estimated_duration_seconds": 10},
    ]))
    db.add(_approval(CRS["cmmc_attestation"], "110 CMMC Level 2 practices verified. Assessment date: 2026-03-15. Evidence archived."))

    await db.commit()
    print("Ironclad Systems Group seed created.")
