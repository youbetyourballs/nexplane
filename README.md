# Nexplane

**The control plane for security execution.**

Nexplane connects intent to action across your infrastructure â€” enabling security engineering and architecture teams to execute infrastructure changes safely, without routing work through sysadmin, network admin, or SRE queues.

> *Execute with Confidence*

---

## What It Does

Security teams identify issues and need to act on them: rotate compromised keys, isolate a compromised endpoint, tighten firewall rules after a scan, deploy an EDR sensor to unprotected hosts, enforce MFA, offboard a departing employee. Today, all of that flows through tickets.

Nexplane gives security teams a governed execution layer:

- **Projects** â€” group related change requests into a sequenced plan with dependency tracking
- **AI Planning Assistant** â€” describe your goal, get a structured change plan with proposed change requests
- **Change Requests** â€” safety-reviewed, approval-gated, audited, with automatic rollback
- **Composable Runbooks** â€” chain change types into reusable multi-step workflows with conditional branching and human checkpoints
- **Asset Inventory** â€” servers, cloud accounts, firewalls, identities, applications â€” discoverable via connectors
- **Connectors** â€” 38+ integrations spanning cloud, identity, EDR, IaC, ticketing, and observability â€” with real API calls when credentials are configured
- **Nexplane Agent** â€” a cross-platform Go binary that runs on managed machines, reaches out to the control plane, and executes signed commands â€” no inbound SSH required
- **Incident Response Playbooks** â€” pre-defined fast-path workflows for host isolation, account lockdown, evidence preservation, and phishing response
- **Vulnerability Remediation Pipeline** â€” close the loop between scanner findings and automated remediation
- **Compliance & Governance** â€” CIS benchmark enforcement, drift detection, change freeze windows, audit evidence collection

---

## Architecture

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  React Frontend  (Vite + TypeScript + Tailwind CSS)                      â”‚
â”‚                                                                          â”‚
â”‚  Dashboard Â· Projects Â· Change Requests Â· Approvals Â· Asset Inventory   â”‚
â”‚  Connectors Â· Settings Â· Runbooks Â· Compliance Â· Incident Response       â”‚
â”‚  Vulnerability Remediation Â· Access Reviews Â· Maintenance Windows        â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                            â”‚ HTTP/REST (localhost only â€” not public internet)
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  FastAPI Backend  (Python 3.12)                                           â”‚
â”‚                                                                          â”‚
â”‚  Safety Engine Â· Planning Engine Â· AI Service Â· Audit Service            â”‚
â”‚  Secrets Service (Fernet AES-256, HSM/Vault-swappable)                   â”‚
â”‚  SecretsService versioning (rotate + rollback with 7-day TTL)            â”‚
â”‚  RunbookExecutor Â· IRExecutor Â· FleetExecutor Â· IaCExecutor              â”‚
â”‚  VulnRemediationEngine Â· IdentityResolver Â· DriftDetection               â”‚
â”‚                                                                          â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
â”‚  â”‚ Action Catalog  (per-connector JSON + executor modules)          â”‚   â”‚
â”‚  â”‚   Tier 1: direct_api  Â·  Tier 3: agent  Â·  Tier 5: ssh          â”‚   â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
â”‚                                                                          â”‚
â”‚  Connectors â€” change actions + ingest (38+ connectors)                   â”‚
â”‚  aws Â· azure Â· gcp Â· cloudflare Â· okta Â· paloalto Â· ssh                 â”‚
â”‚  active_directory Â· entra_id Â· crowdstrike Â· tenable Â· kubernetes        â”‚
â”‚  tailscale Â· terraform_local Â· ansible_local Â· ...                      â”‚
â”‚                                                                          â”‚
â”‚  Agent API  (/agent/register Â· /agent/jobs/next Â· /agent/result)        â”‚
â”‚  IR API  (/ir/templates Â· /ir/execute Â· /ir/bundles)                     â”‚
â”‚  Runbook API  (/runbooks Â· /executions)                                  â”‚
â”‚  Vulnerability API  (/vulnerability/findings Â· /webhooks/findings)       â”‚
â”‚  Compliance API  (/compliance/baselines Â· /compliance/freeze-windows)    â”‚
â”‚  Fleet API  (/maintenance-windows)                                       â”‚
â”‚  Identity API  (/access-reviews Â· /change-requests[offboard/onboard])   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                            â”‚ SQLAlchemy async
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  PostgreSQL 16  (37+ Alembic migrations, 30+ tables)                     â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                    â”‚  Nexplane Agent (Go)                  â”‚
                    â”‚  linux/amd64 Â· arm64 Â· windows/amd64 â”‚
                    â”‚                                       â”‚
                    â”‚  Self-updating â€” fetches version from â”‚
                    â”‚  S3, downloads + SHA256-verifies new  â”‚
                    â”‚  binary atomically via os.Rename +    â”‚
                    â”‚  syscall.Exec                         â”‚
                    â”‚                                       â”‚
                    â”‚  Outbound poll only â€”                 â”‚
                    â”‚  no inbound SSH needed                â”‚
                    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                 â”‚ long-poll HTTP (outbound)
                                 â””â”€â”€â–º /agent/jobs/next

                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                    â”‚  Agent Binary Distribution            â”‚
                    â”‚  S3: nexplane-agent-downloads         â”‚
                    â”‚  (us-east-1, public read)             â”‚
                    â”‚  Published via scripts/upload-agent-  â”‚
                    â”‚  to-s3.sh after each build            â”‚
                    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query v5, React Router v6 |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async, Pydantic v2 |
| Database | PostgreSQL 16 |
| Migrations | Alembic (22+ migrations, 30+ tables) |
| Workflow | Temporal-pattern abstraction (asyncio MVP, Temporal-ready) |
| Auth | JWT + bcrypt |
| AI | Anthropic Claude + OpenAI (multi-provider, default configurable) |
| Secrets | `cryptography.fernet` (AES-256) with versioning for rotation; abstracted for HSM/Vault swap-out |
| Agent | Go 1.26+, AWS SDK v2, `golang.org/x/sys`, lib/pq, go-sql-driver/mysql, go-mssqldb |
| Agent Distribution | AWS S3 (public bucket, `nexplane-agent-downloads`, us-east-1) |
| IaC Runtime | Terraform CLI 1.7.5, Ansible + community.aws, AWS session-manager-plugin |
| VPN | Tailscale (kernel TUN mode in Docker for agent deploy + smoke testing) |
| Connector SDKs | boto3, azure-sdk, google-cloud-*, msal, hvac, kubernetes, falconpy, pytenable, pan-os-python, httpx, paramiko, checkov, google-api-python-client, slack-sdk, PyGithub, google-auth-httplib2, croniter |
| Scheduler | APScheduler 3.x (scanner poll, SLA enforcement, compliance scans, access reviews, maintenance windows, scheduled reboots) |
| Graph | React Flow 11 + @dagrejs/dagre (project dependency visualization) |
| Deployment | Docker Compose (multi-stage build: Go agent binaries â†’ Python backend) |

---

## Quick Start

```bash
git clone <repo-url>
cd nexplane

# Start everything
docker compose up --build

# Frontend:   http://localhost:3000
# Backend:    http://localhost:8000  (localhost only â€” not exposed publicly)
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

Full lifecycle: Draft â†’ Planned â†’ Awaiting Approval â†’ Approved â†’ Executing â†’ Verifying â†’ Completed / Rolled Back.

**Change types:**

| Category | Types |
|----------|-------|
| Infrastructure | `dns_update`, `security_group_update`, `microsegmentation_policy`, `snapshot_asset` |
| EC2 | `ec2_launch`, `ec2_start`, `ec2_stop`, `ec2_reboot`, `ec2_terminate`, `key_pair_create`, `key_pair_delete` |
| SSM | `ssm_command` (run any approved SSM document against an EC2 instance) |
| Network | `tailscale_join`, `tailscale_remove` |
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

### Composable Runbooks

User-defined, reusable multi-step workflows that chain existing change types with conditional logic:

- **Step types:** `change` (creates a change request), `condition` (branches on step results), `human_checkpoint` (pauses for operator confirmation), `parallel_group` (runs steps concurrently)
- **Failure handling:** per-step `on_failure: abort | continue | rollback_all`
- **Versioning:** each edit creates a new version; active executions keep their version snapshot
- **Seed templates:** Engineer Onboarding, Account Compromise IR, Patch Campaign

### Incident Response Playbooks

Pre-built, fast-path change workflows for common incident types â€” launched from a single click:

- **Host Isolation** â€” flush iptables/nftables (Linux) or Windows Firewall rules, allow only management CIDR + control plane, record pre-isolation state for rollback
- **Account Lockdown** â€” disable across AD, Okta, Entra ID, Google Workspace, GitHub, Slack simultaneously
- **Evidence Preservation** â€” collect auth logs, journal, auditd, netstat, ARP, process state â†’ tar.gz â†’ S3 pre-signed upload; runs *before* any remediation
- **Phishing Response** â€” block sender domain, force password reset for affected users, revoke sessions, force MFA re-enrollment

IR change requests get an expedited approval path. Forensic bundles are stored in the `forensic_bundles` table and downloadable via the IR tab.

### Vulnerability Remediation Pipeline

Closes the loop between scanner findings and automated fixes:

- **Webhook ingest** (`POST /vulnerability/webhooks/vulnerability-findings`, HMAC-verified) or scheduled scanner poll
- **Asset matching** â€” maps findings to Nexplane assets by IP/hostname
- **Auto-generate draft CRs** â€” based on `RemediationPolicy` rules (finding type â†’ change type â†’ approval level)
- **CVE blast-radius UI** â€” enter a CVE, see all affected assets, generate a patch campaign in one click
- **Configurable SLA tiers** â€” per-org SLA hours per severity (critical/high/medium); changes apply to new findings only (`GET/PUT /vulnerability/sla/config`)
- **Auto-escalation** â€” background job marks breaches and escalates findings past their threshold (critical: 4h, high: 24h, medium: 72h after breach)
- **SLA tab in UI** â€” summary cards (total/breached/due-soon per severity), overdue findings list sorted by most-overdue, inline SLA configuration panel
- **Policy editor** â€” configure per-severity: auto-generate (yes/no), auto-approve (yes/no), SLA days

### Identity Lifecycle

- **User Offboarding (Kill Switch)** â€” one change request atomically disables across AD, Okta, Entra ID, Google Workspace, GitHub, Slack, and optionally isolates CrowdStrike-managed endpoints in 5 ordered phases
- **User Onboarding** â€” provision accounts across all connected identity systems from a single form
- **Access Reviews** â€” periodic campaigns: collect current group memberships across all identity connectors, present for manager review, auto-generate removal change requests for revoked access

### Secret & Credential Rotation

- **DB credential rotation** â€” generate new password, update DB user, update app config files, restart service, verify connection; with rollback
- **SSH key fleet rotation** â€” remove old key by fingerprint from `authorized_keys` across a fleet, add new public key, backup for rollback
- **API key rotation** â€” rotate AWS IAM key, Okta API token, or GitHub PAT; propagate to Kubernetes secrets, agent env files, SSM Parameter Store
- **Service account rotation** â€” update password in AD/Okta, push to dependent services via asset metadata
- **Step output propagation** â€” credentials travel in memory between steps only, never written to logs or DB

### Fleet Operations

- **Rolling Restart** â€” restart a service across a fleet N hosts at a time; abort if failure rate exceeds threshold; health check between batches
- **Canary Config Push** â€” deploy config to 1 host, run verification command, roll to rest if pass; rollback restores original files
- **Bulk File Distribution** â€” push CA certs, `authorized_keys`, scripts, config files to a fleet
- **Fleet Health Check** â€” disk, load, service status, pending-reboot check across all target hosts before major operations
- **Maintenance Windows** â€” define cron-schedule windows; approved CRs arriving outside windows queue and auto-execute when the window opens

### Compliance & Governance

- **CIS Benchmark Campaigns** â€” audit all CIS controls (filesystem, sysctl, SSH, PAM, auditd, SELinux/AppArmor) then apply remediation; before/after compliance score
- **Drift Detection** â€” weekly scheduled scan compares hosts to their baseline; creates draft remediation CRs for drifted controls
- **Change Freeze Enforcement** â€” declare a freeze window; approve/execute endpoints return 423 Locked; emergency bypass with mandatory justification header and audit log
- **Audit Evidence Collection** â€” request evidence for a SOC2/PCI/ISO27001 control; agent collects config files and command outputs; backend packages as downloadable ZIP

### IaC Orchestration

Terraform, Ansible, and Helm as tracked, auditable Nexplane change types:

- **Terraform (local)** â€” runs `terraform init/plan/apply` directly in the backend container with AWS credentials injected; creates S3 buckets and other AWS resources as tracked CRs with rollback via `terraform destroy`
- **Ansible (local)** â€” `--check` mode as preflight, full run via SSM transport; `community.aws.aws_ssm` connection plugin with `session-manager-plugin`; supports `inventory_content` override for custom targets
- **Terraform (remote)** â€” two-phase: plan output stored as blast radius â†’ approval gate â†’ apply; rollback via state restore
- **Helm** â€” `helm upgrade --atomic` with automatic rollback on health check failure; `helm rollback` on demand

The plan output (diff) renders in the change request detail view with green/red coloring.

### Tailscale Integration

The Tailscale connector enables secure mesh networking as a tracked change:

- **`tailscale_join`** â€” install Tailscale on an EC2 instance via SSM and join the tailnet with a pre-authorized auth key; returns the instance's Tailscale IP
- **`tailscale_remove`** â€” gracefully remove the node from the tailnet
- **Auth key management** â€” store a reusable pre-authorized key in the connector credentials; no OAuth complexity

The backend container itself joins the tailnet during smoke tests using kernel TUN mode (`/dev/net/tun`) so EC2 instances can reach the control plane via Tailscale after joining.

### Database Administration

Agent commands for PostgreSQL, MySQL, and MSSQL (Windows):
- Provision/deprovision DB users with scoped grants
- Grant/revoke permissions
- Configure audit logging (pg_audit, general_log, SQL Audit)
- Connection limit management
- RDS read replica promotion (AWS connector, with Route53 CNAME update)

### Backup & Recovery

- **On-demand backup** â€” restic-based backup to S3 (Linux/Windows agent) or EBS/RDS snapshot (AWS connector)
- **Backup verification** â€” restore RDS snapshot to temp instance, run health query, terminate; confirms backup integrity
- **File-level restore** â€” restic restore with path filter + SHA256 checksum verification
- **DR Failover** â€” Route53 weighted routing update with per-step RTO timestamps; reports actual vs target RTO

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
| Azure | VM lifecycle (create/stop/start/reboot/snapshot/delete); NSG rules (update/restore); Blob storage (account + container create/delete); Managed identities (create/delete); RBAC role assignments (create/delete); VNet + subnets (create/delete); DNS zones + A records (create/delete); SQL Server + Database (create/delete); Monitor metric alerts (create/delete); resource tagging; Entra users (disable/enable, revoke sessions, assign license); Terraform local; Ansible local |
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
| Terraform (remote) | Two-phase planâ†’apply via external Terraform CLI |
| Ansible (remote) | Remote playbook execution |
| Helm | Upgrade/rollback Kubernetes releases |
| AWS CloudFormation, Pulumi, Azure Bicep, Checkov, SaltStack, Chef InSpec | Discovery and execution |

**Identity & Access**

Okta Â· Microsoft Entra ID Â· HashiCorp Vault Â· GitHub

**SaaS**

Google Workspace Â· Slack Â· Kubernetes Â· Helm

**Security Tools / EDR**

SentinelOne Â· Microsoft Defender for Endpoint Â· Snyk Â· Qualys

**Cloud Security & Discovery**

RunZero Â· Wiz Â· Zscaler

**Workflow & Observability**

Jira Â· PagerDuty Â· ServiceNow Â· Splunk Â· Datadog

---

### Nexplane Agent

A cross-platform Go binary that reverses the connection direction: the agent polls the control plane for jobs and executes signed commands. No inbound SSH required.

**Distribution:** Binaries are published to a public S3 bucket after each build:
```
https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/
  nexplane-agent-linux-amd64-{VERSION}
  nexplane-agent-linux-arm64-{VERSION}
  nexplane-agent-windows-amd64-{VERSION}.exe
  + .sha256 sidecar for each
  version  (plain text: current version)
```

**Self-updating:** On startup the agent fetches the `version` file from S3, downloads and SHA256-verifies the new binary if behind, atomically replaces itself via `os.Rename` + `syscall.Exec`. Windows agents log a manual-update message.

**Security model:**
- Bearer token authentication (shared HMAC secret, generated in Settings)
- Each job payload signed with HMAC-SHA256; agent verifies before executing
- Agent registers itself â€” the managed machine appears as an Asset automatically
- Binaries downloaded from S3 directly by the managed machine â€” the Nexplane control plane is never a download proxy

**Agent command packages:**

| Package | Commands | Platform |
|---------|----------|----------|
| `linuxpatch` | `apply_linux_patches` (apt/yum/dnf, security-only or CVE-targeted, dry-run, before/after diff), `audit_linux_patch_status` | Linux |
| `winpatch` | `apply_windows_patches` (WUA COM API, specific KB, reboot scheduling), `audit_windows_patch_status` | Windows |
| `isolation` | `isolate_host` (flush iptables/nftables, allow management CIDR only), `restore_network_access` | Linux + Windows |
| `forensics` | `collect_forensics` (auth.log, journal, auditd, netstat, ARP, proc state â†’ tar.gz â†’ S3) | Linux + Windows |
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
| `crossplatform` | TLS certificates, DNS resolver, software inventory, syslog forwarding | Both |
| `linuxupgrade` | In-place or containerize-and-migrate OS upgrades | Linux |

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
â”œâ”€â”€ VERSION                              # Single source of truth for agent version (0.1.0)
â”œâ”€â”€ agent/                               # Nexplane Agent (Go)
â”‚   â”œâ”€â”€ main.go                          # Entry point â€” updater check, config, fingerprint, register, poll
â”‚   â”œâ”€â”€ go.mod                           # Go 1.26+, AWS SDK v2, DB drivers (pq, mysql, mssqldb)
â”‚   â”œâ”€â”€ updater/                         # Self-update: CheckAndUpdate, FetchVersion, VerifySHA256
â”‚   â”œâ”€â”€ config/                          # Flag + env var config
â”‚   â”œâ”€â”€ fingerprint/                     # Stable machine ID
â”‚   â”œâ”€â”€ agenthmac/                       # HMAC-SHA256 sign/verify
â”‚   â”œâ”€â”€ client/                          # HTTP client â€” register, poll, post result
â”‚   â”œâ”€â”€ registration/                    # Registration logic
â”‚   â”œâ”€â”€ poller/                          # Ephemeral and service modes
â”‚   â”œâ”€â”€ executor/                        # Command dispatcher (30+ commands registered)
â”‚   â””â”€â”€ commands/
â”‚       â”œâ”€â”€ linuxpatch/                  # apt/yum/dnf security patches
â”‚       â”œâ”€â”€ winpatch/                    # Windows Update via WUA COM API
â”‚       â”œâ”€â”€ isolation/                   # Network isolation (iptables/nftables/WF)
â”‚       â”œâ”€â”€ forensics/                   # Evidence collection + S3 upload
â”‚       â”œâ”€â”€ compliance/                  # CIS audit + evidence collection
â”‚       â”œâ”€â”€ credrotation/                # DB, SSH key, API key, env file rotation
â”‚       â”œâ”€â”€ iac/                         # Terraform, Ansible, Helm CLI wrappers
â”‚       â”œâ”€â”€ fleet/                       # Service restart, config push, health check
â”‚       â”œâ”€â”€ backup/                      # restic backup + restore
â”‚       â”œâ”€â”€ reboot/                      # Graceful reboot + post-reboot verify
â”‚       â”œâ”€â”€ dbadmin/                     # PostgreSQL, MySQL, MSSQL user/permission management
â”‚       â”œâ”€â”€ ossecurity/                  # Linux MAC/kernel/integrity hardening
â”‚       â”œâ”€â”€ linuxauth/                   # PAM, SSH, CA certs, NTP, user audit
â”‚       â”œâ”€â”€ winharden/                   # Windows security hardening suite
â”‚       â”œâ”€â”€ crossplatform/               # TLS, DNS, software inventory
â”‚       â””â”€â”€ linuxupgrade/                # Linux in-place and containerize-and-migrate
â”‚
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ Dockerfile                       # Multi-stage: Go agent binaries â†’ Python backend
â”‚   â”‚                                    # Includes: Tailscale, Terraform 1.7.5, Ansible,
â”‚   â”‚                                    # community.aws collection, session-manager-plugin
â”‚   â”œâ”€â”€ seed.py                          # Demo data (org, users, assets, connectors, CRs, projects)
â”‚   â”œâ”€â”€ alembic/versions/                # 37+ migrations (001â†’037), 30+ tables
â”‚   â””â”€â”€ app/
â”‚       â”œâ”€â”€ main.py                      # App factory + router registration
â”‚       â”œâ”€â”€ models/
â”‚       â”‚   â”œâ”€â”€ change_request.py        # 180+ ChangeType values, fleet/IR status values
â”‚       â”‚   â”œâ”€â”€ connector.py             # ConnectorType including tailscale/terraform_local/ansible_local
â”‚       â”‚   â”œâ”€â”€ asset.py                 # AssetType including key_pair, cloud_account, storage_bucket
â”‚       â”‚   â””â”€â”€ ...                      # runbook, vulnerability, compliance, maintenance_window, etc.
â”‚       â”œâ”€â”€ routers/
â”‚       â”‚   â””â”€â”€ ...                      # change_requests, assets, connectors, projects, agent, etc.
â”‚       â”œâ”€â”€ services/
â”‚       â”‚   â””â”€â”€ ...                      # ai_service, ingest_service, safety/planning engine,
â”‚       â”‚                                # connector_service (commit _auto_asset after each step)
â”‚       â”œâ”€â”€ workflows/
â”‚       â”‚   â””â”€â”€ activities.py            # DB session commit after each step; _auto_asset persistence
â”‚       â””â”€â”€ connectors/
â”‚           â”œâ”€â”€ catalog/                 # Per-connector JSON catalogs (38+ connectors)
â”‚           â”œâ”€â”€ change_type_definitions/ # 48+ change type JSON definitions
â”‚           â””â”€â”€ executors/
â”‚               â”œâ”€â”€ aws/                 # EC2, IAM users, S3 buckets/lifecycle, Route53 zones/records,
â”‚               â”‚                        # RDS instances/snapshots, CloudWatch alarms, EBS snapshots,
â”‚               â”‚                        # security groups, key pairs, SSM, Tailscale, agent deploy
â”‚               â”œâ”€â”€ terraform_local/     # terraform_plan_local, terraform_apply_local, terraform_destroy_local
â”‚               â”œâ”€â”€ ansible_local/       # ansible_check_local, ansible_run_local (_runner.py with
â”‚               â”‚                        # SSM + localhost inventory modes)
â”‚               â”œâ”€â”€ nexplane_agent/      # Agent command dispatch + patch/compliance/fleet
â”‚               â””â”€â”€ ...                  # google_workspace, github, slack, entra_id, kubernetes, etc.
â”‚
├── tests/
│   └── smoke/
│       ├── smoke_helpers.py             # Shared client, constants, cloud SDK helpers
│       ├── test_aws_live.py             # AWS phases A-W (EC2, IAM, S3, Route53, RDS, CW, ALB, agent)
│       ├── test_gcp_live.py             # GCP phases L-R (GCE, firewall, storage, SA, IaC)
│       ├── test_azure_live.py           # Azure phases N-Z (VM, NSG, storage, identity, VNet, DNS, SQL, Monitor)
│       ├── test_agent_live.py           # Agent: 12 Linux command packages x AWS
│       └── test_multicloud_live.py      # Parallel cross-cloud VM lifecycle (AWS + GCP + Azure)
â”œâ”€â”€ scripts/
â”‚   â””â”€â”€ upload-agent-to-s3.sh           # Extract binaries from Docker image, upload to S3
â”‚
â”œâ”€â”€ frontend/
â”‚   â””â”€â”€ src/
â”‚       â”œâ”€â”€ pages/
â”‚       â”‚   â”œâ”€â”€ Settings.tsx             # AI providers + agent secret + S3 download links
â”‚       â”‚   â””â”€â”€ ...                      # all other pages
â”‚       â””â”€â”€ ...
â”‚
â”œâ”€â”€ docs/superpowers/
â”‚   â”œâ”€â”€ specs/                           # Design specs
â”‚   â””â”€â”€ plans/                          # Implementation plans
â”‚
â””â”€â”€ docker-compose.yml                   # Backend bound to 127.0.0.1:8000 (not public internet)
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
go build -ldflags="-X main.Version=0.1.0" -o dist/nexplane-agent ./

# Cross-compile
GOOS=linux GOARCH=amd64 go build -ldflags="-X main.Version=0.1.0" -o dist/nexplane-agent-linux-amd64 ./
```

### Running the Agent

Generate an agent secret in **Settings â†’ Agent Configuration** (admin only). The Deploy Agent panel pre-fills platform-specific install commands with your control plane URL, secret, and current version â€” resolved live from S3.

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
| `--poll-interval` | `NP_POLL_INTERVAL` | `30s` |

---

## Smoke Tests

Nexplane has a live smoke test suite that verifies end-to-end functionality against real cloud infrastructure. All smoke test files are in `backend/tests/smoke/` and share infrastructure via `smoke_helpers.py`.

### Test File Structure

| File | Coverage |
|------|----------|
| `smoke_helpers.py` | Shared infrastructure (NexplaneClient, cloud SDK helpers, constants, per-provider client factories) |
| `test_aws_live.py` | AWS phases Aâ€“W: EC2, IAM, S3, Route53, RDS, CloudWatch, ALB, Terraform, Ansible, agent, VNet capture, DR DNS |
| `test_gcp_live.py` | GCP phases Lâ€“R: GCE, firewall, storage, service accounts, Terraform, Ansible |
| `test_azure_live.py` | Azure phases Nâ€“Z: VM, NSG, blob storage, managed identity, RBAC, VNet, DNS, SQL Database, Monitor alerts, tagging, Terraform, Ansible |
| `test_agent_live.py` | All Linux agent command groups (12 packages) Ã— AWS; stub tracks for GCP/Azure/Windows |
| `test_multicloud_live.py` | Parallel cross-cloud VM lifecycle â€” AWS + GCP + Azure run concurrently with per-provider rollback stacks |

### Running Smoke Tests

All smoke test files are independently runnable from inside the backend container:

```bash
# AWS phases A-D (default, no slow RDS/EC2-stop phases)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --email admin@acme.example --password admin123 \
  --phases A,B,C,D \
  --tailscale-auth-key tskey-auth-<key>

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

# Azure phases N-O (VM launch + advanced operations)
docker exec nexplane-backend-1 python tests/smoke/test_azure_live.py \
  --email admin@acme.example --password admin123 \
  --phases N,O --azure-resource-group nexplane-smoke-rg

# Azure all phases N-Z (VM, NSG, storage, identity, VNet, DNS, SQL, Monitor)
docker exec nexplane-backend-1 python tests/smoke/test_azure_live.py \
  --email admin@acme.example --password admin123 \
  --phases N,O,P,Q,R,S,T,U,V,W,X --azure-resource-group nexplane-smoke-rg

# Azure SQL + Monitor (slow â€” ~10 min for SQL server provisioning)
docker exec nexplane-backend-1 python tests/smoke/test_azure_live.py \
  --email admin@acme.example --password admin123 \
  --phases Y,Z --azure-resource-group nexplane-smoke-rg

# Multi-cloud parallel (AWS + GCP + Azure simultaneously, ~15-25 min)
docker exec nexplane-backend-1 python tests/smoke/test_multicloud_live.py \
  --email admin@acme.example --password admin123 \
  --providers aws,gcp,azure \
  --gcp-project <project-id> \
  --azure-resource-group nexplane-smoke-rg

# Agent â€” all Linux command groups on AWS
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --email admin@acme.example --password admin123 \
  --cloud aws --os linux \
  --tailscale-auth-key tskey-auth-<key>

# Agent â€” specific phase on AWS only
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

`test_multicloud_live.py` runs the full VM lifecycle (launch â†’ stop â†’ start â†’ snapshot â†’ delete) across AWS, GCP, and Azure simultaneously using `ThreadPoolExecutor`. Each cloud worker has its own `NexplaneClient`, rollback stack, and failure handling â€” a failure in one cloud does not prevent cleanup in others. A consolidated pass/fail report is printed at the end.

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

End-to-end integration test that creates and destroys real AWS resources against a live account. Verifies the full chain from Nexplane CR â†’ AWS API â†’ asset inventory. All phases use the **rollback stack pattern** â€” each CR is pushed to a LIFO stack; the `finally` block triggers Nexplane's own rollback system in reverse order, followed by boto3 safety-net cleanup.

```bash
# Quick run (no RDS â€” ~15â€“25 minutes depending on phases)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,K \
  --tailscale-auth-key tskey-auth-<your-key>

# Full run including RDS (~35 min additional for Phase J)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --phases A,B,C,D,E,F,G,H,I,J,K \
  --tailscale-auth-key tskey-auth-<your-key> \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123
```

**Prerequisites:**
- AWS connector configured with credentials covering EC2, SSM, IAM, S3, Route53, RDS, CloudWatch
- `NexplaneEC2TestProfile` IAM instance profile with `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`
- Tailscale connector configured with a reusable pre-authorized auth key
- Agent secret generated in Settings

**Phases:**

| Phase | What it tests | AWS resources created | Requires |
|-------|--------------|----------------------|---------|
| **A** | Key pair create â†’ EC2 launch â†’ SSM â†’ Tailscale join â†’ Agent deploy from S3 | EC2 instance, key pair | â€” |
| **B** | SSM patch audit, system info, CloudWatch agent install | â€” | Phase A |
| **C** | Terraform local: S3 bucket create + destroy | S3 bucket | â€” |
| **D** | Ansible local playbook CR + htop install/remove via SSM | â€” | Phase A |
| **E** | EC2 stop/start/reboot + EBS snapshot; rollback stack cleanup | EBS snapshot (deleted) | Phase A |
| **F** | Security group rule add/remove via CR; boto3 verification | Security group (deleted) | â€” |
| **G** | IAM user create â†’ attach policy â†’ rotate key â†’ disable/enable â†’ detach â†’ delete | IAM user (deleted) | â€” |
| **H** | S3 bucket create â†’ lifecycle â†’ bucket policy â†’ public access block â†’ delete | S3 bucket (deleted) | â€” |
| **I** | Route53 private zone + A record create/update/delete; boto3 verification | Hosted zone (deleted) | â€” |
| **J** | RDS db.t3.micro create â†’ snapshot â†’ verify â†’ delete (~25â€“35 min) | RDS instance + snapshot (deleted) | â€” |
| **K** | CloudWatch alarms create â†’ trigger via SSM custom metric â†’ verify ALARM state â†’ rollback | CloudWatch alarms (deleted) | Phase A |

All resources are created under `nexplane-smoke-*` / `nexplane-smoke-test-*` naming prefixes and cleaned up even on failure. The `cleanup()` function in Phase A handles EC2/key-pair teardown; each phase's `finally` block handles its own resources via the rollback stack + boto3 safety net.

**Rollback stack pattern:**
Each phase maintains a `rollback_stack: list[tuple[str, str]]` of `(cr_id, label)`. On success, resources are deleted by triggering rollback of the creation CRs (exercising Nexplane's own rollback system). On failure, the `finally` block iterates `reversed(rollback_stack)` calling `client.rollback_cr()`, followed by direct boto3 cleanup as a safety net.

**Run individual phases:**
```bash
# Phase A only (quickest â€” ~8 minutes for EC2 + agent)
--phases A

# Phase C only (Terraform, no EC2 needed â€” ~2 minutes)
--phases C

# Phases F, G, H standalone (no EC2 needed)
--phases F
--phases G
--phases H

# Phase J standalone (RDS â€” ~30 minutes, costs ~$0.02)
--phases J
```

---

## Settings

| Setting | Who | Notes |
|---------|-----|-------|
| AI Providers (Anthropic, OpenAI) | Admin | API keys per provider; select default. Encrypted at rest. |
| Agent secret | Admin | Shared HMAC secret. Shown once â€” store securely. Deploy panel pre-fills commands with S3 download URL. |
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
# Backend unit tests (200+ tests)
cd backend
pytest

# Agent tests (22 packages)
cd agent
go test ./...

# Live AWS smoke test (requires AWS credentials + real account)
# Quick (no RDS):
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,K \
  --tailscale-auth-key tskey-auth-<key>
# Full (includes RDS ~30 min): --phases A,B,C,D,E,F,G,H,I,J,K
```

> **Note:** When running via Docker Compose on Windows, Vite's file watcher may not pick up changes. Run:
> `docker compose stop frontend && docker compose up frontend -d`

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://nexplane:nexplane_dev@db:5432/nexplane` | PostgreSQL connection |
| `SECRET_KEY` | (dev key) | JWT + Fernet key derivation â€” **change in production** |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed CORS origins |
| `ENVIRONMENT` | `development` | Environment name |
| `AI_MODEL` | `claude-sonnet-4-6` | Anthropic model for AI planning |
| `WEBHOOK_SECRET` | (dev key) | HMAC key for vulnerability scanner webhook verification |
| `NEXPLANE_AGENT_DOWNLOAD_URL` | `https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com` | S3 base URL for agent binary downloads; override for self-hosted distributions |

---

## Security Design

### Network Exposure

The backend API binds to `127.0.0.1:8000` â€” it is not exposed to the public internet. The frontend (port 3000) is the only externally accessible service in the default Docker Compose configuration. Agent binary distribution is handled entirely by S3, not by the backend.

### Safety Engine

| Scenario | Behavior |
|----------|----------|
| Prod + critical asset | `high` or `critical` risk score |
| Missing rollback strategy (prod/critical) | Blocked â€” cannot generate plan |
| Remote command without approved template | Blocked at safety review |
| Freeform shell command | Blocked unconditionally |
| Critical risk change | Requires 2 approvals (approver + admin) |
| Microsegmentation policy | Staged simulation mode only |
| Agent job with invalid HMAC | Rejected before execution |
| AI not configured | 402 response; UI shows inline guidance |
| Active change freeze | 423 Locked on approve/execute; bypass requires justification + `ir_responder` role |
| Step credential output | Never written to logs or DB â€” travels in memory only between steps |
| IaC apply | Gated behind explicit plan review and approval |
| DB replica promotion | `rollback_supported: false`; blast radius warning required in approval |

---

## API Documentation

Interactive OpenAPI docs: `http://localhost:8000/docs`

Key endpoint groups:
- `/auth/*` â€” login, logout, current user
- `/assets/*` â€” inventory CRUD, tag management, bulk tagging, ingest
- `/connectors/*` â€” connector CRUD, test, credentials, schedule, ingest
- `/projects/*` â€” project CRUD, member management, AI chat
- `/change-requests/*` â€” full CR lifecycle (plan â†’ approve â†’ execute â†’ verify â†’ rollback) + batch progress
- `/runbooks/*` â€” CRUD, fork, trigger; `/executions/*` â€” status, resume checkpoint, abort
- `/vulnerability/*` â€” findings CRUD, policies, SLA dashboard, CVE blast-radius, patch campaign; `POST /webhooks/vulnerability-findings`
- `/ir/*` â€” IR playbook templates, execute, forensic bundles
- `/access-reviews/*` â€” collect, decisions, approve, auto-generate removal CRs
- `/compliance/*` â€” baselines CRUD, drift alerts, evidence ZIP download, freeze windows
- `/maintenance-windows/*` â€” CRUD, status check
- `/agent/*` â€” agent registration, job dispatch, result reporting
- `/settings/*` â€” AI providers, agent secret, remediation policies
- `/downloads/*` â€” versioned agent binaries + SHA256 checksums + version file (served from backend for development; production uses S3)
- `/audit-events/*` â€” immutable audit trail
