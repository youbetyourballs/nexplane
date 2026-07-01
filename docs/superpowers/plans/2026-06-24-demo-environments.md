# Demo Environments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed three industry-specific demo organizations (SaaS, FinServ, Defense) into Nexplane and add a sidebar org-switcher that silently re-authenticates without logging out.

**Architecture:** Three idempotent seed modules under `backend/app/seed/scenarios/` each export a single `async def seed(db)` function called from `backend/seed.py`. A DEMO_MODE-gated `GET /demo/orgs` endpoint feeds a `DemoOrgSwitcher` React component rendered in the sidebar.

**Tech Stack:** Python/SQLAlchemy async ORM, FastAPI, React/TypeScript, lucide-react

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `backend/app/seed/scenarios/__init__.py` | Package marker |
| Create | `backend/app/seed/scenarios/saas.py` | CloudRun Technologies seed (6 connectors, 16 assets, 12 CRs) |
| Create | `backend/app/seed/scenarios/finserv.py` | Meridian Capital Partners seed (7 connectors, 18 assets, 13 CRs) |
| Create | `backend/app/seed/scenarios/defense.py` | Ironclad Systems Group seed (6 connectors, 17 assets, 14 CRs) |
| Modify | `backend/seed.py` | Call three scenario seeds in `main()` |
| Create | `backend/app/routers/demo.py` | `GET /demo/orgs` endpoint |
| Modify | `backend/app/main.py` | Conditionally mount demo router when DEMO_MODE=true |
| Modify | `docker-compose.yml` | Add `DEMO_MODE: "true"` to backend env |
| Create | `frontend/src/components/DemoOrgSwitcher.tsx` | Org dropdown — silent re-auth on change |
| Modify | `frontend/src/components/Sidebar.tsx` | Render DemoOrgSwitcher below nav |

---

## UUID Scheme

Org segment (position 3 in UUID) encodes the org:
- Acme: `00000000-0000-0000-xxxx-xxxxxxxxxxxx` (position 3 = 0000)
- CloudRun: position 3 = **0002**
- Meridian: position 3 = **0003**
- Ironclad: position 3 = **0004**

Sub-type uses position 4 prefix: `0000`=users, `0001`=assets, `0002`=connectors, `0003`=CRs

---

## Task 1: Scaffold seed scenarios directory

**Files:**
- Create: `backend/app/seed/scenarios/__init__.py`

- [ ] **Step 1: Create the package marker**

```python
# backend/app/seed/scenarios/__init__.py
```

(empty file)

- [ ] **Step 2: Verify directory structure**

Run: `ls backend/app/seed/scenarios/`
Expected: `__init__.py`

- [ ] **Step 3: Commit**

```bash
git add backend/app/seed/scenarios/__init__.py
git commit -m "chore: scaffold seed scenarios package"
```

---

## Task 2: CloudRun Technologies seed (saas.py)

**Files:**
- Create: `backend/app/seed/scenarios/saas.py`

- [ ] **Step 1: Write saas.py**

```python
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
from app.models.audit_event import AuditEvent
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
```

- [ ] **Step 2: Verify the file was written**

Run: `python -c "import ast; ast.parse(open('backend/app/seed/scenarios/saas.py').read()); print('syntax ok')"` from the nexplane root.
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/seed/scenarios/saas.py
git commit -m "feat(seed): add CloudRun Technologies demo scenario"
```

---

## Task 3: Meridian Capital Partners seed (finserv.py)

**Files:**
- Create: `backend/app/seed/scenarios/finserv.py`

- [ ] **Step 1: Write finserv.py**

```python
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
    "rotate_vault_root":         uuid.UUID("00000000-0000-0003-0003-000000000001"),
    "patch_kernel_trading_01_02":uuid.UUID("00000000-0000-0003-0003-000000000002"),
    "disable_tls_old":           uuid.UUID("00000000-0000-0003-0003-000000000003"),
    "revoke_analyst_ad":         uuid.UUID("00000000-0000-0003-0003-000000000004"),
    "apparmor_market_data":      uuid.UUID("00000000-0000-0003-0003-000000000005"),
    "rotate_okta_token":         uuid.UUID("00000000-0000-0003-0003-000000000006"),
    "paloalto_bloomberg_rule":   uuid.UUID("00000000-0000-0003-0003-000000000007"),
    "crowdstrike_upgrade":       uuid.UUID("00000000-0000-0003-0003-000000000008"),
    "rotate_trade_db_creds":     uuid.UUID("00000000-0000-0003-0003-000000000009"),
    "isolate_ws01":              uuid.UUID("00000000-0000-0003-0003-000000000010"),
    "enforce_mfa_privileged":    uuid.UUID("00000000-0000-0003-0003-000000000011"),
    "tenable_scan_trading":      uuid.UUID("00000000-0000-0003-0003-000000000012"),
    "rotate_aws_cross_account":  uuid.UUID("00000000-0000-0003-0003-000000000013"),
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

    # CR 10: Emergency isolate compromised ws-01 — completed (was rolled_back after forensics)
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/seed/scenarios/finserv.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/seed/scenarios/finserv.py
git commit -m "feat(seed): add Meridian Capital Partners demo scenario"
```

---

## Task 4: Ironclad Systems Group seed (defense.py)

**Files:**
- Create: `backend/app/seed/scenarios/defense.py`

- [ ] **Step 1: Write defense.py**

```python
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
    "freeipa":    uuid.UUID("00000000-0000-0004-0002-000000000001"),
    "crowdstrike":uuid.UUID("00000000-0000-0004-0002-000000000002"),
    "sccm":       uuid.UUID("00000000-0000-0004-0002-000000000003"),
    "wazuh":      uuid.UUID("00000000-0000-0004-0002-000000000004"),
    "ansible":    uuid.UUID("00000000-0000-0004-0002-000000000005"),
    "bind_dns":   uuid.UUID("00000000-0000-0004-0002-000000000006"),
}

ASSETS = {
    "freeipa_idm":       uuid.UUID("00000000-0000-0004-0001-000000000001"),
    "enclave_01":        uuid.UUID("00000000-0000-0004-0001-000000000002"),
    "enclave_02":        uuid.UUID("00000000-0000-0004-0001-000000000003"),
    "enclave_03":        uuid.UUID("00000000-0000-0004-0001-000000000004"),
    "enclave_04":        uuid.UUID("00000000-0000-0004-0001-000000000005"),
    "enclave_05":        uuid.UUID("00000000-0000-0004-0001-000000000006"),
    "win_ws_01":         uuid.UUID("00000000-0000-0004-0001-000000000007"),
    "win_ws_02":         uuid.UUID("00000000-0000-0004-0001-000000000008"),
    "sccm_server":       uuid.UUID("00000000-0000-0004-0001-000000000009"),
    "wazuh_siem":        uuid.UUID("00000000-0000-0004-0001-000000000010"),
    "ansible_control":   uuid.UUID("00000000-0000-0004-0001-000000000011"),
    "bind_dns":          uuid.UUID("00000000-0000-0004-0001-000000000012"),
    "classified_01":     uuid.UUID("00000000-0000-0004-0001-000000000013"),
    "classified_02":     uuid.UUID("00000000-0000-0004-0001-000000000014"),
    "crowdstrike_fleet": uuid.UUID("00000000-0000-0004-0001-000000000015"),
    "internal_ca":       uuid.UUID("00000000-0000-0004-0001-000000000016"),
    "jump_server":       uuid.UUID("00000000-0000-0004-0001-000000000017"),
}

CRS = {
    "kernel_enclave_01_02":   uuid.UUID("00000000-0000-0004-0003-000000000001"),
    "kernel_enclave_03_05":   uuid.UUID("00000000-0000-0004-0003-000000000002"),
    "seccomp_containers":     uuid.UUID("00000000-0000-0004-0003-000000000003"),
    "selinux_enforcing":      uuid.UUID("00000000-0000-0004-0003-000000000004"),
    "rotate_freeipa_admin":   uuid.UUID("00000000-0000-0004-0003-000000000005"),
    "cis_hardening_playbook": uuid.UUID("00000000-0000-0004-0003-000000000006"),
    "patch_sccm_agent":       uuid.UUID("00000000-0000-0004-0003-000000000007"),
    "revoke_contractor_ldap": uuid.UUID("00000000-0000-0004-0003-000000000008"),
    "enable_auditd":          uuid.UUID("00000000-0000-0004-0003-000000000009"),
    "apparmor_wazuh":         uuid.UUID("00000000-0000-0004-0003-000000000010"),
    "rotate_internal_ca":     uuid.UUID("00000000-0000-0004-0003-000000000011"),
    "kernel_module_signing":  uuid.UUID("00000000-0000-0004-0003-000000000012"),
    "bind_dnssec":            uuid.UUID("00000000-0000-0004-0003-000000000013"),
    "cmmc_attestation":       uuid.UUID("00000000-0000-0004-0003-000000000014"),
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
        Asset(id=ASSETS["freeipa_idm"],     organization_id=ORG_ID, name="freeipa-idm-01",       asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Identity and DNS authority for the enclave — all auth flows through this", "depends_on": [], "os": "rhel-9.3", "cmmc_controls": ["AC.1.001", "IA.1.076"]}),
        Asset(id=ASSETS["enclave_01"],      organization_id=ORG_ID, name="enclave-server-01",     asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Primary CUI processing workload", "depends_on": ["freeipa-idm-01"], "os": "rhel-9.3", "kernel": "5.14.0", "classification_level": "CUI", "air_gapped": True, "cmmc_controls": ["SC.3.177", "SI.2.216"]}),
        Asset(id=ASSETS["enclave_02"],      organization_id=ORG_ID, name="enclave-server-02",     asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Secondary CUI processing — failover", "depends_on": ["enclave-server-01"], "os": "rhel-9.3", "kernel": "5.14.0", "classification_level": "CUI", "air_gapped": True}),
        Asset(id=ASSETS["enclave_03"],      organization_id=ORG_ID, name="enclave-server-03",     asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Internal tooling and automation for enclave ops", "depends_on": [], "os": "rhel-9.3", "kernel": "5.14.0"}),
        Asset(id=ASSETS["enclave_04"],      organization_id=ORG_ID, name="enclave-server-04",     asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Log aggregation and SIEM collection node", "depends_on": [], "os": "rhel-9.3", "kernel": "5.14.0"}),
        Asset(id=ASSETS["enclave_05"],      organization_id=ORG_ID, name="enclave-server-05",     asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Backup and data integrity verification node", "depends_on": [], "os": "rhel-9.3", "kernel": "5.14.0"}),
        Asset(id=ASSETS["win_ws_01"],       organization_id=ORG_ID, name="win-workstation-01",    asset_type=AssetType.endpoint,      environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Analyst workstation with CUI access — must be SCCM-managed", "depends_on": [], "os": "windows-11-22h2", "cmmc_controls": ["AC.1.001"]}),
        Asset(id=ASSETS["win_ws_02"],       organization_id=ORG_ID, name="win-workstation-02",    asset_type=AssetType.endpoint,      environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Engineering workstation for system administration", "depends_on": [], "os": "windows-11-22h2"}),
        Asset(id=ASSETS["sccm_server"],     organization_id=ORG_ID, name="sccm-server",           asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Patch management authority for all Windows endpoints in enclave", "depends_on": []}),
        Asset(id=ASSETS["wazuh_siem"],      organization_id=ORG_ID, name="wazuh-siem",            asset_type=AssetType.application,   environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "security-team", "why_exists": "Security event correlation and alert generation for CMMC audit logging", "depends_on": ["enclave-server-04"], "cmmc_controls": ["AU.2.041", "AU.2.042"]}),
        Asset(id=ASSETS["ansible_control"], organization_id=ORG_ID, name="ansible-control",       asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Automation control node for configuration management and hardening playbooks", "depends_on": []}),
        Asset(id=ASSETS["bind_dns"],        organization_id=ORG_ID, name="bind-dns-internal",     asset_type=AssetType.dns_zone,      environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "infra-team", "why_exists": "Authoritative DNS for ironclad.local — no external resolution", "depends_on": [], "provider": "bind", "records": 47}),
        Asset(id=ASSETS["classified_01"],   organization_id=ORG_ID, name="classified-storage-01", asset_type=AssetType.storage_bucket,environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "data-team", "why_exists": "Primary CUI data store — encrypted at rest, access logged", "depends_on": [], "classification_level": "CUI", "cmmc_controls": ["MP.2.119", "SC.3.177"]}),
        Asset(id=ASSETS["classified_02"],   organization_id=ORG_ID, name="classified-storage-02", asset_type=AssetType.storage_bucket,environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "data-team", "why_exists": "CUI backup store — air-gapped from primary", "depends_on": ["classified-storage-01"], "classification_level": "CUI", "air_gapped": True}),
        Asset(id=ASSETS["crowdstrike_fleet"],organization_id=ORG_ID, name="crowdstrike-fleet",     asset_type=AssetType.endpoint,      environment=Environment.prod, criticality=Criticality.high,     asset_metadata={"owner": "security-team", "why_exists": "CrowdStrike sensor coverage across all RHEL hosts in enclave", "depends_on": [], "count": 5}),
        Asset(id=ASSETS["internal_ca"],     organization_id=ORG_ID, name="internal-ca",           asset_type=AssetType.application,   environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Internal certificate authority for all TLS and code signing in enclave", "depends_on": ["freeipa-idm-01"]}),
        Asset(id=ASSETS["jump_server"],     organization_id=ORG_ID, name="jump-server",           asset_type=AssetType.server,        environment=Environment.prod, criticality=Criticality.critical, asset_metadata={"owner": "infra-team", "why_exists": "Single ingress point for all administrative access to enclave — all sessions logged", "depends_on": ["freeipa-idm-01"], "cmmc_controls": ["AC.2.006", "AC.2.013"]}),
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
        {"step_number": 1, "name": "Stage Seccomp Policy",  "generic_action": "stage_policy",    "estimated_duration_seconds": 15},
        {"step_number": 2, "name": "Validate Staged",       "generic_action": "validate_staged", "estimated_duration_seconds": 20},
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
        {"step_number": 1, "name": "Generate New Credentials", "generic_action": "generate_key",   "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Distribute Credentials",   "generic_action": "distribute_key", "estimated_duration_seconds": 15},
        {"step_number": 3, "name": "Revoke Old Credentials",   "generic_action": "schedule_revoke","estimated_duration_seconds": 5},
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
        {"step_number": 1, "name": "Validate Playbook",   "generic_action": "validate_template",  "estimated_duration_seconds": 20},
        {"step_number": 2, "name": "Execute Playbook",    "generic_action": "execute_template",   "estimated_duration_seconds": 600},
        {"step_number": 3, "name": "Collect Output",      "generic_action": "collect_output",     "estimated_duration_seconds": 30},
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
        {"step_number": 1, "name": "Validate Template",   "generic_action": "validate_template", "estimated_duration_seconds": 10},
        {"step_number": 2, "name": "Apply Auditd Rules",  "generic_action": "execute_template",  "estimated_duration_seconds": 20},
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
        {"step_number": 1, "name": "Capture Zone SOA",     "generic_action": "capture_dns_record",   "estimated_duration_seconds": 5},
        {"step_number": 2, "name": "Enable DNSSEC Signing","generic_action": "update_dns_record",    "estimated_duration_seconds": 30},
        {"step_number": 3, "name": "Wait Propagation",     "generic_action": "wait_dns_propagation", "estimated_duration_seconds": 60},
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/seed/scenarios/defense.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/seed/scenarios/defense.py
git commit -m "feat(seed): add Ironclad Systems Group demo scenario"
```

---

## Task 5: Wire scenario seeds into backend/seed.py

**Files:**
- Modify: `backend/seed.py`

- [ ] **Step 1: Add scenario imports and calls to `main()`**

In `backend/seed.py`, replace the `main()` function:

```python
async def main():
    await seed()
    await seed_expansion()
    await seed_runbooks()
    from app.seed.scenarios.saas import seed as seed_saas
    from app.seed.scenarios.finserv import seed as seed_finserv
    from app.seed.scenarios.defense import seed as seed_defense
    async with AsyncSessionLocal() as db:
        await seed_saas(db)
        await seed_finserv(db)
        await seed_defense(db)
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/seed.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add backend/seed.py
git commit -m "feat(seed): wire demo scenario seeds into main()"
```

---

## Task 6: Backend demo router

**Files:**
- Create: `backend/app/routers/demo.py`

- [ ] **Step 1: Write demo.py**

```python
"""Demo org switcher endpoint — only active when DEMO_MODE=true."""
import os
from fastapi import APIRouter, HTTPException

router = APIRouter()

DEMO_ORGS = [
    {"id": "00000000-0000-0000-0000-000000000001", "name": "Acme Security Corp",        "slug": "acme",    "admin_email": "admin@acme.example",      "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000002", "name": "CloudRun Technologies",     "slug": "saas",    "admin_email": "admin@cloudrun.example",  "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000003", "name": "Meridian Capital Partners", "slug": "finserv", "admin_email": "admin@meridian.example",  "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000004", "name": "Ironclad Systems Group",    "slug": "defense", "admin_email": "admin@ironclad.example",  "admin_password": "admin123"},
]


@router.get("/orgs")
async def list_demo_orgs():
    if os.getenv("DEMO_MODE") != "true":
        raise HTTPException(status_code=404, detail="Not found")
    return DEMO_ORGS
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/routers/demo.py
git commit -m "feat(api): add GET /demo/orgs endpoint"
```

---

## Task 7: Mount demo router + add DEMO_MODE env var

**Files:**
- Modify: `backend/app/main.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add conditional router mount in main.py**

Find the block of `app.include_router(...)` calls near the end of `backend/app/main.py`. Add after the last `app.include_router(...)` call (before `app.mount("/mcp", ...)`):

```python
import os as _os
if _os.getenv("DEMO_MODE") == "true":
    from app.routers.demo import router as demo_router
    app.include_router(demo_router, prefix="/demo", tags=["demo"])
```

- [ ] **Step 2: Add DEMO_MODE to docker-compose.yml**

In `docker-compose.yml`, in the `backend` service's `environment:` section, add:

```yaml
      DEMO_MODE: "true"
```

alongside the existing env vars (`DATABASE_URL`, `SECRET_KEY`, etc.)

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py docker-compose.yml
git commit -m "feat(config): mount demo router when DEMO_MODE=true"
```

---

## Task 8: DemoOrgSwitcher frontend component

**Files:**
- Create: `frontend/src/components/DemoOrgSwitcher.tsx`

- [ ] **Step 1: Write DemoOrgSwitcher.tsx**

```tsx
import { useEffect, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';

interface DemoOrg {
  id: string;
  name: string;
  slug: string;
  admin_email: string;
  admin_password: string;
}

export function DemoOrgSwitcher() {
  const { user, login } = useAuth();
  const [orgs, setOrgs] = useState<DemoOrg[]>([]);

  useEffect(() => {
    fetch('/api/demo/orgs')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (data) setOrgs(data); })
      .catch(() => {});
  }, []);

  if (orgs.length === 0) return null;

  const currentOrgId = user?.organization_id ?? '';

  async function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const selected = orgs.find((o) => o.id === e.target.value);
    if (!selected || selected.id === currentOrgId) return;
    await login(selected.admin_email, selected.admin_password);
    window.location.reload();
  }

  return (
    <div className="px-3 py-2 border-t border-slate-700">
      <label className="block text-xs text-slate-400 mb-1">Demo org</label>
      <select
        value={currentOrgId}
        onChange={handleChange}
        className="w-full bg-slate-800 text-slate-200 text-sm rounded px-2 py-1 border border-slate-600 focus:outline-none focus:border-slate-400"
      >
        {orgs.map((org) => (
          <option key={org.id} value={org.id}>
            {org.name}
          </option>
        ))}
      </select>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/DemoOrgSwitcher.tsx
git commit -m "feat(ui): add DemoOrgSwitcher component"
```

---

## Task 9: Wire DemoOrgSwitcher into Sidebar

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Add import at top of Sidebar.tsx**

Add to the imports block:

```tsx
import { DemoOrgSwitcher } from './DemoOrgSwitcher';
```

- [ ] **Step 2: Render DemoOrgSwitcher**

In `Sidebar.tsx`, find the bottom strip section (where Notifications and Settings are rendered). Add `<DemoOrgSwitcher />` immediately above the bottom strip `<div>`:

```tsx
      <DemoOrgSwitcher />
      <div className="...">  {/* existing bottom strip */}
```

- [ ] **Step 3: Restart frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Then open http://100.101.186.39:3000 in browser. Verify:
- Demo org dropdown appears in sidebar
- Selecting "CloudRun Technologies" logs in and reloads to that org's data
- Selecting "Meridian Capital Partners" switches to financial services data
- Selecting "Ironclad Systems Group" switches to defense data
- Selecting "Acme Security Corp" returns to original org
- Re-running seed on populated DB prints "already exists - skipping" for all 4 orgs

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat(ui): render DemoOrgSwitcher in sidebar"
```

---

## Acceptance Criteria Checklist

- [ ] `backend/app/seed/scenarios/saas.py` seeds CloudRun org with 6 connectors, 16 assets, 12 CRs idempotently
- [ ] `backend/app/seed/scenarios/finserv.py` seeds Meridian org with 7 connectors, 18 assets, 13 CRs idempotently
- [ ] `backend/app/seed/scenarios/defense.py` seeds Ironclad org with 6 connectors, 17 assets, 14 CRs idempotently
- [ ] `backend/seed.py` calls all three scenario seeds in `main()`
- [ ] All CRs have appropriate status, risk level, and desired_outcome
- [ ] Completed CRs have at least one Approval record
- [ ] All assets have `owner`, `why_exists`, `depends_on` in metadata
- [ ] `GET /demo/orgs` returns 4 orgs when `DEMO_MODE=true`, 404 otherwise
- [ ] `DemoOrgSwitcher` renders in sidebar when demo orgs endpoint is available
- [ ] Switching org silently re-auths and reloads
- [ ] Container restart with fresh DB seeds all 4 orgs successfully
- [ ] Re-running seed on populated DB is a no-op (idempotent)
