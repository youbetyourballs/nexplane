"""Seed: Meridian Capital Partners — Financial Services scenario."""
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

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")

USERS = {
    "admin":    uuid.UUID("00000000-0000-0003-0000-000000000010"),
    "operator": uuid.UUID("00000000-0000-0003-0000-000000000011"),
    "approver": uuid.UUID("00000000-0000-0003-0000-000000000012"),
    "auditor":  uuid.UUID("00000000-0000-0003-0000-000000000013"),
}

CONNECTORS = {
    "aws":              uuid.UUID("00000000-0000-0003-0002-000000000001"),
    "active_directory": uuid.UUID("00000000-0000-0003-0002-000000000002"),
    "crowdstrike":      uuid.UUID("00000000-0000-0003-0002-000000000003"),
    "okta":             uuid.UUID("00000000-0000-0003-0002-000000000004"),
    "hashicorp_vault":  uuid.UUID("00000000-0000-0003-0002-000000000005"),
    "paloalto":         uuid.UUID("00000000-0000-0003-0002-000000000006"),
    "tenable":          uuid.UUID("00000000-0000-0003-0002-000000000007"),
}

ASSETS = {
    "ad_dc_01":               uuid.UUID("00000000-0000-0003-0001-000000000001"),
    "ad_dc_02":               uuid.UUID("00000000-0000-0003-0001-000000000002"),
    "trading_01":             uuid.UUID("00000000-0000-0003-0001-000000000003"),
    "trading_02":             uuid.UUID("00000000-0000-0003-0001-000000000004"),
    "trading_03":             uuid.UUID("00000000-0000-0003-0001-000000000005"),
    "trading_04":             uuid.UUID("00000000-0000-0003-0001-000000000006"),
    "bloomberg_cluster":      uuid.UUID("00000000-0000-0003-0001-000000000007"),
    "market_data_feed":       uuid.UUID("00000000-0000-0003-0001-000000000008"),
    "trade_db":               uuid.UUID("00000000-0000-0003-0001-000000000009"),
    "paloalto_fw":            uuid.UUID("00000000-0000-0003-0001-000000000010"),
    "aws_audit_account":      uuid.UUID("00000000-0000-0003-0001-000000000011"),
    "okta_tenant":            uuid.UUID("00000000-0000-0003-0001-000000000012"),
    "hashicorp_vault":        uuid.UUID("00000000-0000-0003-0001-000000000013"),
    "privileged_ws_01":       uuid.UUID("00000000-0000-0003-0001-000000000014"),
    "privileged_ws_02":       uuid.UUID("00000000-0000-0003-0001-000000000015"),
    "tenable_scanner":        uuid.UUID("00000000-0000-0003-0001-000000000016"),
    "s3_audit_bucket":        uuid.UUID("00000000-0000-0003-0001-000000000017"),
    "meridian_dns":           uuid.UUID("00000000-0000-0003-0001-000000000018"),
}

CRS = {
    "rotate_vault_root":          uuid.UUID("00000000-0000-0003-0003-000000000001"),
    "patch_kernel_trading_01_02": uuid.UUID("00000000-0000-0003-0003-000000000002"),
    "disable_tls_old":            uuid.UUID("00000000-0000-0003-0003-000000000003"),
    "revoke_analyst_ad":          uuid.UUID("00000000-0000-0003-0003-000000000004"),
    "apparmor_market_data":       uuid.UUID("00000000-0000-0003-0003-000000000005"),
    "rotate_okta_token":          uuid.UUID("00000000-0000-0003-0003-000000000006"),
    "paloalto_bloomberg_rule":    uuid.UUID("00000000-0000-0003-0003-000000000007"),
    "crowdstrike_upgrade":        uuid.UUID("00000000-0000-0003-0003-000000000008"),
    "rotate_trade_db_creds":      uuid.UUID("00000000-0000-0003-0003-000000000009"),
    "isolate_ws01":               uuid.UUID("00000000-0000-0003-0003-000000000010"),
    "enforce_mfa_privileged":     uuid.UUID("00000000-0000-0003-0003-000000000011"),
    "tenable_scan_trading":       uuid.UUID("00000000-0000-0003-0003-000000000012"),
    "rotate_aws_cross_account":   uuid.UUID("00000000-0000-0003-0003-000000000013"),
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
        print("Meridian seed already exists - skipping.")
        return

    db.add(Organization(id=ORG_ID, name="Meridian Capital Partners"))

    db.add_all([
        User(id=USERS["admin"],    organization_id=ORG_ID, email="admin@meridian.example",
             name="Victoria Chen",  role=UserRole.admin,             hashed_password=hash_password("admin123")),
        User(id=USERS["operator"], organization_id=ORG_ID, email="operator@meridian.example",
             name="Marcus Webb",    role=UserRole.security_operator, hashed_password=hash_password("admin123")),
        User(id=USERS["approver"], organization_id=ORG_ID, email="approver@meridian.example",
             name="Elena Kovacs",   role=UserRole.approver,          hashed_password=hash_password("admin123")),
        User(id=USERS["auditor"],  organization_id=ORG_ID, email="auditor@meridian.example",
             name="Robert Osei",    role=UserRole.auditor,           hashed_password=hash_password("admin123")),
    ])

    db.add_all([
        Connector(id=CONNECTORS["aws"],              organization_id=ORG_ID, name="AWS Audit",        connector_type=ConnectorType.aws,              status=ConnectorStatus.active, scoped_permissions={"s3": ["read", "write"], "cloudtrail": ["read"], "iam": ["read"]}),
        Connector(id=CONNECTORS["active_directory"], organization_id=ORG_ID, name="Active Directory", connector_type=ConnectorType.active_directory,  status=ConnectorStatus.active, scoped_permissions={"accounts": ["disable", "enable", "reset"], "groups": ["read", "write"], "ldap": ["read"]}),
        Connector(id=CONNECTORS["crowdstrike"],      organization_id=ORG_ID, name="CrowdStrike",      connector_type=ConnectorType.crowdstrike,       status=ConnectorStatus.active, scoped_permissions={"hosts": ["read", "isolate", "restore"], "sensors": ["deploy", "remove"], "rtr": ["execute"]}),
        Connector(id=CONNECTORS["okta"],             organization_id=ORG_ID, name="Okta",             connector_type=ConnectorType.okta,              status=ConnectorStatus.active, scoped_permissions={"users": ["read", "suspend", "activate"], "groups": ["read", "write"], "mfa": ["read", "enforce"]}),
        Connector(id=CONNECTORS["hashicorp_vault"],  organization_id=ORG_ID, name="HashiCorp Vault",  connector_type=ConnectorType.hashicorp_vault,   status=ConnectorStatus.active, scoped_permissions={"secrets": ["read", "write", "rotate"], "auth": ["read"], "leases": ["revoke"]}),
        Connector(id=CONNECTORS["paloalto"],         organization_id=ORG_ID, name="Palo Alto",        connector_type=ConnectorType.paloalto,          status=ConnectorStatus.active, scoped_permissions={"policy": ["read", "stage", "commit"], "flows": ["read"], "zones": ["read"]}),
        Connector(id=CONNECTORS["tenable"],          organization_id=ORG_ID, name="Tenable",          connector_type=ConnectorType.tenable,           status=ConnectorStatus.active, scoped_permissions={"scans": ["read", "launch"], "assets": ["read"], "vulnerabilities": ["read"]}),
    ])

    db.add_all([
        Asset(id=ASSETS["ad_dc_01"],          organization_id=ORG_ID, name="ad-dc-01",               asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Primary AD domain controller for meridian.local", "depends_on": [], "os": "windows-server-2022", "role": "pdc"}),
        Asset(id=ASSETS["ad_dc_02"],          organization_id=ORG_ID, name="ad-dc-02",               asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Secondary AD domain controller for redundancy", "depends_on": ["ad-dc-01"], "os": "windows-server-2022", "role": "bdc"}),
        Asset(id=ASSETS["trading_01"],        organization_id=ORG_ID, name="trading-server-01",      asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "trading-ops", "why_exists": "Executes equity trading algorithms — primary", "depends_on": [], "os": "rhel-8.9", "kernel": "4.18.0", "sla_tier": "tier-0", "regulatory_scope": ["SEC17a-4"]}),
        Asset(id=ASSETS["trading_02"],        organization_id=ORG_ID, name="trading-server-02",      asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "trading-ops", "why_exists": "Executes equity trading algorithms — secondary failover", "depends_on": ["trading-server-01"], "os": "rhel-8.9", "kernel": "4.18.0", "sla_tier": "tier-0", "regulatory_scope": ["SEC17a-4"]}),
        Asset(id=ASSETS["trading_03"],        organization_id=ORG_ID, name="trading-server-03",      asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "trading-ops", "why_exists": "Runs risk calculation engine", "depends_on": [], "os": "rhel-8.9", "kernel": "4.18.0"}),
        Asset(id=ASSETS["trading_04"],        organization_id=ORG_ID, name="trading-server-04",      asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "trading-ops", "why_exists": "Backtesting and simulation workloads", "depends_on": [], "os": "rhel-8.9", "kernel": "4.18.0"}),
        Asset(id=ASSETS["bloomberg_cluster"], organization_id=ORG_ID, name="bloomberg-terminal-cluster", asset_type=AssetType.endpoint,    environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "trading-ops", "why_exists": "Bloomberg Terminal access for portfolio managers", "depends_on": ["trading-server-01"], "count": 8}),
        Asset(id=ASSETS["market_data_feed"],  organization_id=ORG_ID, name="market-data-feed",       asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "trading-ops", "why_exists": "Real-time market data ingestion from NYSE/NASDAQ feeds", "depends_on": ["trading-server-01", "trading-server-02"], "port": 8443, "protocol": "tcp"}),
        Asset(id=ASSETS["trade_db"],          organization_id=ORG_ID, name="trade-db",               asset_type=AssetType.database,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "data-eng", "why_exists": "Immutable trade ledger — primary system of record", "depends_on": ["trading-server-01"], "engine": "postgres-15", "regulatory_scope": ["SEC17a-4", "FINRA"]}),
        Asset(id=ASSETS["paloalto_fw"],       organization_id=ORG_ID, name="paloalto-fw-01",         asset_type=AssetType.firewall,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Perimeter firewall segmenting trading network from corp", "depends_on": [], "model": "PA-5250", "software": "10.2.7"}),
        Asset(id=ASSETS["aws_audit_account"], organization_id=ORG_ID, name="aws-audit-account",      asset_type=AssetType.cloud_account,    environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "compliance-team", "why_exists": "Isolated AWS account for immutable audit log storage — never runs workloads", "depends_on": [], "account_id": "987654321098"}),
        Asset(id=ASSETS["okta_tenant"],       organization_id=ORG_ID, name="okta-tenant",            asset_type=AssetType.identity_provider,environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "SSO and MFA for all corporate applications", "depends_on": ["ad-dc-01"]}),
        Asset(id=ASSETS["hashicorp_vault"],   organization_id=ORG_ID, name="hashicorp-vault",        asset_type=AssetType.application,      environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Secrets management and credential rotation for all services", "depends_on": ["ad-dc-01"]}),
        Asset(id=ASSETS["privileged_ws_01"],  organization_id=ORG_ID, name="privileged-ws-01",       asset_type=AssetType.endpoint,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Jump workstation for privileged AD and infrastructure access", "depends_on": [], "data_classification": "restricted"}),
        Asset(id=ASSETS["privileged_ws_02"],  organization_id=ORG_ID, name="privileged-ws-02",       asset_type=AssetType.endpoint,         environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Secondary privileged workstation", "depends_on": [], "data_classification": "restricted"}),
        Asset(id=ASSETS["tenable_scanner"],   organization_id=ORG_ID, name="tenable-scanner",        asset_type=AssetType.server,           environment=Environment.prod, criticality=Criticality.medium,   asset_metadata={"owner": "security-team", "why_exists": "Credentialed vulnerability scanning of on-prem fleet", "depends_on": []}),
        Asset(id=ASSETS["s3_audit_bucket"],   organization_id=ORG_ID, name="s3-audit-bucket",        asset_type=AssetType.storage_bucket,   environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "compliance-team", "why_exists": "Immutable CloudTrail and AD event log archive — 7-year retention", "depends_on": [], "regulatory_scope": ["SEC17a-4"]}),
        Asset(id=ASSETS["meridian_dns"],      organization_id=ORG_ID, name="meridian-dns-zone",      asset_type=AssetType.dns_zone,         environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Internal DNS for meridian.local and trading network", "depends_on": [], "provider": "bind_dns"}),
    ])

    # CR 1: Rotate Vault root token — completed (dual approval)
    db.add(ChangeRequest(id=CRS["rotate_vault_root"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate HashiCorp Vault root token",
        description="Reconstitution rollback: vault unseal keys stored offline. Dual approval required.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.critical, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["hashicorp_vault"])],
        desired_outcome={"service": "hashicorp-vault", "key_type": "root_token", "grace_period_hours": 0, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_vault_root"], [
        {"step_number": 1, "name": "Generate New Token",  "generic_action": "generate_key",    "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Distribute Token",    "generic_action": "distribute_key",  "estimated_duration_seconds": 15},
        {"step_number": 3, "name": "Revoke Old Token",    "generic_action": "schedule_revoke", "estimated_duration_seconds": 5},
    ]))
    db.add(_approval(CRS["rotate_vault_root"], "Dual approval: trading-ops + CISO confirmed. Vault rotation approved."))

    # CR 2: Kernel patch trading servers 01+02 — awaiting_approval
    db.add(ChangeRequest(id=CRS["patch_kernel_trading_01_02"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Patch Linux kernel on trading servers 01 and 02",
        description="Requires maintenance window Sat 02:00–04:00 EST. Dual approval: trading-ops + CISO.",
        change_type=ChangeType.ec2_stop_start, risk_level=RiskLevel.critical, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["trading_01"]), str(ASSETS["trading_02"])],
        desired_outcome={"instance_name": "trading-server-01", "rollback_strategy": "stop_if_running"}))
    db.add(_plan(CRS["patch_kernel_trading_01_02"], [
        {"step_number": 1, "name": "Create Pre-Change Snapshot", "generic_action": "create_snapshot", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Stop Instance",              "generic_action": "stop_instance",   "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Start Instance",             "generic_action": "start_instance",  "estimated_duration_seconds": 60},
    ]))

    # CR 3: Disable TLS 1.0/1.1 — completed
    db.add(ChangeRequest(id=CRS["disable_tls_old"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Disable TLS 1.0/1.1 on all endpoints",
        description="Enforced via AD group policy. Rollback: GPO revert.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["ad_dc_01"])],
        desired_outcome={"template_id": "apply_gpo", "parameters": {"policy": "disable_tls_legacy"}}))
    db.add(_plan(CRS["disable_tls_old"], [
        {"step_number": 1, "name": "Validate Template", "generic_action": "validate_template", "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Apply GPO",         "generic_action": "execute_template",  "estimated_duration_seconds": 30},
    ]))
    db.add(_approval(CRS["disable_tls_old"], "TLS 1.0/1.1 disable approved. Required for PCI DSS compliance."))

    # CR 4: Revoke terminated analyst AD account — completed
    db.add(ChangeRequest(id=CRS["revoke_analyst_ad"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Revoke terminated analyst AD account",
        description="Account disabled, groups removed, access review evidence attached.",
        change_type=ChangeType.dns_update, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["ad_dc_01"])],
        desired_outcome={"record_name": "analyst-jsmith", "record_type": "A", "new_value": "disabled", "ttl": 0, "rollback_strategy": "restore_previous_record"}))
    db.add(_plan(CRS["revoke_analyst_ad"], [
        {"step_number": 1, "name": "Capture Account State", "generic_action": "capture_dns_record", "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Disable Account",       "generic_action": "update_dns_record",  "estimated_duration_seconds": 10},
    ]))
    db.add(_approval(CRS["revoke_analyst_ad"], "HR termination confirmed. Access revocation approved."))

    # CR 5: AppArmor for market data feed — draft
    db.add(ChangeRequest(id=CRS["apparmor_market_data"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Apply AppArmor policy to market data feed process",
        description="Restrict market-data-feed binary to required syscalls and file paths only.",
        change_type=ChangeType.microsegmentation_policy, risk_level=RiskLevel.high, status=ChangeRequestStatus.draft,
        target_asset_ids=[str(ASSETS["market_data_feed"])],
        desired_outcome={"policy_rules": [{"process": "market-data-feed", "profile": "apparmor-strict"}], "critical_flows": [{"name": "feed-ingress", "port": 8443}], "rollback_strategy": "remove_staged_policy"}))

    # CR 6: Rotate Okta API token — completed
    db.add(ChangeRequest(id=CRS["rotate_okta_token"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate Okta API token for SIEM integration",
        description="Old token revoked after 24h grace period.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["okta_tenant"])],
        desired_outcome={"service": "okta", "key_type": "api_token", "grace_period_hours": 24, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_okta_token"], [
        {"step_number": 1, "name": "Generate New Token",  "generic_action": "generate_key",    "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Distribute Token",    "generic_action": "distribute_key",  "estimated_duration_seconds": 15},
        {"step_number": 3, "name": "Schedule Old Revoke", "generic_action": "schedule_revoke", "estimated_duration_seconds": 5},
    ]))
    db.add(_approval(CRS["rotate_okta_token"], "90-day rotation policy. Approved."))

    # CR 7: Palo Alto Bloomberg rule — awaiting_approval
    db.add(ChangeRequest(id=CRS["paloalto_bloomberg_rule"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Add Palo Alto rule: Bloomberg → market data feed TCP 8443",
        description="New Bloomberg terminal cluster needs access to market-data-feed port 8443. CISO sign-off required.",
        change_type=ChangeType.security_group_update, risk_level=RiskLevel.high, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["paloalto_fw"])],
        desired_outcome={"group_id": "paloalto-trading-zone", "rules": [{"src": "bloomberg-cluster", "dst": "market-data-feed", "port": 8443, "action": "allow"}]}))
    db.add(_plan(CRS["paloalto_bloomberg_rule"], [
        {"step_number": 1, "name": "Export Current Rules",  "generic_action": "export_security_group",   "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Validate New Rule",     "generic_action": "validate_security_rules", "estimated_duration_seconds": 10},
        {"step_number": 3, "name": "Stage Rule",            "generic_action": "update_security_group",   "estimated_duration_seconds": 15},
    ]))

    # CR 8: CrowdStrike sensor upgrade — planned
    db.add(ChangeRequest(id=CRS["crowdstrike_upgrade"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="CrowdStrike sensor upgrade on trading servers",
        description="Sensor v7.10 → v7.15. Maintenance window: next Saturday.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.medium, status=ChangeRequestStatus.planned,
        target_asset_ids=[str(ASSETS["trading_01"]), str(ASSETS["trading_02"]), str(ASSETS["trading_03"]), str(ASSETS["trading_04"])],
        desired_outcome={"template_id": "upgrade_crowdstrike_sensor", "parameters": {"target_version": "7.15"}}))
    db.add(_plan(CRS["crowdstrike_upgrade"], [
        {"step_number": 1, "name": "Download Package",  "generic_action": "download_package", "estimated_duration_seconds": 60},
        {"step_number": 2, "name": "Install Agent",     "generic_action": "install_agent",    "estimated_duration_seconds": 120},
        {"step_number": 3, "name": "Start Service",     "generic_action": "start_service",    "estimated_duration_seconds": 30},
    ]))

    # CR 9: Rotate trade DB credentials — completed
    db.add(ChangeRequest(id=CRS["rotate_trade_db_creds"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate PostgreSQL trade DB credentials",
        description="Master password and application service account rotated. Reconstitution rollback documented.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.critical, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["trade_db"])],
        desired_outcome={"service": "postgres-trade-db", "key_type": "password", "grace_period_hours": 1, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_trade_db_creds"], [
        {"step_number": 1, "name": "Generate New Credentials", "generic_action": "generate_key",    "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Distribute Credentials",   "generic_action": "distribute_key",  "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Schedule Old Revoke",      "generic_action": "schedule_revoke", "estimated_duration_seconds": 5},
    ]))
    db.add(_approval(CRS["rotate_trade_db_creds"], "Dual approval: data-eng lead + CISO. Trade DB rotation approved."))

    # CR 10: Emergency isolate compromised ws-01 — rolled_back after forensics
    db.add(ChangeRequest(id=CRS["isolate_ws01"], organization_id=ORG_ID, requester_id=USERS["admin"],
        title="Emergency: isolate compromised privileged-ws-01",
        description="CrowdStrike network isolation triggered after suspicious lateral movement. Isolation lifted after forensics completed.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.critical, status=ChangeRequestStatus.rolled_back,
        target_asset_ids=[str(ASSETS["privileged_ws_01"])],
        desired_outcome={"template_id": "crowdstrike_isolate_host", "parameters": {"host_id": "privileged-ws-01"}}))
    db.add(_plan(CRS["isolate_ws01"], [
        {"step_number": 1, "name": "Validate Template",  "generic_action": "validate_template", "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Isolate Host",       "generic_action": "execute_template",  "estimated_duration_seconds": 10},
    ]))
    db.add(_approval(CRS["isolate_ws01"], "Emergency isolation approved by CISO. Forensics complete — isolation lifted via rollback."))

    # CR 11: Enforce MFA on privileged AD accounts — completed
    db.add(ChangeRequest(id=CRS["enforce_mfa_privileged"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Enforce MFA on all privileged AD accounts",
        description="Okta MFA enforced for all users with AD admin role.",
        change_type=ChangeType.remote_command, risk_level=RiskLevel.medium, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["okta_tenant"]), str(ASSETS["ad_dc_01"])],
        desired_outcome={"template_id": "enforce_okta_mfa", "parameters": {"target_group": "ad-admins"}}))
    db.add(_plan(CRS["enforce_mfa_privileged"], [
        {"step_number": 1, "name": "Validate Template", "generic_action": "validate_template", "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Enforce MFA Policy","generic_action": "execute_template",  "estimated_duration_seconds": 20},
    ]))
    db.add(_approval(CRS["enforce_mfa_privileged"], "MFA enforcement for privileged accounts approved. Required for SOX."))

    # CR 12: Tenable credentialed scan — completed
    db.add(ChangeRequest(id=CRS["tenable_scan_trading"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Tenable credentialed scan: trading server fleet",
        description="4 criticals found, 2 remediated.",
        change_type=ChangeType.snapshot_asset, risk_level=RiskLevel.low, status=ChangeRequestStatus.completed,
        target_asset_ids=[str(ASSETS["tenable_scanner"])],
        desired_outcome={"snapshot_tag": "tenable-scan-trading-q2", "rollback_strategy": "rollback_unavailable"}))
    db.add(_plan(CRS["tenable_scan_trading"], [
        {"step_number": 1, "name": "Capture Scan Baseline", "generic_action": "create_snapshot", "estimated_duration_seconds": 30},
        {"step_number": 2, "name": "Verify Scan Complete",  "generic_action": "verify_snapshot", "estimated_duration_seconds": 10},
    ]))
    db.add(_approval(CRS["tenable_scan_trading"], "Quarterly credentialed scan approved."))

    # CR 13: Rotate AWS cross-account role — awaiting_approval
    db.add(ChangeRequest(id=CRS["rotate_aws_cross_account"], organization_id=ORG_ID, requester_id=USERS["operator"],
        title="Rotate AWS cross-account role for audit logging",
        description="90-day rotation policy. Role used by compliance team only.",
        change_type=ChangeType.key_rotation, risk_level=RiskLevel.medium, status=ChangeRequestStatus.awaiting_approval,
        target_asset_ids=[str(ASSETS["aws_audit_account"])],
        desired_outcome={"service": "aws-cross-account-role", "key_type": "iam_role", "grace_period_hours": 24, "rollback_strategy": "cancel_revocation"}))
    db.add(_plan(CRS["rotate_aws_cross_account"], [
        {"step_number": 1, "name": "Generate New Role Credentials", "generic_action": "generate_key",   "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Distribute Credentials",        "generic_action": "distribute_key", "estimated_duration_seconds": 15},
        {"step_number": 3, "name": "Schedule Old Revoke",           "generic_action": "schedule_revoke","estimated_duration_seconds": 5},
    ]))

    await db.commit()
    print("Meridian Capital Partners seed created.")
