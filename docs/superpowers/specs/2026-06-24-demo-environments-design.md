# Spec: Demo Environments — Three Seeded Scenarios

**Date:** 2026-06-24
**Sub-project:** 2 of 6 (Repositioning series)
**Status:** Approved for implementation

---

## Summary

Seed three cohesive demo organizations into the platform, each representing a distinct industry scenario with realistic assets, connectors, and change requests. Add a demo org switcher to the sidebar so users can switch between scenarios without logging out. All seed data is forward-compatible with the asset graph (sub-project 3) and infrastructure memory (sub-project 4) by including `why_exists`, `depends_on`, and provenance-oriented metadata fields.

---

## Architecture

### Approach: Separate seed modules + switcher (Option B/C hybrid)

**Seed modules:** Three new files under `backend/app/seed/scenarios/`:
- `saas.py` — CloudRun Technologies
- `finserv.py` — Meridian Capital Partners
- `defense.py` — Ironclad Systems Group

Each module exports a single async `seed(db)` function that is fully idempotent (skips if org UUID already exists). `backend/seed.py` calls all three after the existing Acme seed.

**Fixed UUIDs:** Each org uses a deterministic UUID for idempotency, same pattern as Acme:
- Acme: `00000000-0000-0000-0000-000000000001`
- CloudRun: `00000000-0000-0000-0000-000000000002`
- Meridian: `00000000-0000-0000-0000-000000000003`
- Ironclad: `00000000-0000-0000-0000-000000000004`

**User schema per org:** 4 users with org-specific email domains:
- `admin@<domain>.example` — role: admin
- `operator@<domain>.example` — role: operator
- `approver@<domain>.example` — role: approver
- `auditor@<domain>.example` — role: auditor

All demo user passwords: `admin123`

**Asset metadata shape:** Every asset includes at minimum:
```python
{
    "owner": "<team or person name>",
    "why_exists": "<one sentence rationale>",
    "depends_on": ["<asset name>", ...],   # forward-compat with asset graph
    # scenario-specific fields below
}
```

**Switcher:** `GET /demo/orgs` endpoint (DEMO_MODE-gated) + sidebar UI component.

---

## Scenario 1: CloudRun Technologies (SaaS)

**Org slug:** `saas` | **Domain:** `cloudrun.example`

**Industry context:** B2B SaaS company running on AWS + Kubernetes. Platform/infrastructure team is modernizing: containerizing legacy services, hardening K8s workloads, rotating credentials, and managing cert lifecycle.

### Connectors (6)

| Name | Type | Scoped Permissions |
|------|------|--------------------|
| AWS Production | aws | ec2: [describe, snapshot], iam: [rotate-keys], s3: [read, write] |
| Kubernetes Prod | kubernetes | workloads: [read, patch, restart], namespaces: [read], rbac: [read, write] |
| GitHub | github | repos: [read], workflows: [read, trigger], packages: [read, write] |
| Cloudflare | cloudflare | dns: [read, write], zones: [read], waf: [read, write] |
| Datadog | datadog | monitors: [read], dashboards: [read], metrics: [read] |
| Snyk | snyk | projects: [read], vulnerabilities: [read], reports: [read] |

### Assets (16)

| Name | Type | Environment | Criticality | Key Metadata |
|------|------|-------------|-------------|--------------|
| prod-k8s-cluster | kubernetes_cluster | prod | critical | owner: platform-eng, why_exists: "Runs all production microservices", depends_on: ["AWS Production account"] |
| app-server-01 | server | prod | critical | owner: platform-eng, why_exists: "Hosts payment service (pre-containerization)", os: ubuntu-22.04, kernel: 5.15.0-91 |
| app-server-02 | server | prod | critical | owner: platform-eng, why_exists: "Hosts auth service (pre-containerization)", os: ubuntu-22.04, kernel: 5.15.0-91 |
| app-server-03 | server | prod | high | owner: platform-eng, why_exists: "Hosts notification service", os: ubuntu-22.04, kernel: 5.15.0-91 |
| prod-postgres-rds | database | prod | critical | owner: data-eng, why_exists: "Primary transactional database for all product data", engine: postgres-15, size_gb: 500, depends_on: ["app-server-01", "app-server-02"] |
| prod-redis-cache | database | prod | high | owner: platform-eng, why_exists: "Session cache and rate limiting store", depends_on: ["prod-postgres-rds"] |
| cloudrun-dns-zone | dns_zone | prod | high | owner: platform-eng, why_exists: "authoritative DNS for cloudrun.io", provider: cloudflare, records: 24 |
| aws-prod-account | cloud_account | prod | critical | owner: platform-eng, why_exists: "Primary cloud account for all production workloads", account_id: "123456789012", region: us-east-1 |
| prod-load-balancer | load_balancer | prod | critical | owner: platform-eng, why_exists: "Terminates TLS and routes to K8s ingress", depends_on: ["prod-k8s-cluster"] |
| github-cloudrun-org | application | prod | high | owner: eng-leads, why_exists: "Source of truth for all application code and CI/CD pipelines" |
| payment-service-image | container_image | prod | critical | owner: payments-team, why_exists: "Container image for payment processing service", base_image: ubuntu:22.04, depends_on: ["prod-k8s-cluster"] |
| auth-service-image | container_image | prod | high | owner: platform-eng, why_exists: "Container image for authentication service", base_image: ubuntu:22.04 |
| api-service-image | container_image | prod | high | owner: platform-eng, why_exists: "Container image for public API gateway", base_image: ubuntu:22.04 |
| snyk-cloudrun-project | application | prod | medium | owner: security-eng, why_exists: "Tracks SCA vulnerabilities across all repos" |
| datadog-workspace | application | prod | medium | owner: platform-eng, why_exists: "Observability for prod infrastructure and services" |
| prod-s3-audit-bucket | storage_bucket | prod | high | owner: security-eng, why_exists: "Immutable audit log storage for CloudTrail and K8s audit logs" |

### Change Requests (12)

1. **Kernel upgrade: app-server-01 and app-server-02** — `ec2_stop_start` — completed. Kernel 5.15 → 6.1. Pre-change snapshot taken. Rollback available.
2. **Kernel upgrade: app-server-03** — `ec2_stop_start` — rolled_back. Node panic on first boot; rolled back to snapshot. Rollback executed successfully.
3. **Containerize payment service** — `tailscale_generate_auth_key` (placeholder type) — awaiting_approval. Move payment service from app-server-01 to prod-k8s-cluster. Blast radius: critical. Requires CTO approval.
4. **Apply seccomp-default profile to K8s workloads** — `security_group_update` (repurposed) — draft. Apply RuntimeDefault seccomp profile to all K8s pods in prod namespace.
5. **Rotate AWS IAM keys — CI/CD service account** — `key_rotation` — completed. Old key revoked 30 days after new key distributed.
6. **Enforce Pod Security Standards (restricted)** — `microsegmentation_policy` — planned. Enforce restricted PSS on prod namespace. Blocks privileged containers.
7. **Patch base image CVE: ubuntu:22.04** — `snapshot_asset` — awaiting_approval. Critical CVE in base image used by payment-service-image, auth-service-image, api-service-image. Linked to Snyk finding.
8. **Rotate PostgreSQL master credentials** — `key_rotation` — draft. Master password rotation for prod-postgres-rds. Reconstitution rollback: snapshot before rotation.
9. **Migrate API subdomain to new load balancer** — `dns_update` — completed. api.cloudrun.io CNAME updated. TTL-based rollback. Zero downtime.
10. **Enable K8s audit logging to S3** — `remote_command` — completed. Audit log stream to prod-s3-audit-bucket configured.
11. **Rotate wildcard TLS certificate** — `key_rotation` — awaiting_approval. *.cloudrun.io cert expires in 28 days.
12. **Add Cloudflare WAF rules: SQLi patterns** — `security_group_update` — completed. 3 new WAF rules deployed. Rollback: rule disable.

---

## Scenario 2: Meridian Capital Partners (Financial Services)

**Org slug:** `finserv` | **Domain:** `meridian.example`

**Industry context:** Hedge fund with AWS + on-prem hybrid. Strict change control with dual approval on anything touching trading infrastructure. Active IR playbook use, vault credential rotation, privileged access management.

### Connectors (7)

| Name | Type | Scoped Permissions |
|------|------|--------------------|
| AWS Audit | aws | s3: [read, write], cloudtrail: [read], iam: [read] |
| Active Directory | active_directory | accounts: [disable, enable, reset], groups: [read, write], ldap: [read] |
| CrowdStrike | crowdstrike | hosts: [read, isolate, restore], sensors: [deploy, remove], rtr: [execute] |
| Okta | okta | users: [read, suspend, activate], groups: [read, write], mfa: [read, enforce] |
| HashiCorp Vault | hashicorp_vault | secrets: [read, write, rotate], auth: [read], leases: [revoke] |
| Palo Alto | paloalto | policy: [read, stage, commit], flows: [read], zones: [read] |
| Tenable | tenable | scans: [read, launch], assets: [read], vulnerabilities: [read] |

### Assets (18)

| Name | Type | Environment | Criticality | Key Metadata |
|------|------|-------------|-------------|--------------|
| ad-dc-01 | server | prod | critical | owner: infra-team, why_exists: "Primary AD domain controller for meridian.local", os: windows-server-2022, role: pdc, depends_on: [] |
| ad-dc-02 | server | prod | critical | owner: infra-team, why_exists: "Secondary AD domain controller for redundancy", os: windows-server-2022, role: bdc, depends_on: ["ad-dc-01"] |
| trading-server-01 | server | prod | critical | owner: trading-ops, why_exists: "Executes equity trading algorithms — primary", os: rhel-8.9, kernel: 4.18.0, sla_tier: tier-0, regulatory_scope: ["SEC17a-4"] |
| trading-server-02 | server | prod | critical | owner: trading-ops, why_exists: "Executes equity trading algorithms — secondary failover", os: rhel-8.9, kernel: 4.18.0, sla_tier: tier-0, regulatory_scope: ["SEC17a-4"] |
| trading-server-03 | server | prod | high | owner: trading-ops, why_exists: "Runs risk calculation engine", os: rhel-8.9, kernel: 4.18.0 |
| trading-server-04 | server | prod | high | owner: trading-ops, why_exists: "Backtesting and simulation workloads", os: rhel-8.9, kernel: 4.18.0 |
| bloomberg-terminal-cluster | endpoint | prod | critical | owner: trading-ops, why_exists: "Bloomberg Terminal access for portfolio managers", count: 8, depends_on: ["trading-server-01"] |
| market-data-feed | server | prod | critical | owner: trading-ops, why_exists: "Real-time market data ingestion from NYSE/NASDAQ feeds", port: 8443, protocol: tcp, depends_on: ["trading-server-01", "trading-server-02"] |
| trade-db | database | prod | critical | owner: data-eng, why_exists: "Immutable trade ledger — primary system of record", engine: postgres-15, regulatory_scope: ["SEC17a-4", "FINRA"], depends_on: ["trading-server-01"] |
| paloalto-fw-01 | firewall | prod | critical | owner: infra-team, why_exists: "Perimeter firewall segmenting trading network from corp", model: PA-5250, software: 10.2.7 |
| aws-audit-account | cloud_account | prod | high | owner: compliance-team, why_exists: "Isolated AWS account for immutable audit log storage — never runs workloads", account_id: "987654321098" |
| okta-tenant | identity_provider | prod | critical | owner: infra-team, why_exists: "SSO and MFA for all corporate applications", depends_on: ["ad-dc-01"] |
| hashicorp-vault | application | prod | critical | owner: infra-team, why_exists: "Secrets management and credential rotation for all services", depends_on: ["ad-dc-01"] |
| privileged-ws-01 | endpoint | prod | critical | owner: infra-team, why_exists: "Jump workstation for privileged AD and infrastructure access — data_classification: restricted" |
| privileged-ws-02 | endpoint | prod | critical | owner: infra-team, why_exists: "Secondary privileged workstation", data_classification: restricted |
| tenable-scanner | server | prod | medium | owner: security-team, why_exists: "Credentialed vulnerability scanning of on-prem fleet" |
| s3-audit-bucket | storage_bucket | prod | critical | owner: compliance-team, why_exists: "Immutable CloudTrail and AD event log archive — 7-year retention", regulatory_scope: ["SEC17a-4"] |
| meridian-dns-zone | dns_zone | prod | high | owner: infra-team, why_exists: "Internal DNS for meridian.local and trading network", provider: bind_dns |

### Change Requests (13)

1. **Rotate HashiCorp Vault root token** — `key_rotation` — completed. Reconstitution rollback: vault unseal keys stored offline. Dual approval required.
2. **Patch Linux kernel on trading servers (01 and 02)** — `ec2_stop_start` — awaiting_approval. Requires maintenance window (Sat 02:00–04:00 EST). Dual approval: trading-ops + CISO.
3. **Disable TLS 1.0/1.1 on all endpoints** — `remote_command` — completed. Enforced via AD group policy. Rollback: GPO revert.
4. **Revoke terminated analyst AD account** — `dns_update` (placeholder) — completed. Account disabled, groups removed, access review evidence attached.
5. **Apply AppArmor policy to market data feed process** — `microsegmentation_policy` — draft. Restrict market-data-feed binary to required syscalls and file paths only.
6. **Rotate Okta API token for SIEM integration** — `key_rotation` — completed. Old token revoked after 24h grace period.
7. **Add Palo Alto rule: Bloomberg → market data feed TCP 8443** — `security_group_update` — awaiting_approval. New Bloomberg terminal cluster needs access to market-data-feed port 8443. CISO sign-off required.
8. **CrowdStrike sensor upgrade on trading servers** — `remote_command` — planned. Sensor v7.10 → v7.15. Maintenance window: next Saturday.
9. **Rotate PostgreSQL trade DB credentials** — `key_rotation` — completed. Master password and application service account rotated. Reconstitution rollback documented.
10. **Emergency: isolate compromised privileged-ws-01** — `remote_command` — completed. CrowdStrike network isolation triggered after suspicious lateral movement detected. Isolation lifted after forensics (rolled back).
11. **Enforce MFA on all privileged AD accounts** — `remote_command` — completed. Okta MFA enforced for all users with AD admin role.
12. **Tenable credentialed scan: trading server fleet** — `snapshot_asset` — completed. 4 criticals found, 2 remediated.
13. **Rotate AWS cross-account role for audit logging** — `key_rotation` — awaiting_approval. 90-day rotation policy. Role used by compliance team only.

---

## Scenario 3: Ironclad Systems Group (Defense / Regulated)

**Org slug:** `defense` | **Domain:** `ironclad.example`

**Industry context:** Defense contractor managing a CMMC Level 2 enclave. All changes require dual approval and produce compliance evidence artifacts. No cloud — entirely on-prem. Air-gapped workloads.

### Connectors (6)

| Name | Type | Scoped Permissions |
|------|------|--------------------|
| FreeIPA | freeipa | accounts: [read, disable, enable, reset], groups: [read, write], dns: [read] |
| CrowdStrike | crowdstrike | hosts: [read, isolate], sensors: [deploy, update], rtr: [execute] |
| SCCM | sccm | devices: [read], patches: [deploy, verify], software: [read] |
| Wazuh | wazuh | agents: [read], alerts: [read], rules: [read, write] |
| Ansible | ansible | playbooks: [execute], inventory: [read, write], facts: [read] |
| BIND DNS | bind_dns | zones: [read, write], records: [read, write], dnssec: [sign, verify] |

### Assets (17)

| Name | Type | Environment | Criticality | Key Metadata |
|------|------|-------------|-------------|--------------|
| freeipa-idm-01 | server | prod | critical | owner: infra-team, why_exists: "Identity and DNS authority for the enclave — all auth flows through this", os: rhel-9.3, cmmc_controls: ["AC.1.001", "IA.1.076"], depends_on: [] |
| enclave-server-01 | server | prod | critical | owner: infra-team, why_exists: "Primary CUI processing workload", os: rhel-9.3, kernel: 5.14.0, classification_level: CUI, air_gapped: true, cmmc_controls: ["SC.3.177", "SI.2.216"] |
| enclave-server-02 | server | prod | critical | owner: infra-team, why_exists: "Secondary CUI processing — failover", os: rhel-9.3, kernel: 5.14.0, classification_level: CUI, air_gapped: true |
| enclave-server-03 | server | prod | high | owner: infra-team, why_exists: "Internal tooling and automation for enclave ops", os: rhel-9.3, kernel: 5.14.0 |
| enclave-server-04 | server | prod | high | owner: infra-team, why_exists: "Log aggregation and SIEM collection node", os: rhel-9.3, kernel: 5.14.0 |
| enclave-server-05 | server | prod | high | owner: infra-team, why_exists: "Backup and data integrity verification node", os: rhel-9.3, kernel: 5.14.0 |
| win-workstation-01 | endpoint | prod | high | owner: infra-team, why_exists: "Analyst workstation with CUI access — must be SCCM-managed", os: windows-11-22h2, cmmc_controls: ["AC.1.001"] |
| win-workstation-02 | endpoint | prod | high | owner: infra-team, why_exists: "Engineering workstation for system administration", os: windows-11-22h2 |
| sccm-server | server | prod | high | owner: infra-team, why_exists: "Patch management authority for all Windows endpoints in enclave" |
| wazuh-siem | application | prod | critical | owner: security-team, why_exists: "Security event correlation and alert generation for CMMC audit logging", cmmc_controls: ["AU.2.041", "AU.2.042"], depends_on: ["enclave-server-04"] |
| ansible-control | server | prod | high | owner: infra-team, why_exists: "Automation control node for configuration management and hardening playbooks" |
| bind-dns-internal | dns_zone | prod | high | owner: infra-team, why_exists: "Authoritative DNS for ironclad.local — no external resolution", provider: bind, records: 47 |
| classified-storage-01 | storage_bucket | prod | critical | owner: data-team, why_exists: "Primary CUI data store — encrypted at rest, access logged", classification_level: CUI, cmmc_controls: ["MP.2.119", "SC.3.177"] |
| classified-storage-02 | storage_bucket | prod | critical | owner: data-team, why_exists: "CUI backup store — air-gapped from primary", classification_level: CUI, air_gapped: true |
| crowdstrike-fleet | endpoint | prod | high | owner: security-team, why_exists: "CrowdStrike sensor coverage across all RHEL hosts in enclave", count: 5 |
| internal-ca | application | prod | critical | owner: infra-team, why_exists: "Internal certificate authority for all TLS and code signing in enclave", depends_on: ["freeipa-idm-01"] |
| jump-server | server | prod | critical | owner: infra-team, why_exists: "Single ingress point for all administrative access to enclave — all sessions logged", cmmc_controls: ["AC.2.006", "AC.2.013"] |

### Change Requests (14)

1. **Kernel upgrade enclave-server-01 and 02** — `ec2_stop_start` — completed. Kernel 5.14 → 6.1. Dual approval: infra-lead + ISSO. Pre-change snapshot. Compliance evidence artifact attached.
2. **Kernel upgrade enclave-server-03, 04, 05** — `ec2_stop_start` — awaiting_approval. Same upgrade, second phase. Dual approval required.
3. **Apply seccomp policy to containerized workloads** — `microsegmentation_policy` — completed. RuntimeDefault seccomp applied. Policy stored as compliance artifact. CMMC SC.3.177.
4. **Set SELinux enforcing mode on enclave-server-03** — `remote_command` — planned. Currently permissive. Enforcing required for CMMC SI.2.216.
5. **Rotate FreeIPA admin credentials** — `key_rotation` — completed. Reconstitution rollback: break-glass procedure documented. Dual approval.
6. **Ansible CIS Level 2 hardening playbook** — `remote_command` — awaiting_approval. Applies CIS RHEL 9 Level 2 benchmark across all enclave servers. ISSO approval required.
7. **Patch SCCM agent on Windows workstations** — `remote_command` — completed. SCCM agent 5.0.9040 → 5.0.9106. Rollback: uninstall new agent.
8. **Revoke departed contractor LDAP account** — `dns_update` (placeholder) — completed. FreeIPA account disabled, Kerberos principal removed, access review evidence archived. CMMC AC.1.001.
9. **Enable auditd rules: privileged command logging** — `remote_command` — completed. Auditd rules for sudo, su, ssh-keygen. CMMC AU.2.041.
10. **Apply AppArmor profile to Wazuh agent** — `microsegmentation_policy` — draft. Restrict Wazuh agent binary to required paths and network access.
11. **Rotate internal CA** — `key_rotation` — awaiting_approval. 12-month rotation. Reconstitution rollback: old CA kept valid 90 days post-rotation. Dual approval: infra-lead + ISSO.
12. **Deploy kernel module signing enforcement** — `remote_command` — draft. Enforce UEFI Secure Boot + module signing on all enclave servers.
13. **BIND DNS DNSSEC signing** — `dns_update` — awaiting_approval. Enable DNSSEC for ironclad.local zone. Rollback: disable DNSSEC, revert SOA.
14. **CMMC Level 2 compliance attestation CR** — `snapshot_asset` — completed. Verification of 110 practices. Evidence bundle attached as artifact. Assessment date logged.

---

## Demo Switcher

### Backend: `GET /demo/orgs`

**File:** `backend/app/routers/demo.py` (new file)

- Only registered when env var `DEMO_MODE=true` (default in docker-compose.yml)
- No auth required (credentials are demo-only, always `admin123`)
- Returns all 4 demo orgs including Acme

```python
DEMO_ORGS = [
    {"id": "00000000-0000-0000-0000-000000000001", "name": "Acme Security Corp", "slug": "acme", "admin_email": "admin@acme.example", "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000002", "name": "CloudRun Technologies", "slug": "saas", "admin_email": "admin@cloudrun.example", "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000003", "name": "Meridian Capital Partners", "slug": "finserv", "admin_email": "admin@meridian.example", "admin_password": "admin123"},
    {"id": "00000000-0000-0000-0000-000000000004", "name": "Ironclad Systems Group", "slug": "defense", "admin_email": "admin@ironclad.example", "admin_password": "admin123"},
]
```

Returns 404 if `DEMO_MODE != "true"`.

Router is conditionally included in `backend/app/main.py`:
```python
if os.getenv("DEMO_MODE") == "true":
    from app.routers.demo import router as demo_router
    app.include_router(demo_router, prefix="/demo", tags=["demo"])
```

### Frontend: Sidebar switcher

**File:** `frontend/src/components/DemoOrgSwitcher.tsx` (new component)

- Rendered in `Sidebar.tsx` between user info block and sign-out link, only when `GET /demo/orgs` returns successfully
- Shows: "Demo:" label + current org name as a `<select>` dropdown
- On change: calls `POST /auth/login` with selected org credentials → stores new token → `window.location.reload()` to reset all state
- Current org highlighted (matched by org name from `useAuth()`)
- Styled consistent with sidebar (slate text, no chrome)

### docker-compose.yml

Add to backend service env:
```yaml
DEMO_MODE: "true"
```

---

## Acceptance Criteria

- [ ] `backend/app/seed/scenarios/saas.py` seeds CloudRun org idempotently with 6 connectors, 16 assets, 12 CRs
- [ ] `backend/app/seed/scenarios/finserv.py` seeds Meridian org idempotently with 7 connectors, 18 assets, 13 CRs
- [ ] `backend/app/seed/scenarios/defense.py` seeds Ironclad org idempotently with 6 connectors, 17 assets, 14 CRs
- [ ] `backend/seed.py` calls all three scenario seeds after existing Acme seed
- [ ] All CRs have appropriate status, risk level, and desired_outcome
- [ ] Completed CRs have at least one Approval record
- [ ] All assets have `owner`, `why_exists`, `depends_on` in metadata
- [ ] `GET /demo/orgs` returns 4 orgs when `DEMO_MODE=true`, 404 otherwise
- [ ] `DemoOrgSwitcher` renders in sidebar when demo orgs endpoint is available
- [ ] Switching org silently re-auths and reloads
- [ ] Container restart with fresh DB seeds all 4 orgs successfully
- [ ] Re-running seed on populated DB is a no-op (idempotent)

---

## Out of Scope

- Asset relationship graph edges (sub-project 3 — metadata hints are forward-compat stubs)
- Vulnerability findings records (sub-project 5 will add these)
- Recommendation records (sub-project 6)
- Per-org runbook templates (can be added in a follow-up)
- Access review or maintenance window records per scenario (existing Acme data covers demo)
