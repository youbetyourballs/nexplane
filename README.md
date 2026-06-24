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
- **Connectors** — 70+ integrations spanning cloud, identity, EDR, IaC, ticketing, and observability — with real API calls when credentials are configured
- **Nexplane Agent** — a cross-platform Go binary (Linux, Windows, **macOS**) that runs on managed machines, reaches out to the control plane, and executes signed commands — no inbound SSH required
- **Incident Response Playbooks** — pre-defined fast-path workflows for host isolation, account lockdown, evidence preservation, and phishing response
- **Vulnerability Remediation Pipeline** — close the loop between scanner findings and automated remediation
- **Compliance & Governance** — CIS benchmark enforcement, drift detection, change freeze windows, audit evidence collection
- **Security Policy Auto-Generation** — observe live workload behavior, synthesize least-privilege seccomp / AppArmor / SELinux / eBPF policies, and roll them out behind a soak period before enforcement
- **Single Sign-On (OIDC)** — delegate login to an external identity provider per organization, with `local` / `idp` auth modes and optional user auto-provisioning
- **Guided First-Run Setup** — fresh instances bootstrap their first org and admin through a one-time, instance-bound setup token

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
                            │ HTTP/REST (localhost only — not public internet)
┌───────────────────────────▼─────────────────────────────────────────────┐
│  FastAPI Backend  (Python 3.12)                                           │
│                                                                          │
│  Safety Engine · Planning Engine · AI Service · Audit Service            │
│  Secrets Service (Fernet AES-256, HSM/Vault-swappable)                   │
│  SecretsService versioning (rotate + rollback with 7-day TTL)            │
│  RunbookExecutor · IRExecutor · FleetExecutor · IaCExecutor              │
│  VulnRemediationEngine · IdentityResolver · DriftDetection               │
│  SecurityPolicyEngine (seccomp/AppArmor/SELinux/eBPF + soak)            │
│  OIDC Service · SetupGuard middleware · Edition gating                   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ Action Catalog  (per-connector JSON + executor modules)          │   │
│  │   Tier 1: direct_api  ·  Tier 3: agent  ·  Tier 5: ssh          │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Connectors — change actions + ingest (70+ connectors)                   │
│  aws · azure · gcp · cloudflare · okta · paloalto · ssh                 │
│  active_directory · entra_id · crowdstrike · tenable · kubernetes        │
│  tailscale · terraform_local · ansible_local · ...                      │
│                                                                          │
│  Agent API  (/agent/register · /agent/jobs/next · /agent/result)        │
│  IR API  (/ir/templates · /ir/execute · /ir/bundles)                     │
│  Runbook API  (/runbooks · /executions)                                  │
│  Vulnerability API  (/vulnerability/findings · /webhooks/findings)       │
│  Compliance API  (/compliance/baselines · /compliance/freeze-windows)    │
│  Fleet API  (/maintenance-windows)                                       │
│  Identity API  (/access-reviews · /change-requests[offboard/onboard])   │
│  Security Policy API  (/security-policy/soak-sessions · /baselines)      │
│  Auth/SSO API  (/identity-providers · /auth/oidc · /orgs/{id}/auth-mode) │
│  Setup API  (/setup/token · /setup/consume — first-run bootstrap)        │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ SQLAlchemy async
┌───────────────────────────▼─────────────────────────────────────────────┐
│  PostgreSQL 16  (120+ Alembic migrations, 40+ tables)                    │
└──────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │  Nexplane Agent (Go)                  │
                    │  linux/amd64 · linux/arm64 ·          │
                    │  windows/amd64 · darwin/arm64 (macOS) │
                    │                                       │
                    │  Self-updating — fetches version from │
                    │  S3, downloads + SHA256-verifies new  │
                    │  binary atomically via os.Rename +    │
                    │  syscall.Exec                         │
                    │                                       │
                    │  Outbound poll only —                 │
                    │  no inbound SSH needed                │
                    └────────────┬──────────────────────────┘
                                 │ long-poll HTTP (outbound)
                                 └──► /agent/jobs/next

                    ┌──────────────────────────────────────┐
                    │  Agent Binary Distribution            │
                    │  S3: nexplane-agent-downloads         │
                    │  (us-east-1, public read)             │
                    │  Published via scripts/upload-agent-  │
                    │  to-s3.sh after each build            │
                    └──────────────────────────────────────┘
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query v5, React Router v6 |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async, Pydantic v2 |
| Database | PostgreSQL 16 |
| Migrations | Alembic (120+ migrations, 40+ tables) |
| Workflow | Temporal-pattern abstraction (asyncio MVP, Temporal-ready) |
| Auth | JWT + bcrypt; OIDC SSO (per-org identity providers, `local` / `idp` auth modes); one-time setup-token bootstrap |
| Editions | `core` (default) and `commercial` (`NEXPLANE_EDITION`); commercial CR catalog mounted from an overlay path |
| AI | Anthropic Claude + OpenAI (multi-provider, default configurable) |
| Secrets | `cryptography.fernet` (AES-256) with versioning for rotation; abstracted for HSM/Vault swap-out |
| Agent | Go 1.26+, AWS SDK v2, `golang.org/x/sys`, lib/pq, go-sql-driver/mysql, go-mssqldb; cross-compiles for Linux, Windows, and macOS (darwin/arm64) |
| Agent Distribution | AWS S3 (public bucket, `nexplane-agent-downloads`, us-east-1) |
| IaC Runtime | Terraform CLI 1.7.5, Ansible + community.aws, AWS session-manager-plugin |
| VPN | Tailscale (kernel TUN mode in Docker for agent deploy + smoke testing) |
| Connector SDKs | boto3, azure-sdk, google-cloud-*, msal, hvac, kubernetes, falconpy, pytenable, pan-os-python, httpx, paramiko, checkov, google-api-python-client, slack-sdk, PyGithub, google-auth-httplib2, croniter |
| Scheduler | APScheduler 3.x (scanner poll, SLA enforcement, compliance scans, access reviews, maintenance windows, scheduled reboots) |
| Graph | React Flow 11 + @dagrejs/dagre (project dependency visualization) |
| Deployment | Docker Compose (multi-stage build: Go agent binaries → Python backend) |

---

## Quick Start

```bash
git clone <repo-url>
cd nexplane

# Start everything
docker compose up --build

# Frontend:   http://localhost:3000
# Backend:    http://localhost:8000  (localhost only — not exposed publicly)
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
| EC2 | `ec2_launch`, `ec2_start`, `ec2_stop`, `ec2_reboot`, `ec2_terminate`, `key_pair_create`, `key_pair_delete` |
| SSM | `ssm_command` (run any approved SSM document against an EC2 instance) |
| Network | `tailscale_join`, `tailscale_remove` |
| IP Migration | `change_ip`, `migrate_ip`, `ip_campaign` |
| Agent | `deploy_nexplane_agent`, `patch_packages`, `patch_campaign`, `isolate_host`, `rolling_restart`, `canary_config_push`, `distribute_file`, `fleet_health_check` |
| Identity | `offboard_user`, `onboard_user`, `key_rotation`, `rotate_db_credentials`, `rotate_ssh_keys`, `rotate_api_key`, `rotate_service_account` |
| IAM | `iam_user_create`, `iam_user_delete` |
| S3 Storage | `s3_bucket_create`, `s3_bucket_delete`, `s3_lifecycle_configure` |
| DNS (Route53) | `route53_zone_create`, `route53_record_upsert`, `route53_record_delete` |
| RDS | `rds_instance_create`, `rds_instance_delete`, `rds_snapshot_create` |
| Observability | `cloudwatch_alarm_create`, `cloudwatch_alarm_delete` |
| Incident Response | `lockdown_account`, `phishing_response`, `preserve_evidence` |
| IaC (local) | `terraform_local_apply`, `ansible_local_playbook` |
| IaC (remote) | `terraform_apply`, `ansible_playbook`, `helm_upgrade` |
| Database | `provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `promote_db_replica`, `db_connection_config` |
| Backup / Recovery | `create_backup`, `verify_backup`, `restore_files`, `dr_failover`, `scheduled_reboot` |
| Compliance | `enforce_cis_benchmark`, `collect_evidence` |
| Telemetry | `telemetry_agent_deploy`, `remote_command` |
| macOS | `macos_filevault_enable`, `macos_gatekeeper_enable`, `macos_santa_install`, `macos_santa_rule_add`, `macos_santa_mode_set`, `macos_softwareupdate_install`, `macos_profiles_install`, `macos_defaults_write`, `macos_sysinfo` … (23 macOS change types) |
| Security Policy | `apply_seccomp_profile`, `apply_apparmor_profile`, `apply_selinux_policy`, `apply_ebpf_policy` (synthesized from soak sessions) |

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
- **Configurable SLA tiers** — per-org SLA hours per severity (critical/high/medium); changes apply to new findings only (`GET/PUT /vulnerability/sla/config`)
- **Auto-escalation** — background job marks breaches and escalates findings past their threshold (critical: 4h, high: 24h, medium: 72h after breach)
- **SLA tab in UI** — summary cards (total/breached/due-soon per severity), overdue findings list sorted by most-overdue, inline SLA configuration panel
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

### Security Policy Auto-Generation

Generate least-privilege host security policies from observed behavior instead of writing them by hand. The `SecurityPolicyEngine` drives a **soak session** lifecycle (`POST /security-policy/soak-sessions`):

1. **Observe** — agents collect runtime behavior from target assets over a soak window
2. **Synthesize** — a per-backend plugin turns observations into a candidate policy
3. **Diff** — review the proposed policy against the current baseline (`GET /security-policy/soak-sessions/{id}/diff`)
4. **Accept** — approve the diff to emit a hardening change request and persist a new baseline (`POST .../accept`)

**Policy backends (plugins):** `seccomp`, `apparmor`, `selinux`, `ebpf_lsm` (eBPF LSM), `ebpf_network`. Per-project baselines are tracked (`GET/DELETE /security-policy/baselines/{project_id}`) so re-observed behavior is diffed against the last accepted policy. The **Security Policy Soak** panel in the UI surfaces active sessions, the live diff, and the accept action. Project phases support a configurable **soak period** (`soak_hours`, default 72h) so staged rollouts auto-advance only after the observation window closes clean.

### Authentication & SSO

- **Local auth** — JWT + bcrypt, the default for every org
- **OIDC single sign-on** — delegate login to an external identity provider via the authorization-code flow (`GET /auth/oidc/{idp_id}/redirect` → issuer → `GET /auth/oidc/{idp_id}/callback`). Issuer metadata is discovered from `.well-known/openid-configuration`; state is verified on callback
- **Identity provider CRUD** — manage providers per org (`/identity-providers`); `GET /identity-providers/active` is public so the login screen can render "Continue with …" buttons before authentication
- **Per-org auth modes** — `local` or `idp`, switched atomically by an admin (`POST /orgs/{org_id}/auth-mode`). Switching to `idp` activates the chosen provider; switching back to `local` returns providers to `pending` so a tested provider can be re-enabled
- **User provisioning** — on OIDC callback, users are matched by `(email, org)`. With `auto_provision` enabled an unknown email is provisioned as a `security_operator` (login only via the IdP); otherwise login is rejected so admins can pre-create accounts

### First-Run Setup (commercial edition)

Fresh `commercial`-edition instances ship locked until bootstrapped. `SetupGuardMiddleware` 307-redirects all non-exempt traffic to `/setup` until the first user exists (exempt: `/setup`, `/health`, `/docs`, `/openapi.json`, `/api/v1/setup`, …).

- **`POST /setup/token`** — machine-to-machine; authenticated by the `X-Ops-Secret` header (sourced from `NEXPLANE_OPS_SECRET` or SSM `/nexplane/ops/instance-shared-secret`). Mints a one-time, instance-URL-bound setup token (24h TTL) and returns the setup URL
- **`POST /setup/consume`** — unauthenticated; validates the token against the instance URL, creates the first org and admin user (12+ char password), marks the token used, and returns a JWT

### Editions

`NEXPLANE_EDITION` selects the edition (default `core`):

- **`core`** — the full open platform: every connector, change type, runbook, and agent capability documented here
- **`commercial`** — additionally enables the first-run setup token flow, the setup guard, and a **commercial CR catalog** loaded at runtime from `NEXPLANE_COMMERCIAL_CATALOG_PATH`. Commercial executors live outside the `nexplane` package and are mounted into the container, so no commercial code ships in the core image

### IaC Orchestration

Terraform, Ansible, and Helm as tracked, auditable Nexplane change types:

- **Terraform (local)** — runs `terraform init/plan/apply` directly in the backend container with AWS credentials injected; creates S3 buckets and other AWS resources as tracked CRs with rollback via `terraform destroy`
- **Ansible (local)** — `--check` mode as preflight, full run via SSM transport; `community.aws.aws_ssm` connection plugin with `session-manager-plugin`; supports `inventory_content` override for custom targets
- **Terraform (remote)** — two-phase: plan output stored as blast radius → approval gate → apply; rollback via state restore
- **Helm** — `helm upgrade --atomic` with automatic rollback on health check failure; `helm rollback` on demand

The plan output (diff) renders in the change request detail view with green/red coloring.

### Tailscale Integration

The Tailscale connector enables secure mesh networking as a tracked change:

- **`tailscale_join`** — install Tailscale on an EC2 instance via SSM and join the tailnet with a pre-authorized auth key; returns the instance's Tailscale IP
- **`tailscale_remove`** — gracefully remove the node from the tailnet
- **Auth key management** — store a reusable pre-authorized key in the connector credentials; no OAuth complexity

The backend container itself joins the tailnet during smoke tests using kernel TUN mode (`/dev/net/tun`) so EC2 instances can reach the control plane via Tailscale after joining.

### IP Migration

Nexplane orchestrates IP address changes on live hosts with automatic rollback safety. The `change_ip` change type dispatches to the Nexplane agent, which captures a full network snapshot before making any change and restores it on rollback.

**Methods -- applied in order of risk (lowest to highest):**

| # | Method | When to use | Connectivity guarantee |
|---|--------|-------------|----------------------|
| 1 | `tailscale` | Tailscale is active on the host | Agent stays reachable via Tailscale overlay; physical IP change is transparent |
| 2 | `secondary_swap` | Secondary IP can be assigned to the ENI/NIC | Old IP stays active until new IP is confirmed; two-phase commit |
| 3 | `commit_timer` | Tailscale not available; commit timer safety net required | Agent probes control plane after change; auto-rolls back if unreachable within the timer window |
| 4 | `manual` | Planned maintenance with human confirmation | Change is applied; operator confirms before it is committed |

`auto` (the default) selects the lowest-risk method available at execution time.

**Dead man's switch (commit_timer):**

Before applying the change, the agent writes `pending_rollback.json` to disk. A background goroutine probes `{control-plane}/health` every N seconds. If the probe succeeds within the timer window the file is deleted and the change is committed. If the probe never succeeds (or the agent crashes and restarts), `pending_rollback.json` is detected at startup and the original configuration is restored automatically.

The timer window is configurable per CR (`commit_timer_seconds`, default 30s, range 10-300s). The probe URL defaults to the agent's configured control plane URL (`NP_CONTROL_PLANE` env var).

**Multi-host campaigns (`ip_campaign`):**

Orchestrates IP changes across a fleet with `batch_size` control and an abort threshold -- if the error rate across a batch exceeds `abort_error_threshold`, the campaign halts and rolls back completed hosts.

**DNS coordination (`migrate_ip`):**

The `migrate_ip` change type is a multi-stage orchestrator that handles DNS TTL-aware ordering:
- Short TTL records (<= 120s): single CR updates IP and DNS together
- Long TTL records (> 120s): two-CR sequence -- `prepare_dns` lowers TTL first, then `migrate_ip` changes the IP once propagation is confirmed

**IP Migration Wizard (UI):**

Available on server and endpoint asset detail pages via the "Change IP" button in the Network section. Guides the operator through four steps: Configure (interface, new IP, gateway, DNS, method, timer) -> Pre-flight (live dry-run checks via CR) -> Execute (stage progress + commit timer countdown) -> Verify (connectivity confirmation + rollback button).

**Rollback:**

`change_ip_rollback` restores all pre-change state from the snapshot: IP addresses, gateway, routes, DNS servers, and MTU. Rollback can be triggered manually from the CR detail page, automatically by the dead man's switch timer, or by calling `client.rollback_cr()` from the smoke tests.

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

**Cloud & Infrastructure**

| Connector | Key capabilities |
|-----------|-----------------|
| AWS | EC2 lifecycle (launch/stop/start/reboot/terminate); key pairs; IAM users + policies (create/delete/attach/detach/rotate); S3 buckets (create/delete/lifecycle/policy/public-access); Route53 zones + records (create/upsert/delete/DR-failover); RDS instances (create/delete/snapshot/replica); CloudWatch alarms (create/delete); ALB + target groups + listeners (create/delete/modify/register-targets); security groups; EBS snapshots; SSM commands; Tailscale join/remove; agent deploy |
| Azure | VM lifecycle (create/stop/start/reboot/snapshot/delete); NSG rules (update/restore); blob storage accounts + containers (create/delete); managed identities (create/delete); RBAC role assignments (create/delete); VNet + subnets (create/delete); DNS zones + A records (create/delete); SQL Server + Database (create/delete); Monitor metric alerts (create/delete); resource tagging; Entra users (disable/enable, revoke sessions, assign license); Terraform local; Ansible local |
| GCP | Compute lifecycle (create/stop/start/reboot/snapshot/delete); firewall rules (create/delete); storage buckets (block public access); service accounts (disable/rotate key); IAM bindings; SCC findings; Terraform local; Ansible local |
| Cloudflare | WAF; firewall; access policies; block IP; SSL mode; DNS |
| Palo Alto | Address objects/rules/zones; create/delete rules; block IP; commit |
| Tailscale | Join/remove nodes; auth key management; mesh networking |
| Active Directory | Discover/disable stale accounts; move OU; rotate service account passwords |
| CrowdStrike | Isolate host; RTR commands; prevention policy; host containment |
| Tenable | Launch/pause/resume scans; export; trigger remediation verification |
| SSH | Execute approved command templates; check service status; tail logs |
| Nexplane Agent | Full OS hardening + all command packages (see below) |

**IaC & Configuration Management**

| Connector | Key capabilities |
|-----------|-----------------|
| terraform_local | Apply/destroy Terraform plans locally in the backend container; AWS credentials injected |
| ansible_local | Run Ansible playbooks via SSM transport or local connection; check + apply lifecycle |
| Terraform (remote) | Two-phase plan→apply via external Terraform CLI |
| Ansible (remote) | Remote playbook execution |
| Helm | Upgrade/rollback Kubernetes releases |
| AWS CloudFormation, Pulumi, Azure Bicep, Checkov, SaltStack, Chef InSpec | Discovery and execution |

**Identity & Access**

Okta · Microsoft Entra ID · Azure AD (Graph) · Active Directory · LDAP · FreeIPA · Keycloak · Teleport · HashiCorp Vault · Infisical · Microsoft LAPS

**Code & Version Control**

GitHub · GitLab · Gitea · JFrog Xray

**Security Tools / EDR**

CrowdStrike · Microsoft Defender for Endpoint · SentinelOne · Wazuh · Falco

**Vulnerability Scanners**

Tenable · Snyk · Qualys · OpenVAS · Nessus · Wiz · RunZero · Elastic Security

**SaaS**

Google Workspace · Slack · Kubernetes · Helm

**Databases**

PostgreSQL · Redis · MongoDB

**Windows Management**

Microsoft Intune · SCCM / MECM · Windows Update for Business · WinRM

**macOS / MDM & Binary Authorization**

Jamf · MicroMDM · Santa Sync Server

**Network, Firewall & Certificates**

Cloudflare · Palo Alto · OPNsense · Tailscale · step-ca · BIND DNS

**Cloud Security & Discovery**

OCI · RunZero · Wiz · Zscaler

**Workflow & Observability**

Jira · PagerDuty · ServiceNow · Splunk · Datadog · SMTP

---

### Required Permissions

Minimum permissions for each connector when using live credentials with the smoke test suite.

**AWS**

Create an IAM user with programmatic access and attach these managed policies:
- `AmazonEC2FullAccess`
- `AmazonSSMFullAccess`
- `IAMFullAccess`
- `AmazonRDSFullAccess`
- `AmazonRoute53FullAccess`
- `AmazonS3FullAccess`
- `CloudWatchFullAccess`

Also attach or create a policy granting `sts:GetCallerIdentity`.

> **Note:** AWS Free Tier accounts cannot launch Windows EC2 instances (requires a paid account for Windows Server AMIs and `t3.micro` Windows capacity).

**GCP**

Grant the service account these IAM roles on the project:
- `roles/compute.admin`
- `roles/iam.serviceAccountAdmin`
- `roles/iam.serviceAccountKeyAdmin`
- `roles/storage.admin`
- `roles/dns.admin`
- `roles/iam.securityAdmin` (for IAM bindings)

**Azure**

The service principal needs two roles assigned at the subscription scope:
- `Contributor` — required for all resource create/delete/modify operations
- `User Access Administrator` — required for RBAC role assignment operations (Phase W)

> `Contributor` alone is **not sufficient** — role assignment operations fail without `User Access Administrator`.

**Tailscale**

- Use a **reusable** auth key (not single-use). Single-use keys are consumed on the first node join and break subsequent smoke test runs.
- The key must be **pre-authorized** (no manual approval step in the Tailscale admin console).
- Pass the key via `--tailscale-auth-key tskey-auth-<key>` when running smoke tests, or leave it unset and configure the Tailscale connector with the key -- the smoke test runner reads from the connector automatically when no flag is provided. The Smoke Tests UI has the same behavior: leave the Tailscale Auth Key field blank to use the connector key.

### Nexplane Agent

A cross-platform Go binary that reverses the connection direction: the agent polls the control plane for jobs and executes signed commands. No inbound SSH required.

**Distribution:** Binaries are published to a public S3 bucket after each build:
```
https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/
  nexplane-agent-linux-amd64-{VERSION}
  nexplane-agent-linux-arm64-{VERSION}
  nexplane-agent-windows-amd64-{VERSION}.exe
  nexplane-agent-darwin-arm64-{VERSION}
  + .sha256 sidecar for each
  version  (plain text: current version)
```

> The `darwin/arm64` (Apple Silicon) binary is built on macOS hardware and published to the same bucket; the Linux/Windows binaries are produced by the Docker multi-stage build.

**Self-updating:** On startup the agent fetches the `version` file from S3, downloads and SHA256-verifies the new binary if behind, atomically replaces itself via `os.Rename` + `syscall.Exec`. Windows agents log a manual-update message.

**Security model:**
- Bearer token authentication (shared HMAC secret, generated in Settings)
- Each job payload signed with HMAC-SHA256; agent verifies before executing
- Agent registers itself — the managed machine appears as an Asset automatically
- Binaries downloaded from S3 directly by the managed machine — the Nexplane control plane is never a download proxy

**Agent command packages:**

| Package | Commands | Platform |
|---------|----------|----------|
| `changip` | `change_ip` (tailscale/secondary_swap/commit_timer/manual methods, dead man's switch, snapshot+rollback), `change_ip_rollback` | Linux + Windows |
| `linuxpatch` | `apply_linux_patches` (apt/yum/dnf, security-only or CVE-targeted, dry-run, before/after diff), `audit_linux_patch_status` | Linux |
| `winpatch` | `apply_windows_patches` (WUA COM API, specific KB, reboot scheduling), `audit_windows_patch_status` | Windows |
| `isolation` | `isolate_host` (flush iptables/nftables, allow management CIDR only), `restore_network_access` | Linux + Windows |
| `forensics` | `collect_forensics` (auth.log, journal, auditd, netstat, ARP, proc state → tar.gz → S3) | Linux + Windows |
| `compliance` | `audit_cis_compliance` (filesystem, sysctl, SSH, PAM, auditd, SELinux/AppArmor per-control pass/fail), `collect_evidence` | Linux |
| `credrotation` | `rotate_db_credentials` (generate, update DB user, update config, restart service), `rotate_ssh_keys` (fingerprint-based), `update_agent_env_file` | Linux + Windows |
| `iac` | `terraform_plan`, `terraform_apply`, `terraform_rollback`, `ansible_check`, `ansible_run`, `helm_diff`, `helm_upgrade`, `helm_rollback` | Linux |
| `fleet` | `restart_service`, `push_config_file` (with backup), `distribute_file`, `health_check` | Linux + Windows |
| `backup` | `create_backup` (restic), `restore_files` (restic with path filter + SHA256 verify) | Both |
| `reboot` | `graceful_reboot`, `verify_post_reboot` (service health check) | Both |
| `dbadmin` | `provision_db_user`, `deprovision_db_user`, `grant_permissions`, `revoke_permissions`, `configure_db_audit`, `db_connection_config` | PostgreSQL, MySQL, MSSQL |
| `ossecurity` | SELinux, AppArmor, seccomp, sysctl, iptables, kernel modules, mount hardening, auditd, FIM, eBPF | Linux |
| `linuxauth` | PAM, SSH, CA certs, NTP, user/group audit, privesc audit | Linux |
| `winharden` | LAPS, Credential Guard, PowerShell CLM, AppLocker, SMB, BitLocker, Windows Firewall, TLS, RDP, audit policy, registry | Windows |
| `crossplatform` | TLS certificates, DNS resolver, software inventory, syslog forwarding | Linux + Windows + macOS |
| `linuxupgrade` | In-place or containerize-and-migrate OS upgrades | Linux |
| `macos` | FileVault, Gatekeeper, **Santa** binary authorization (install, rules, monitor/lockdown mode, sync, event export), `softwareupdate`, configuration profiles, `defaults`, `launchctl`, Homebrew, system info | macOS |
| `ebpf` | eBPF LSM + network policy posture audit, policy synthesis and deployment | Linux |
| `appdiscovery` / `deepdiscover` | Running-service and listening-port discovery; deep application/dependency mapping | Linux + Windows + macOS |
| `containerizebuild` / `containerizeretire` | Containerize a legacy workload (build + push image) and retire the source host | Linux |

**macOS (darwin/arm64) support:**

The agent runs natively on Apple Silicon macOS and exposes 23 macOS change types across three areas:

- **Posture & encryption** — FileVault status/enable, Gatekeeper status/enable/disable, configuration profiles, system info
- **Binary authorization (Santa)** — install Google/North Pole Security **Santa**, add/remove/list rules, switch monitor ↔ lockdown mode, trigger sync, export decisions, check a binary. When SIP blocks the system extension (e.g. unmanaged EC2 `mac2.metal`), `santa_install` returns `installed: true, activated: false` rather than failing, so the CR still completes and signals that MDM/SIP approval is pending
- **Observability & hardening** — software inventory (`brew`/MacPorts/`pkgutil`), CIS compliance audit (SIP, Gatekeeper, ALF, NTP, SSH, auditd, Santa, screen lock), SSH hardening, NTP via `systemsetup`, syslog forwarding, network isolation via `pfctl`, software-update audit/install, fleet ops via `launchctl`/`brew`

macOS smoke coverage runs against an EC2 `mac2.metal` instance on a Dedicated Host (`MAC_AGENT_BOOTSTRAP`, `MAC_POSTURE_AUDIT`, `MAC_AUTH_HARDENING`, `MAC_OBSERVABILITY` phases). See `docs/runbooks/mac-smoke-dedicated-host-setup.md`.

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
├── VERSION                              # Single source of truth for agent version (0.1.2)
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
│       ├── changip/                     # IP change: tailscale/secondary_swap/commit_timer/manual
│       │                                # changip_linux.go (nmcli + ip addr fallback)
│       │                                # changip_windows.go (netsh)
│       │                                # changip_deadman.go (dead man's switch, platform-neutral)
│       │                                # changip_tailscale.go (Tailscale detection, platform-neutral)
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
│       ├── linuxauth/                   # PAM, SSH, CA certs, NTP, user audit
│       ├── winharden/                   # Windows security hardening suite
│       ├── crossplatform/               # TLS, DNS, software inventory (Linux/Windows/macOS)
│       ├── linuxupgrade/                # Linux in-place and containerize-and-migrate
│       ├── macos/                       # macOS: FileVault, Gatekeeper, Santa, profiles, defaults
│       ├── ebpf/                        # eBPF LSM + network policy posture and deploy
│       ├── appdiscovery/ deepdiscover/  # Service/port discovery + deep dependency mapping
│       └── containerizebuild/ containerizeretire/  # Legacy workload containerization
│
├── backend/
│   ├── Dockerfile                       # Multi-stage: Go agent binaries → Python backend
│   │                                    # Includes: Tailscale, Terraform 1.7.5, Ansible,
│   │                                    # community.aws collection, session-manager-plugin
│   ├── seed.py                          # Demo data (org, users, assets, connectors, CRs, projects)
│   ├── alembic/versions/                # 120+ migrations, 40+ tables
│   └── app/
│       ├── main.py                      # App factory + router registration
│       ├── models/
│       │   ├── change_request.py        # 400+ ChangeType values, fleet/IR status values
│       │   ├── connector.py             # ConnectorType including tailscale/terraform_local/ansible_local
│       │   ├── asset.py                 # AssetType including key_pair, cloud_account, storage_bucket
│       │   ├── setup_token.py           # First-run setup token (one-time, instance-bound, 24h TTL)
│       │   ├── identity_provider.py     # OIDC/LDAP/SAML IdP config; Organization.auth_mode (local/idp)
│       │   └── ...                      # runbook, vulnerability, compliance, maintenance_window, etc.
│       ├── middleware/
│       │   └── setup_guard.py           # Redirect to /setup until first admin exists (commercial)
│       ├── routers/
│       │   ├── setup.py                 # /setup/token (ops-secret) + /setup/consume
│       │   ├── identity_providers.py    # IdP CRUD + /identity-providers/active
│       │   ├── oidc.py                  # /auth/oidc/{idp}/redirect + /callback
│       │   ├── org_auth_mode.py         # /orgs/{id}/auth-mode
│       │   ├── security_policy.py       # /security-policy/soak-sessions + /baselines
│       │   └── ...                      # change_requests, assets, connectors, projects, agent, etc.
│       ├── services/
│       │   ├── oidc_service.py          # build_authorization_url, exchange_code
│       │   ├── security_policy/         # seccomp/apparmor/selinux/ebpf plugins + soak_service
│       │   └── ...                      # ai_service, ingest_service, safety/planning engine
│       ├── workflows/
│       │   └── activities.py            # DB session commit after each step; _auto_asset persistence
│       └── connectors/
│           ├── catalog/                 # Per-connector JSON catalogs (70+ connectors)
│           ├── change_type_definitions/ # 410+ change type JSON definitions
│           └── executors/
│               ├── aws/                 # EC2, IAM, S3, Route53, RDS, CloudWatch, ALB, EBS,
│               │                        # security groups, key pairs, SSM, Tailscale, agent deploy
│               ├── azure/               # VM, NSG, blob storage, managed identity, RBAC, VNet,
│               │                        # DNS, SQL Server/Database, Monitor metric alerts
│               ├── gcp/                 # GCE, firewall, storage, service accounts, IAM, SCC
│               ├── terraform_local/     # terraform_plan_local, terraform_apply_local, terraform_destroy_local
│               ├── ansible_local/       # ansible_check_local, ansible_run_local (_runner.py with
│               │                        # SSM + localhost inventory modes)
│               ├── nexplane_agent/      # Agent command dispatch + patch/compliance/fleet
│               └── ...                  # google_workspace, github, slack, entra_id, kubernetes, etc.
│
├── tests/
│   └── smoke/
│       ├── smoke_helpers.py             # Shared client, constants, cloud SDK helpers
│       ├── test_aws_live.py             # AWS phases A–W (EC2, IAM, S3, Route53, RDS, CW, ALB, agent)
│       ├── test_gcp_live.py             # GCP phases L–R (GCE, firewall, storage, SA, IaC)
│       ├── test_azure_live.py           # Azure phases N–Z (VM, NSG, storage, identity, VNet, DNS, SQL, Monitor)
│       ├── test_agent_live.py           # Agent: 12 Linux command packages × AWS
│       └── test_multicloud_live.py      # Parallel cross-cloud VM lifecycle (AWS + GCP + Azure)
│
├── scripts/
│   └── upload-agent-to-s3.sh           # Extract binaries from Docker image, upload to S3
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Settings.tsx             # AI providers + agent secret + S3 download links
│       │   └── ...                      # all other pages
│       └── ...
│
├── deploy/
│   └── nginx.conf                       # Production HTTPS reverse proxy (TLS, /api + /setup routing)
│
├── helm/nexplane/                       # Helm chart: values.yaml + values-enterprise/-managed
│
├── docs/superpowers/
│   ├── specs/                           # Design specs
│   └── plans/                          # Implementation plans
│
├── docker-compose.yml                   # Dev: backend bound to 127.0.0.1:8000 (not public internet)
└── docker-compose.prod.yml              # Prod: nginx + built frontend + backend + Postgres
```

---

## Building the Agent

The agent is compiled inside the Docker multi-stage build. After building, publish the binaries to S3:

```bash
# Build the Docker image (compiles Go agent for linux/amd64, linux/arm64, windows/amd64)
docker compose build backend

# Upload binaries to S3
./scripts/upload-agent-to-s3.sh
```

The script extracts binaries from the built image and uploads them to `s3://nexplane-agent-downloads` with correct cache headers. The `version` file (60s cache) and immutable binaries (1-year cache) are uploaded separately.

**To build agent locally:**

```bash
cd agent

# Run tests
go test ./...

# Build for current platform
go build -ldflags="-X main.Version=0.1.2" -o dist/nexplane-agent ./

# Cross-compile
GOOS=linux GOARCH=amd64 go build -ldflags="-X main.Version=0.1.2" -o dist/nexplane-agent-linux-amd64 ./
GOOS=darwin GOARCH=arm64 go build -ldflags="-X main.Version=0.1.2" -o dist/nexplane-agent-darwin-arm64 ./
```

### Running the Agent

Generate an agent secret in **Settings → Agent Configuration** (admin only). The Deploy Agent panel pre-fills platform-specific install commands with your control plane URL, secret, and current version — resolved live from S3.

**One-liner (Linux x86_64):**
```bash
VERSION=$(curl -fsSL https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version)
curl -fsSL "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-linux-amd64-${VERSION}" \
  -o nexplane-agent && chmod +x nexplane-agent
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
| `--hostname` | `NP_HOSTNAME` | OS hostname |
| `--poll-interval` | `NP_POLL_INTERVAL` | `30s` |

---

## Smoke Tests

Nexplane has a live smoke test suite that verifies end-to-end functionality against real cloud infrastructure. All smoke test files are in `backend/tests/smoke/` and share infrastructure via `smoke_helpers.py`.

### Test File Structure

| File | Coverage |
|------|----------|
| `smoke_helpers.py` | Shared infrastructure (NexplaneClient, cloud SDK helpers, constants, per-provider client factories) |
| `test_aws_live.py` | AWS phases A-X + IP_A/IP_D/IP_D2: EC2, IAM, S3, Route53, RDS, CloudWatch, ALB, Terraform, Ansible, agent, IP migration |
| `test_gcp_live.py` | GCP phases L–R: GCE, firewall, storage, service accounts, Terraform, Ansible |
| `test_azure_live.py` | Azure phases N–Z: VM, NSG, blob storage, managed identity, RBAC, VNet, DNS, SQL Database, Monitor alerts, tagging, Terraform, Ansible |
| `test_agent_live.py` | All Linux agent command groups (12 packages) × AWS; stub tracks for GCP/Azure/Windows |
| `test_multicloud_live.py` | Parallel cross-cloud VM lifecycle — AWS + GCP + Azure run concurrently with per-provider rollback stacks |

### Running Smoke Tests

All smoke test files are independently runnable from inside the backend container:

```bash
# AWS phases A-D (default, no slow RDS/EC2-stop phases)
# --tailscale-auth-key is optional; if omitted the Tailscale connector key is used automatically
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --email admin@acme.example --password admin123 \
  --phases A,B,C,D

# AWS IP migration phases (Linux, runs after Phase A -- shares the same EC2 instance)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --email admin@acme.example --password admin123 \
  --phases A,IP_A,IP_D,IP_D2

# AWS IP migration phases (Windows -- slow, ~15 min per phase)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --email admin@acme.example --password admin123 \
  --phases IP_WIN_A,IP_WIN_D

# AWS new phases P-T (IAM advanced, S3 policy, DNS failover, agent lifecycle)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --email admin@acme.example --password admin123 \
  --phases P,Q,R,T

# GCP phases L-M (GCE launch + advanced operations)
docker exec nexplane-backend-1 python tests/smoke/test_gcp_live.py \
  --email admin@acme.example --password admin123 \
  --phases L,M --gcp-project <project-id>

# GCP new phases N-R (firewall, storage, service accounts, Terraform, Ansible)
docker exec nexplane-backend-1 python tests/smoke/test_gcp_live.py \
  --email admin@acme.example --password admin123 \
  --phases N,O,P,Q,R --gcp-project <project-id>

# Azure VM + NSG + storage + identity + VNet + DNS + Terraform + Ansible
docker exec nexplane-backend-1 python tests/smoke/test_azure_live.py \
  --email admin@acme.example --password admin123 \
  --phases N,O,P,Q,R,S,T,U,V,W,X --azure-resource-group nexplane-smoke-rg

# Azure SQL + Monitor (slow — ~10 min for SQL server provisioning)
docker exec nexplane-backend-1 python tests/smoke/test_azure_live.py \
  --email admin@acme.example --password admin123 \
  --phases Y,Z --azure-resource-group nexplane-smoke-rg

# Multi-cloud parallel (AWS + GCP + Azure simultaneously, ~15-25 min)
docker exec nexplane-backend-1 python tests/smoke/test_multicloud_live.py \
  --email admin@acme.example --password admin123 \
  --providers aws,gcp,azure \
  --gcp-project <project-id> \
  --azure-resource-group nexplane-smoke-rg

# Agent — all Linux command groups on AWS
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --email admin@acme.example --password admin123 \
  --cloud aws --os linux \
  --tailscale-auth-key tskey-auth-<key>

# Agent — specific phase on AWS only
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --email admin@acme.example --password admin123 \
  --cloud aws --os linux --phases ossecurity,linuxauth \
  --tailscale-auth-key tskey-auth-<key>
```

### Agent Command Coverage

The `test_agent_live.py` file tests all Linux agent command packages against AWS (EC2 + SSM):

| Package | Commands |
|---------|----------|
| `linux_patch` | `audit_linux_patch_status`, `apply_linux_patches` |
| `ossecurity` | `configure_selinux`, `configure_seccomp`, `apply_sysctl_hardening`, `configure_host_firewall`, `blacklist_kernel_modules`, `harden_mount_options`, `deploy_auditd_rules`, `setup_file_integrity_monitoring`, `audit_os_security_posture`, `audit_ebpf_posture`, `configure_ebpf_security_policy`, `deploy_ebpf_policy` |
| `linuxauth` | `harden_ssh`, `configure_pam`, `manage_ca_certificates`, `configure_ntp`, `audit_users_and_groups`, `audit_privesc_vulnerabilities` |
| `crossplatform` | `harden_tls_protocols`, `configure_dns_resolver`, `audit_software_inventory`, `configure_syslog` |
| `compliance` | `audit_cis_compliance`, `collect_evidence` |
| `forensics` | forensics bundle collection |
| `fleet` | `restart_service`, `push_config_file`, `health_check` |
| `backup` | `create_backup`, `restore_files` |
| `reboot` | `graceful_reboot` (check only), `verify_post_reboot` |
| `credrotation` | `update_agent_env_file`, `rotate_ssh_keys` |
| `iac` | `terraform_plan` (non-destructive) |
| `linuxupgrade` | `estimate_image_size` (non-destructive) |

Windows tracks (`win_patch`, `winharden`) are implemented for AWS and are stubs for GCP/Azure pending Sub-project B completion.

### Multi-Cloud Parallel Test

`test_multicloud_live.py` runs the full VM lifecycle (launch → stop → start → snapshot → delete) across AWS, GCP, and Azure simultaneously using `ThreadPoolExecutor`. Each cloud worker has its own `NexplaneClient`, rollback stack, and failure handling — a failure in one cloud does not prevent cleanup in others. A consolidated pass/fail report is printed at the end.

```bash
# Run all three providers in parallel
docker exec nexplane-backend-1 python tests/smoke/test_multicloud_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example --password admin123 \
  --providers aws,gcp,azure \
  --gcp-project <project-id> \
  --azure-resource-group nexplane-smoke-rg

# Run a subset (e.g. just AWS + Azure)
  --providers aws,azure
```

---

## Live AWS Smoke Test

End-to-end integration test that creates and destroys real AWS resources against a live account. Verifies the full chain from Nexplane CR → AWS API → asset inventory. All phases use the **rollback stack pattern** — each CR is pushed to a LIFO stack; the `finally` block triggers Nexplane's own rollback system in reverse order, followed by boto3 safety-net cleanup.

```bash
# Default phases -- no RDS, ~15-25 minutes. Tailscale key pulled from connector if omitted.
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,K,IP_A,IP_D,IP_D2

# Full run including RDS and Windows IP phases (~60 min)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,J,K,IP_A,IP_D,IP_D2,IP_WIN_A,IP_WIN_D
```

**Prerequisites:**
- AWS connector configured with credentials covering EC2, SSM, IAM, S3, Route53, RDS, CloudWatch
- `NexplaneEC2TestProfile` IAM instance profile with `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`
- Tailscale connector configured with a reusable pre-authorized auth key
- Agent secret generated in Settings

**Phases:**

| Phase | What it tests | AWS resources created | Requires |
|-------|--------------|----------------------|---------|
| **A** | Key pair create → EC2 launch → SSM → Tailscale join → Agent deploy from S3 | EC2 instance, key pair | — |
| **B** | SSM patch audit, system info, CloudWatch agent install | — | Phase A |
| **C** | Terraform local: S3 bucket create + destroy | S3 bucket | — |
| **D** | Ansible local playbook CR + htop install/remove via SSM | — | Phase A |
| **E** | EC2 stop/start/reboot + EBS snapshot; rollback stack cleanup | EBS snapshot (deleted) | Phase A |
| **F** | Security group rule add/remove via CR; boto3 verification | Security group (deleted) | — |
| **G** | IAM user create → attach policy → rotate key → disable/enable → detach → delete | IAM user (deleted) | — |
| **H** | S3 bucket create → lifecycle → bucket policy → public access block → delete | S3 bucket (deleted) | — |
| **I** | Route53 private zone + A record create/update/delete; boto3 verification | Hosted zone (deleted) | — |
| **J** | RDS db.t3.micro create → snapshot → verify → delete (~25–35 min) | RDS instance + snapshot (deleted) | — |
| **K** | CloudWatch alarms create -> trigger via SSM custom metric -> verify ALARM state -> rollback | CloudWatch alarms (deleted) | Phase A |
| **IP_A** | change_ip (tailscale method) on dummy interface -> verify new IP applied -> rollback -> verify restored | dummy NM interface (deleted) | Phase A |
| **IP_D** | change_ip (commit_timer, success path) -> verify timer cancelled -> verify new IP -> rollback | dummy NM interface (deleted) | Phase A |
| **IP_D2** | change_ip (commit_timer, unreachable probe URL) -> wait for dead man's switch to fire -> verify original IP restored | dummy NM interface (deleted) | Phase A |
| **IP_DNS** | migrate_ip with Route53 DNS update -> verify A record updated -> rollback -> verify A record reverted | Route53 A record (deleted); skipped if no test zone | Phase A |
| **IP_WIN_A** | Windows EC2: change_ip (tailscale method) -> verify new IP via PowerShell -> rollback | Windows EC2 (terminated) | -- (slow) |
| **IP_WIN_D** | Windows EC2: change_ip (commit_timer, success) -> verify timer file gone -> verify new IP -> rollback | Windows EC2 (terminated) | -- (slow) |

**IP phase design notes:**

- IP_A, IP_D, IP_D2 use a temporary dummy network interface (`ip link add type dummy`) so the real EC2 ENI IP is never changed, which would break SSM and Tailscale connectivity on AWS.
- IP_WIN_A and IP_WIN_D spin up their own Windows EC2 instance with Tailscale and the Nexplane agent installed. They are in `slow_phases` and opt-in via the "Include slow phases" checkbox in the Smoke Tests UI.
- IP_DNS is skipped gracefully if the Route53 hosted zone `smoke.nexplane.internal` does not exist.
- All IP phases use the agent server asset (not the raw EC2 inventory asset) as the CR target, because `change_ip` dispatches to the agent registration, not to the cloud connector.

All resources are created under `nexplane-smoke-*` / `nexplane-smoke-test-*` naming prefixes and cleaned up even on failure. The `cleanup()` function in Phase A handles EC2/key-pair teardown; each phase's `finally` block handles its own resources via the rollback stack + boto3 safety net.

**Rollback stack pattern:**
Each phase maintains a `rollback_stack: list[tuple[str, str]]` of `(cr_id, label)`. On success, resources are deleted by triggering rollback of the creation CRs (exercising Nexplane's own rollback system). On failure, the `finally` block iterates `reversed(rollback_stack)` calling `client.rollback_cr()`, followed by direct boto3 cleanup as a safety net.

**Run individual phases:**
```bash
# Phase A only (quickest — ~8 minutes for EC2 + agent)
--phases A

# Phase C only (Terraform, no EC2 needed — ~2 minutes)
--phases C

# Phases F, G, H standalone (no EC2 needed)
--phases F
--phases G
--phases H

# Phase J standalone (RDS — ~30 minutes, costs ~$0.02)
--phases J
```

---

## Settings

| Setting | Who | Notes |
|---------|-----|-------|
| AI Providers (Anthropic, OpenAI) | Admin | API keys per provider; select default. Encrypted at rest. |
| Agent secret | Admin | Shared HMAC secret. Shown once — store securely. Deploy panel pre-fills commands with S3 download URL. |
| Connector credentials | Operator+ | Per-connector API credentials. Encrypted, never returned in GET. |
| Remediation policies | Admin | Per-severity: auto-generate CR, auto-approve, SLA days. |
| Maintenance windows | Admin | Cron-scheduled windows when changes are allowed. |
| Identity providers (SSO) | Admin | OIDC provider config per org; switch org `auth_mode` between `local` and `idp`. |

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
# Backend unit tests (200+ tests)
cd backend
pytest

# Agent tests (29 command packages)
cd agent
go test ./...

# Live AWS smoke test (requires AWS credentials + real account)
# Tailscale key is optional -- omit to use the Tailscale connector key
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,K,IP_A,IP_D,IP_D2
# With RDS (~30 min extra): add J
# With Windows IP phases (~15 min per): add IP_WIN_A,IP_WIN_D
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
| `WEBHOOK_SECRET` | (dev key) | HMAC key for vulnerability scanner webhook verification (must be ≥16 chars outside development) |
| `NEXPLANE_AGENT_DOWNLOAD_URL` | `https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com` | S3 base URL for agent binary downloads; override for self-hosted distributions |
| `NEXPLANE_EDITION` | `core` | Edition gate — `core` or `commercial` (enables setup flow + commercial CR catalog) |
| `NEXPLANE_COMMERCIAL_CATALOG_PATH` | (unset) | Path to the mounted commercial CR catalog/executors (commercial edition only) |
| `NEXPLANE_OPS_SECRET` | (unset) | Shared secret for the `X-Ops-Secret` setup-token endpoint; falls back to SSM `/nexplane/ops/instance-shared-secret` |
| `INSTANCE_URL` | `http://localhost:8000` | Public instance URL used for OIDC redirect URIs, setup links, and email |

---

## Production Deployment

The default `docker-compose.yml` is for local development. For a hardened single-node install, use the production stack, which adds an HTTPS reverse proxy and a built (non-dev) frontend:

```bash
docker compose -f docker-compose.prod.yml up -d
```

**What the production stack adds:**

- **`nginx` reverse proxy** (`deploy/nginx.conf`) — terminates TLS (1.2+, strong ciphers; certs at `/etc/nginx/certs/{fullchain,privkey}.pem`), 301-redirects HTTP→HTTPS, proxies `/api/*` (strips the `/api` prefix) and `/setup/*` to the backend, and serves the SPA from the frontend
- **Built frontend** (`frontend/Dockerfile.prod`) — Vite build served by nginx, with `VITE_API_URL` / `VITE_AGENT_DOWNLOAD_URL` baked in at build time
- **Backend** — runs Alembic `upgrade head` on startup, then uvicorn with 2 workers; same image as dev (bundles Tailscale, Terraform, Ansible, the SSM plugin, and Helm)
- **Postgres 16** with a healthcheck gate

Set `SECRET_KEY` (≥32 chars), `WEBHOOK_SECRET` (≥16 chars), `INSTANCE_URL`, and `CORS_ORIGINS` for the deployment, plus SMTP variables if email is used.

### Kubernetes (Helm)

A Helm chart lives in `helm/nexplane/` with three values profiles:

| Profile | Use | Notes |
|---------|-----|-------|
| `values.yaml` | Default / single-node | Bundled Postgres + Redis, 1 replica, ingress off |
| `values-enterprise.yaml` | Self-hosted enterprise | External Postgres + Redis, 2 replicas, ingress + TLS |
| `values-managed.yaml` | Nexplane-managed | Enterprise layout with tuned resource requests/limits |

```bash
helm install nexplane ./helm/nexplane -f helm/nexplane/values-enterprise.yaml
```

### Editions & the commercial overlay

`core` is fully self-contained. To run a `commercial` instance, set `NEXPLANE_EDITION=commercial` and mount the commercial CR catalog/executors at `NEXPLANE_COMMERCIAL_CATALOG_PATH`. Commercial deployments support two delivery models: **managed** (Nexplane provisions one isolated instance per client) and **self-hosted** (the customer runs the Docker Compose bundle, a VM image, or the Helm chart). Either way, a fresh instance is bootstrapped through the first-run setup-token flow.

---

## Security Design

### Network Exposure

The backend API binds to `127.0.0.1:8000` — it is not exposed to the public internet. The frontend (port 3000) is the only externally accessible service in the default Docker Compose configuration. Agent binary distribution is handled entirely by S3, not by the backend.

### Safety Engine

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
| Unconfigured commercial instance | All non-exempt traffic 307-redirected to `/setup` until the first admin exists |
| Setup token | One-time, instance-URL-bound, 24h TTL; `/setup/token` requires the `X-Ops-Secret` header |
| OIDC login, unknown email | Rejected unless the provider has `auto_provision` enabled |
| Security policy rollout | Enforced only after the soak window closes; the synthesized diff must be reviewed and accepted first |

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
- `/security-policy/*` — soak sessions (create/stop/diff/accept), per-project baselines
- `/identity-providers/*` — OIDC provider CRUD; `/identity-providers/active` (public)
- `/auth/oidc/{idp_id}/*` — authorization-code redirect + callback
- `/orgs/{id}/auth-mode` — switch org auth mode (`local` / `idp`)
- `/setup/*` — first-run bootstrap: `token` (ops-secret) + `consume` (commercial edition)
- `/agent/*` — agent registration, job dispatch, result reporting
- `/settings/*` — AI providers, agent secret, remediation policies
- `/downloads/*` — versioned agent binaries + SHA256 checksums + version file (served from backend for development; production uses S3)
- `/audit-events/*` — immutable audit trail
