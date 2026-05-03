# Nexplane

**The control plane for security execution.**

Nexplane connects intent to action across your infrastructure — enabling security engineering and architecture teams to execute infrastructure changes safely, without routing work through sysadmin, network admin, or SRE queues.

> *Execute with Confidence*

---

## What It Does

Security teams identify issues and need to act on them: rotate compromised keys, isolate a compromised endpoint, tighten firewall rules after a scan, deploy an EDR sensor to unprotected hosts, enforce MFA, offboard a departing employee. Today, all of that flows through tickets.

Nexplane gives security teams a governed execution layer:

- **Projects** — group related change requests into a sequenced plan with dependency tracking
- **AI Planning Assistant** — describe your goal, get a structured change plan with proposed change requests
- **Change Requests** — safety-reviewed, approval-gated, audited, with automatic rollback
- **Composable Runbooks** — chain change types into reusable multi-step workflows with conditional branching and human checkpoints
- **Asset Inventory** — servers, cloud accounts, firewalls, identities, applications — discoverable via connectors
- **Connectors** — 38+ integrations spanning cloud, identity, EDR, IaC, ticketing, and observability — with real API calls when credentials are configured
- **Nexplane Agent** — a cross-platform Go binary that runs on managed machines, reaches out to the control plane, and executes signed commands — no inbound SSH required
- **Incident Response Playbooks** — pre-defined fast-path workflows for host isolation, account lockdown, evidence preservation, and phishing response
- **Vulnerability Remediation Pipeline** — close the loop between scanner findings and automated remediation
- **Compliance & Governance** — CIS benchmark enforcement, drift detection, change freeze windows, audit evidence collection

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  React Frontend  (Vite + TypeScript + Tailwind CSS)                      │
│                                                                          │
│  Dashboard · Projects · Change Requests · Approvals · Asset Inventory   │
│  Connectors · Settings · Runbooks · Compliance · Incident Response       │
│  Vulnerability Remediation · Access Reviews · Maintenance Windows        │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ HTTP/REST
┌───────────────────────────▼─────────────────────────────────────────────┐
│  FastAPI Backend  (Python 3.12)                                           │
│                                                                          │
│  Safety Engine · Planning Engine · AI Service · Audit Service            │
│  Secrets Service (Fernet AES-256, HSM/Vault-swappable)                   │
│  SecretsService versioning (rotate + rollback with 7-day TTL)            │
│  RunbookExecutor · IRExecutor · FleetExecutor · IaCExecutor              │
│  VulnRemediationEngine · IdentityResolver · DriftDetection               │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ Action Catalog  (per-connector JSON + executor modules)          │   │
│  │   Tier 1: direct_api  ·  Tier 3: agent  ·  Tier 5: ssh          │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Connectors — change actions + ingest (38+ connectors)                   │
│  aws · azure · gcp · cloudflare · okta · paloalto · ssh                 │
│  active_directory · entra_id · crowdstrike · tenable · kubernetes        │
│  google_workspace · github · slack · nexplane_agent · ...               │
│                                                                          │
│  Agent API  (/agent/register · /agent/jobs/next · /agent/result)        │
│  IR API  (/ir/templates · /ir/execute · /ir/bundles)                     │
│  Runbook API  (/runbooks · /executions)                                  │
│  Vulnerability API  (/vulnerability/findings · /webhooks/findings)       │
│  Compliance API  (/compliance/baselines · /compliance/freeze-windows)    │
│  Fleet API  (/maintenance-windows)                                       │
│  Identity API  (/access-reviews · /change-requests[offboard/onboard])   │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ SQLAlchemy async
┌───────────────────────────▼─────────────────────────────────────────────┐
│  PostgreSQL 16  (16+ Alembic migrations, 30+ tables)                     │
└──────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │  Nexplane Agent (Go)                  │
                    │  linux/amd64 · arm64 · windows/amd64 │
                    │                                       │
                    │  Self-updating — checks /downloads/   │
                    │  version on startup, applies update   │
                    │  atomically via os.Rename + exec()    │
                    │                                       │
                    │  Outbound poll only —                 │
                    │  no inbound SSH needed                │
                    └────────────┬──────────────────────────┘
                                 │ long-poll HTTP (outbound)
                                 └──► /agent/jobs/next
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query v5, React Router v6 |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async, Pydantic v2 |
| Database | PostgreSQL 16 |
| Migrations | Alembic (16 migrations, 30+ tables) |
| Workflow | Temporal-pattern abstraction (asyncio MVP, Temporal-ready) |
| Auth | JWT + bcrypt |
| AI | Anthropic Claude + OpenAI (multi-provider, default configurable) |
| Secrets | `cryptography.fernet` (AES-256) with versioning for rotation; abstracted for HSM/Vault swap-out |
| Agent | Go 1.26+, AWS SDK v2, `golang.org/x/sys`, lib/pq, go-sql-driver/mysql, go-mssqldb |
| Connector SDKs | boto3, azure-sdk, google-cloud-*, msal, hvac, kubernetes, falconpy, pytenable, pan-os-python, httpx, paramiko, checkov, google-api-python-client, slack-sdk, PyGithub, google-auth-httplib2, croniter |
| Scheduler | APScheduler 3.x (scanner poll, SLA enforcement, compliance scans, access reviews, maintenance windows, scheduled reboots) |
| Graph | React Flow 11 + @dagrejs/dagre (project dependency visualization) |
| Deployment | Docker Compose (multi-stage build: Go agent binaries + Python backend) |

---

## Quick Start

```bash
git clone <repo-url>
cd nexplane

# Start everything
docker compose up --build

# Frontend:   http://localhost:3000
# Backend:    http://localhost:8000
# API docs:   http://localhost:8000/docs
```

### Demo Credentials

| Email | Password | Role |
|-------|----------|------|
| admin@acme.example | admin123 | Admin |
| operator@acme.example | operator123 | Security Operator |
| approver@acme.example | approver123 | Approver |
| auditor@acme.example | auditor123 | Auditor |

---

## Key Features

### Projects

Group change requests into sequenced, dependency-linked security initiatives. Built-in dependency graph prevents executing CR B before CR A completes. Visual DAG with React Flow + Dagre auto-layout.

### AI Planning Assistant

Available on draft projects. Opens a conversational panel where an operator describes their goal and the AI proposes structured change plans referencing your actual asset inventory. Supports **Anthropic** (Claude) and **OpenAI** as AI providers.

### Change Requests

Full lifecycle: Draft → Planned → Awaiting Approval → Approved → Executing → Verifying → Completed / Rolled Back.

**Change types:**

| Category | Types |
|----------|-------|
| Infrastructure | `dns_update`, `security_group_update`, `microsegmentation_policy`, `snapshot_asset` |
| EC2 | `ec2_launch`, `ec2_start`, `ec2_stop`, `ec2_reboot`, `ec2_terminate` |
| Agent (OS) | `patch_packages`, `patch_campaign`, `isolate_host`, `rolling_restart`, `canary_config_push`, `distribute_file`, `fleet_health_check` |
| Identity | `offboard_user`, `onboard_user`, `key_rotation`, `rotate_db_credentials`, `rotate_ssh_keys`, `rotate_api_key`, `rotate_service_account` |
| Incident Response | `lockdown_account`, `phishing_response`, `preserve_evidence` |
| IaC | `terraform_apply`, `ansible_playbook`, `helm_upgrade` |
| Database | `provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `promote_db_replica`, `db_connection_config` |
| Backup / Recovery | `create_backup`, `verify_backup`, `restore_files`, `dr_failover`, `scheduled_reboot` |
| Compliance | `enforce_cis_benchmark`, `collect_evidence` |
| Telemetry | `telemetry_agent_deploy`, `remote_command` |

### Composable Runbooks

User-defined, reusable multi-step workflows that chain existing change types with conditional logic:

- **Step types:** `change` (creates a change request), `condition` (branches on step results), `human_checkpoint` (pauses for operator confirmation), `parallel_group` (runs steps concurrently)
- **Failure handling:** per-step `on_failure: abort | continue | rollback_all`
- **Versioning:** each edit creates a new version; active executions keep their version snapshot
- **Seed templates:** Engineer Onboarding, Account Compromise IR, Patch Campaign

### Incident Response Playbooks

Pre-built, fast-path change workflows for common incident types — launched from a single click:

- **Host Isolation** — flush iptables/nftables (Linux) or Windows Firewall rules, allow only management CIDR + control plane, record pre-isolation state for rollback
- **Account Lockdown** — disable across AD, Okta, Entra ID, Google Workspace, GitHub, Slack simultaneously
- **Evidence Preservation** — collect auth logs, journal, auditd, netstat, ARP, process state → tar.gz → S3 pre-signed upload; runs *before* any remediation
- **Phishing Response** — block sender domain, force password reset for affected users, revoke sessions, force MFA re-enrollment

IR change requests get an expedited approval path. Forensic bundles are stored in the `forensic_bundles` table and downloadable via the IR tab.

### Vulnerability Remediation Pipeline

Closes the loop between scanner findings and automated fixes:

- **Webhook ingest** (`POST /vulnerability/webhooks/vulnerability-findings`, HMAC-verified) or scheduled scanner poll
- **Asset matching** — maps findings to Nexplane assets by IP/hostname
- **Auto-generate draft CRs** — based on `RemediationPolicy` rules (finding type → change type → approval level)
- **CVE blast-radius UI** — enter a CVE, see all affected assets, generate a patch campaign in one click
- **SLA enforcement** — background job escalates overdue findings and auto-creates change requests
- **Policy editor** — configure per-severity: auto-generate (yes/no), auto-approve (yes/no), SLA days

### Identity Lifecycle

- **User Offboarding (Kill Switch)** — one change request atomically disables across AD, Okta, Entra ID, Google Workspace, GitHub, Slack, and optionally isolates CrowdStrike-managed endpoints in 5 ordered phases
- **User Onboarding** — provision accounts across all connected identity systems from a single form
- **Access Reviews** — periodic campaigns: collect current group memberships across all identity connectors, present for manager review, auto-generate removal change requests for revoked access

### Secret & Credential Rotation

- **DB credential rotation** — generate new password, update DB user, update app config files, restart service, verify connection; with rollback
- **SSH key fleet rotation** — remove old key by fingerprint from `authorized_keys` across a fleet, add new public key, backup for rollback
- **API key rotation** — rotate AWS IAM key, Okta API token, or GitHub PAT; propagate to Kubernetes secrets, agent env files, SSM Parameter Store
- **Service account rotation** — update password in AD/Okta, push to dependent services via asset metadata
- **Step output propagation** — credentials travel in memory between steps only, never written to logs or DB

### Fleet Operations

- **Rolling Restart** — restart a service across a fleet N hosts at a time; abort if failure rate exceeds threshold; health check between batches
- **Canary Config Push** — deploy config to 1 host, run verification command, roll to rest if pass; rollback restores original files
- **Bulk File Distribution** — push CA certs, `authorized_keys`, scripts, config files to a fleet
- **Fleet Health Check** — disk, load, service status, pending-reboot check across all target hosts before major operations
- **Maintenance Windows** — define cron-schedule windows; approved CRs arriving outside windows queue and auto-execute when the window opens

### Compliance & Governance

- **CIS Benchmark Campaigns** — audit all CIS controls (filesystem, sysctl, SSH, PAM, auditd, SELinux/AppArmor) then apply remediation; before/after compliance score
- **Drift Detection** — weekly scheduled scan compares hosts to their baseline; creates draft remediation CRs for drifted controls
- **Change Freeze Enforcement** — declare a freeze window; approve/execute endpoints return 423 Locked; emergency bypass with mandatory justification header and audit log
- **Audit Evidence Collection** — request evidence for a SOC2/PCI/ISO27001 control; agent collects config files and command outputs; backend packages as downloadable ZIP

### IaC Orchestration

Terraform, Ansible, and Helm as tracked, auditable Nexplane change types:

- **Terraform** — two-phase: `terraform plan` output stored as blast radius → approval gate → `terraform apply`; rollback via `terraform apply` to previous state
- **Ansible** — `--check` mode as preflight, full run with rollback playbook path
- **Helm** — `helm upgrade --atomic` with automatic rollback on health check failure; `helm rollback` on demand

The plan output (diff) renders in the change request detail view with green/red coloring.

### Database Administration

Agent commands for PostgreSQL, MySQL, and MSSQL (Windows):
- Provision/deprovision DB users with scoped grants
- Grant/revoke permissions
- Configure audit logging (pg_audit, general_log, SQL Audit)
- Connection limit management
- RDS read replica promotion (AWS connector, with Route53 CNAME update)

### Backup & Recovery

- **On-demand backup** — restic-based backup to S3 (Linux/Windows agent) or EBS/RDS snapshot (AWS connector)
- **Backup verification** — restore RDS snapshot to temp instance, run health query, terminate; confirms backup integrity
- **File-level restore** — restic restore with path filter + SHA256 checksum verification
- **DR Failover** — Route53 weighted routing update with per-step RTO timestamps; reports actual vs target RTO

### SaaS Change Actions

Change actions (not just discovery) on previously read-only connectors:

**Google Workspace:** `remove_from_groups`, `reset_2fa`, `revoke_oauth_tokens`, `wipe_mobile_device`, `suspend_user`, `unsuspend_user`

**GitHub:** `remove_org_member`, `revoke_user_pats`, `enforce_branch_protection`, `archive_repo`, `disable_actions`, `enable_actions`

**Slack:** `deactivate_user`, `reactivate_user`

**Microsoft 365 / Entra ID:** `remove_from_teams`, `assign_license`, `remove_license`, `revoke_sessions`, `disable_user`

**Kubernetes:** `restart_deployment`, `scale_deployment`, `apply_network_policy`, `update_rbac`, `rotate_secret`, `helm_upgrade`, `helm_rollback`

### Connectors

**Cloud & Infrastructure (10 connectors)**

| Connector | Key capabilities |
|-----------|-----------------|
| AWS | EC2 start/stop/terminate/launch; IAM key rotation; S3 access; security groups; EBS/RDS snapshots; DR failover; promote read replica |
| Azure | VMs; NSGs; Entra users (disable/enable, revoke sessions, assign license); storage; Defender |
| GCP | Compute; IAM; storage; firewall; SAs; SCC findings; stop/start/delete; block public buckets |
| Cloudflare | WAF; firewall; access policies; block IP; SSL mode; DNS |
| Palo Alto | Address objects/rules/zones; create/delete rules; block IP; commit |
| Active Directory | Discover/disable stale accounts; move OU; rotate service account passwords |
| CrowdStrike | Isolate host; RTR commands; prevention policy; host containment |
| Tenable | Launch/pause/resume scans; export; trigger remediation verification |
| SSH | Execute approved command templates; check service status; tail logs |
| Nexplane Agent | Full OS hardening + all new command packages (see below) |

**Identity & Access (4 connectors)**

| Connector | Key capabilities |
|-----------|-----------------|
| Okta | Suspend/deactivate; reset MFA; revoke sessions; force enrollment; rotate API key; rotate service account |
| Microsoft Entra ID | Disable/enable; reset MFA; block sign-in; remove from Teams; assign/remove license; revoke sessions |
| HashiCorp Vault | Discover engines/policies/leases; rotate secrets; revoke leases; seal vault |
| GitHub | Branch protection; suspend member; revoke PATs; archive repos; disable Actions |

**SaaS (4 connectors with change actions)**

| Connector | Key capabilities |
|-----------|-----------------|
| Google Workspace | Suspend/unsuspend; remove from groups; reset 2FA; revoke OAuth tokens; wipe mobile device |
| Slack | Deactivate/reactivate user |
| Kubernetes | Restart/scale deployments; network policies; RBAC; rotate secrets; Helm upgrade/rollback |
| Helm | Discover releases/history; upgrade; rollback; uninstall |

**Security Tools / EDR (4 connectors)**

SentinelOne · Microsoft Defender for Endpoint · Snyk · Qualys

**Cloud Security & Discovery (3 connectors)**

RunZero · Wiz · Zscaler

**IaC & Configuration Management (9 connectors)**

Terraform · Ansible · AWS CloudFormation · Pulumi · Azure Bicep · Checkov · SaltStack · Chef InSpec · Helm

**Workflow & Observability (7 connectors)**

Jira · PagerDuty · ServiceNow · Splunk · Datadog · Google Workspace · PagerDuty

---

### Nexplane Agent

A cross-platform Go binary that reverses the connection direction: the agent polls the control plane for jobs and executes signed commands. No inbound SSH required.

**Self-updating:** On startup the agent fetches `/downloads/version`, downloads and SHA256-verifies the new binary if behind, atomically replaces itself via `os.Rename` + `syscall.Exec`. Windows agents log a manual-update message.

**Security model:**
- Bearer token authentication (shared HMAC secret, generated in Settings)
- Each job payload signed with HMAC-SHA256; agent verifies before executing
- Agent registers itself — the managed machine appears as an Asset automatically

**Agent command packages:**

| Package | Commands | Platform |
|---------|----------|----------|
| `linuxpatch` | `apply_linux_patches` (apt/yum/dnf, security-only or CVE-targeted, dry-run, before/after diff), `audit_linux_patch_status` | Linux |
| `winpatch` | `apply_windows_patches` (WUA COM API, specific KB, reboot scheduling), `audit_windows_patch_status` | Windows |
| `isolation` | `isolate_host` (flush iptables/nftables, allow management CIDR only), `restore_network_access` | Linux + Windows |
| `forensics` | `collect_forensics` (auth.log, journal, auditd, netstat, ARP, proc state → tar.gz → S3) | Linux + Windows |
| `compliance` | `audit_cis_compliance` (filesystem, sysctl, SSH, PAM, auditd, SELinux/AppArmor per-control pass/fail), `collect_evidence` | Linux |
| `credrotation` | `rotate_db_credentials` (generate, update DB user, update config, restart service), `rotate_ssh_keys` (fingerprint-based), `update_agent_env_file` | Linux + Windows |
| `iac` | `terraform_plan`, `terraform_apply`, `terraform_rollback`, `ansible_check`, `ansible_run`, `helm_diff`, `helm_upgrade`, `helm_rollback` | Linux |
| `fleet` | `restart_service`, `push_config_file` (with backup), `distribute_file`, `health_check` | Linux + Windows |
| `backup` | `create_backup` (restic), `restore_files` (restic with path filter + SHA256 verify) | Linux + Windows |
| `reboot` | `graceful_reboot`, `verify_post_reboot` (service health check) | Linux + Windows |
| `dbadmin` | `provision_db_user`, `deprovision_db_user`, `grant_permissions`, `revoke_permissions`, `configure_db_audit`, `db_connection_config` | PostgreSQL, MySQL, MSSQL |
| `ossecurity` | SELinux, AppArmor, seccomp, sysctl, iptables, kernel modules, mount hardening, auditd, FIM, eBPF | Linux |
| `linuxauth` | PAM, SSH, CA certs, NTP, user/group audit, privesc audit | Linux |
| `winharden` | LAPS, Credential Guard, PowerShell CLM, AppLocker, SMB, BitLocker, Windows Firewall, TLS, RDP, audit policy, registry | Windows |
| `crossplatform` | TLS certificates, DNS resolver, software inventory, syslog forwarding | Linux + Windows |
| `linuxupgrade` | In-place or containerize-and-migrate OS upgrades | Linux |
| `estimatesize` | Disk space preflight | Both |
| `changip` | IP address change (IPv4/IPv6/DHCP) | Both |
| `configsyslog` | Syslog forwarding (rsyslog/syslog-ng/NXLog/WEF) | Both |
| `virtualize` | Disk imaging (dd/losetup/VHD/robocopy) | Both |
| `uploadimage` | S3 multipart upload | Both |

---

## Example Security Projects (seeded)

The demo environment includes 10 pre-seeded example projects covering real security workflows:

1. Isolate and Investigate Compromised Endpoint
2. Deploy EDR to Unprotected Hosts
3. Remediate Critical CVE Across Fleet
4. Offboard Departed Employee
5. Remediate Public Azure Storage
6. Tighten Firewall After Vulnerability Scan
7. MFA Enforcement for Non-Compliant Accounts
8. Microsegmentation for Payments Subnet
9. Harden Payments-API Workload Isolation
10. Identify and Respond to Firewall Chokepoints

---

## Project Structure

```
nexplane/
├── VERSION                              # Single source of truth for agent version (0.1.0)
├── agent/                               # Nexplane Agent (Go)
│   ├── main.go                          # Entry point — updater check, config, fingerprint, register, poll
│   ├── go.mod                           # Go 1.26+, AWS SDK v2, DB drivers (pq, mysql, mssqldb)
│   ├── updater/                         # Self-update: CheckAndUpdate, FetchVersion, VerifySHA256
│   ├── config/                          # Flag + env var config
│   ├── fingerprint/                     # Stable machine ID
│   ├── agenthmac/                       # HMAC-SHA256 sign/verify
│   ├── client/                          # HTTP client — register, poll, post result
│   ├── registration/                    # Registration logic
│   ├── poller/                          # Ephemeral and service modes
│   ├── executor/                        # Command dispatcher (30+ commands registered)
│   └── commands/
│       ├── linuxpatch/                  # apt/yum/dnf security patches
│       ├── winpatch/                    # Windows Update via WUA COM API
│       ├── isolation/                   # Network isolation (iptables/nftables/WF)
│       ├── forensics/                   # Evidence collection + S3 upload
│       ├── compliance/                  # CIS audit + evidence collection
│       ├── credrotation/                # DB, SSH key, API key, env file rotation
│       ├── iac/                         # Terraform, Ansible, Helm CLI wrappers
│       ├── fleet/                       # Service restart, config push, health check
│       ├── backup/                      # restic backup + restore
│       ├── reboot/                      # Graceful reboot + post-reboot verify
│       ├── dbadmin/                     # PostgreSQL, MySQL, MSSQL user/permission management
│       ├── ossecurity/                  # Linux MAC/kernel/integrity hardening
│       ├── ebpf/                        # eBPF program management
│       ├── linuxauth/                   # PAM, SSH, CA certs, NTP, user audit
│       ├── winharden/                   # Windows security hardening suite
│       ├── crossplatform/               # TLS, DNS, software inventory
│       ├── linuxupgrade/                # Linux in-place and containerize-and-migrate
│       ├── estimatesize/                # Disk space preflight
│       ├── changip/                     # IP address change
│       ├── configsyslog/                # Syslog forwarding
│       ├── virtualize/                  # Disk imaging
│       └── uploadimage/                 # S3 multipart upload
│
├── backend/
│   ├── Dockerfile                       # Multi-stage: agent binaries → Python backend
│   ├── seed.py                          # Demo data (org, users, assets, connectors, CRs, projects)
│   ├── alembic/versions/                # 16 migrations (001→016), 30+ tables
│   └── app/
│       ├── main.py                      # App factory + router registration
│       ├── models/
│       │   ├── change_request.py        # 30+ ChangeType values, fleet/IR status values
│       │   ├── runbook.py               # Runbook, RunbookStep, RunbookExecution, RunbookStepResult
│       │   ├── vulnerability.py         # VulnerabilityFinding, RemediationPolicy, RemediationSLA
│       │   ├── compliance.py            # ComplianceBaseline, ChangeFreezeWindow
│       │   ├── maintenance_window.py    # Fleet maintenance scheduling
│       │   ├── access_review.py         # AccessReview + access_review_change_requests join table
│       │   ├── access_review_schedule.py # Recurring access review scheduling
│       │   └── ...                      # asset, connector, project, agent, org_settings, etc.
│       ├── routers/
│       │   ├── runbooks.py              # /runbooks + /executions (CRUD, trigger, resume checkpoint)
│       │   ├── vulnerability.py         # /vulnerability (findings, policies, SLA, webhook ingest)
│       │   ├── compliance.py            # /compliance (baselines, freeze windows, evidence ZIP)
│       │   ├── incident_response.py     # /ir (templates, execute, bundles)
│       │   ├── access_reviews.py        # /access-reviews (collect, decisions, approve, changes)
│       │   ├── maintenance_windows.py   # /maintenance-windows CRUD + status
│       │   └── ...                      # change_requests, assets, connectors, projects, agent, etc.
│       ├── services/
│       │   ├── runbook_service.py       # RunbookService: fork, trigger, version snapshot
│       │   ├── runbook_executor.py      # RunbookExecutor: change/condition/checkpoint/parallel steps
│       │   ├── runbook_cr_bridge.py     # Bridges executor to ChangeRequest model
│       │   ├── iac_executor.py          # Two-phase IaC execution (plan → approval → apply)
│       │   ├── fleet_executor.py        # Rolling restart, canary push, distribute file, health check
│       │   ├── vuln_asset_matcher.py    # Finding → Nexplane asset matching by IP/hostname
│       │   ├── vuln_remediation_engine.py # Finding → change request generation
│       │   ├── identity_resolution.py   # Email → per-connector account lookup
│       │   ├── change_execution.py      # Step output propagation (produces/consumes)
│       │   ├── secrets_service.py       # Fernet AES-256 + versioning (rotate/rollback)
│       │   ├── scheduler_service.py     # APScheduler: 6+ recurring jobs
│       │   └── ...                      # ai_service, ingest_service, safety/planning engine
│       ├── compliance/
│       │   ├── freeze.py                # require_no_active_freeze FastAPI dependency
│       │   └── drift.py                 # detect_drift, run_drift_detection weekly job
│       ├── jobs/
│       │   ├── sla_enforcement.py       # Mark overdue findings, auto-create CRs
│       │   ├── finding_asset_match.py   # Retry unmatched findings against asset inventory
│       │   └── scanner_poll.py          # Pull findings from Qualys/Tenable APIs
│       ├── seed/
│       │   └── runbook_templates.py     # 3 seed runbooks: Onboarding, IR, Patch Campaign
│       ├── connectors/
│       │   ├── catalog/                 # Per-connector JSON catalogs (38+ connectors)
│       │   ├── change_type_definitions/ # 30+ change type JSON definitions
│       │   └── executors/               # Real API implementations
│       │       ├── nexplane_agent/      # Agent command dispatch + patch/compliance/fleet
│       │       ├── aws/                 # EC2, IAM, S3, SGs, RDS, EBS, Route53, DR failover
│       │       ├── google_workspace/    # suspend, remove_from_groups, reset_2fa, revoke_oauth, wipe_device
│       │       ├── github/              # remove_member, revoke_pats, branch_protection, archive, actions
│       │       ├── slack/               # deactivate_user, reactivate_user
│       │       ├── entra_id/            # remove_from_teams, assign/remove_license, revoke_sessions
│       │       ├── kubernetes/          # restart/scale deployment, RBAC, network policy, Helm
│       │       ├── active_directory/    # rotate service account password (ldap3)
│       │       ├── okta/                # rotate API key, rotate service account
│       │       ├── offboard_user/       # 8-step kill switch across all identity connectors
│       │       └── onboard_user/        # 6-step provisioning across all identity connectors
│       └── tests/                       # 200+ passing tests
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Runbooks.tsx             # Runbook list + trigger
│       │   ├── RunbookEditor.tsx        # Step builder (change/condition/checkpoint/parallel)
│       │   ├── RunbookExecution.tsx     # Live progress + checkpoint resume UI
│       │   ├── Compliance.tsx           # CIS scores, control breakdown, drift alerts, evidence
│       │   ├── VulnerabilityRemediation.tsx # CVE blast-radius + FindingQueue + SLAWidget
│       │   ├── BackupRecovery.tsx       # On-demand backup + restore request
│       │   ├── ScheduledOperations.tsx  # Scheduled reboots, access review schedules
│       │   ├── MaintenanceWindows.tsx   # Fleet maintenance window CRUD
│       │   ├── AccessReviews.tsx        # Access review list + decision UI
│       │   ├── Projects.tsx             # Project list
│       │   ├── ProjectDetail.tsx        # AI panel + List/Graph tab
│       │   ├── Settings.tsx             # AI providers + agent secret + remediation policies
│       │   ├── Connectors.tsx           # Connector cards + credentials + schedule + discovery
│       │   ├── Assets.tsx               # Asset inventory with tag search + connector filter
│       │   └── ChangeRequestDetail.tsx  # Full CR detail + IR tab + batch progress + plan output panel
│       ├── components/
│       │   ├── FreezeAlert.tsx          # Amber banner during active change freeze
│       │   ├── IRPlaybookLauncher.tsx   # IR playbook card grid + parameter modal
│       │   ├── IRStepStatusBadge.tsx    # Colored status indicators for IR steps
│       │   ├── FindingQueue.tsx         # Paginated vulnerability findings with SLA badges
│       │   ├── SLAWidget.tsx            # Per-severity SLA status summary
│       │   ├── RemediationPolicyEditor.tsx # Policy CRUD UI in Settings
│       │   ├── AIPanel.tsx              # Conversational AI planning panel
│       │   ├── CredentialModal.tsx      # Per-connector credential configuration
│       │   ├── ScheduleModal.tsx        # Recurring ingest interval picker
│       │   ├── Layout.tsx               # App shell with FreezeAlert polling
│       │   ├── Sidebar.tsx              # Navigation (all sections including new ones)
│       │   └── ProjectGraph/            # Dependency DAG (React Flow + Dagre)
│       └── hooks/
│           └── useRunbooks.ts           # TanStack Query hooks for runbook API
│
├── docs/superpowers/
│   ├── specs/                           # 13 design specs (agent self-update + 12 sysadmin workflows)
│   └── plans/                           # 13 implementation plans
│
└── docker-compose.yml
```

---

## Building the Agent

The agent is built inside Docker as part of `docker compose build`. Binaries are served at `/downloads/`:

```
nexplane-agent-linux-amd64-0.1.0
nexplane-agent-linux-arm64-0.1.0
nexplane-agent-windows-amd64-0.1.0.exe
+ .sha256 sidecar for each
version   (plain text: 0.1.0)
```

**To build locally:**

```bash
cd agent

# Run tests
go test ./...

# Build for current platform
go build -ldflags="-X main.Version=0.1.0" -o dist/nexplane-agent ./

# Cross-compile
GOOS=linux GOARCH=amd64 go build -ldflags="-X main.Version=0.1.0" -o dist/nexplane-agent-linux-amd64 ./
```

### Running the Agent

Generate an agent secret in **Settings → Agent Configuration** (admin only), then use the one-liner from the Deploy Agent panel (pre-filled with your control plane URL, secret, and platform).

**Ephemeral (run once):**
```bash
curl -fsSL http://localhost:8000/downloads/nexplane-agent-linux-amd64-0.1.0 -o nexplane-agent && chmod +x nexplane-agent
./nexplane-agent --control-plane http://localhost:8000 --secret <your-secret> --mode ephemeral
```

**Persistent service (systemd):**
```ini
[Unit]
Description=Nexplane Agent
After=network.target

[Service]
ExecStart=/usr/local/bin/nexplane-agent \
  --control-plane https://nexplane.acme.example:8000 \
  --secret sk-agent-<your-secret> \
  --mode service \
  --poll-interval 30s
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**Windows Service (PowerShell):**
```powershell
New-Service -Name "NexplaneAgent" `
  -BinaryPathName "C:\nexplane\nexplane-agent.exe --mode service --poll-interval 30s --control-plane https://nexplane.acme.example:8000 --secret <your-secret>" `
  -StartupType Automatic
Start-Service NexplaneAgent
```

**Flag / env var reference:**

| Flag | Env var | Default |
|------|---------|---------|
| `--control-plane` | `NP_CONTROL_PLANE` | (required) |
| `--secret` | `NP_SECRET` | (required) |
| `--mode` | `NP_MODE` | `service` |
| `--poll-interval` | `NP_POLL_INTERVAL` | `30s` |

---

## Settings

| Setting | Who | Notes |
|---------|-----|-------|
| AI Providers (Anthropic, OpenAI) | Admin | API keys per provider; select default. Encrypted at rest. |
| Agent secret | Admin | Shared HMAC secret. Shown once — store securely. Deploy panel pre-fills commands. |
| Connector credentials | Operator+ | Per-connector API credentials. Encrypted, never returned in GET. |
| Remediation policies | Admin | Per-severity: auto-generate CR, auto-approve, SLA days. |
| Maintenance windows | Admin | Cron-scheduled windows when changes are allowed. |

All secrets use `SecretsService` (Fernet AES-256 with versioned rotation), designed for HashiCorp Vault / AWS Secrets Manager / HSM swap-out.

---

## Local Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start PostgreSQL
docker run -d -e POSTGRES_USER=nexplane -e POSTGRES_PASSWORD=nexplane_dev \
  -e POSTGRES_DB=nexplane -p 5432:5432 postgres:16-alpine

# Run migrations
alembic upgrade head

# Seed demo data
python seed.py

# Start API
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Running Tests

```bash
# Backend (200+ tests)
cd backend
pytest

# Agent (22 packages)
cd agent
go test ./...
```

> **Note:** When running via Docker Compose on Windows, Vite's file watcher may not pick up changes. Run:
> `docker compose stop frontend && docker compose up frontend -d`

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://nexplane:nexplane_dev@db:5432/nexplane` | PostgreSQL connection |
| `SECRET_KEY` | (dev key) | JWT + Fernet key derivation — **change in production** |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed CORS origins |
| `ENVIRONMENT` | `development` | Environment name |
| `AI_MODEL` | `claude-sonnet-4-6` | Anthropic model for AI planning |
| `WEBHOOK_SECRET` | (dev key) | HMAC key for vulnerability scanner webhook verification |

---

## Safety Design

| Scenario | Behavior |
|----------|----------|
| Prod + critical asset | `high` or `critical` risk score |
| Missing rollback strategy (prod/critical) | Blocked — cannot generate plan |
| Remote command without approved template | Blocked at safety review |
| Freeform shell command | Blocked unconditionally |
| Critical risk change | Requires 2 approvals (approver + admin) |
| Microsegmentation policy | Staged simulation mode only |
| Agent job with invalid HMAC | Rejected before execution |
| AI not configured | 402 response; UI shows inline guidance |
| Active change freeze | 423 Locked on approve/execute; bypass requires justification + `ir_responder` role |
| Step credential output | Never written to logs or DB — travels in memory only between steps |
| IaC apply | Gated behind explicit plan review and approval |
| DB replica promotion | `rollback_supported: false`; blast radius warning required in approval |

---

## API Documentation

Interactive OpenAPI docs: `http://localhost:8000/docs`

Key endpoint groups:
- `/auth/*` — login, logout, current user
- `/assets/*` — inventory CRUD, tag management, bulk tagging, ingest
- `/connectors/*` — connector CRUD, test, credentials, schedule, ingest
- `/projects/*` — project CRUD, member management, AI chat
- `/change-requests/*` — full CR lifecycle (plan → approve → execute → verify → rollback) + batch progress
- `/runbooks/*` — CRUD, fork, trigger; `/executions/*` — status, resume checkpoint, abort
- `/vulnerability/*` — findings CRUD, policies, SLA dashboard, CVE blast-radius, patch campaign; `POST /webhooks/vulnerability-findings`
- `/ir/*` — IR playbook templates, execute, forensic bundles
- `/access-reviews/*` — collect, decisions, approve, auto-generate removal CRs
- `/compliance/*` — baselines CRUD, drift alerts, evidence ZIP download, freeze windows
- `/maintenance-windows/*` — CRUD, status check
- `/agent/*` — agent registration, job dispatch, result reporting
- `/settings/*` — AI providers, agent secret, remediation policies
- `/downloads/*` — versioned agent binaries + SHA256 checksums + version file (unauthenticated)
- `/audit-events/*` — immutable audit trail
