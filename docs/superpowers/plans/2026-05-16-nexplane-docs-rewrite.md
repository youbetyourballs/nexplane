# nexplane-docs Full Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite all pages in the nexplane-docs MkDocs site so they accurately reflect the real Nexplane product.

**Architecture:** Clone `youbetyourballs/nexplane-docs`, rewrite `mkdocs.yml` nav, write ~57 markdown pages derived from `README.md` in the main nexplane repo, push to main (Netlify auto-deploys).

**Tech Stack:** MkDocs Material theme, Markdown, GitHub, Netlify

**Source of truth:** `README.md` in the nexplane repo — all capabilities, counts, change types, connector tables, and agent command packages come from there.

---

## File Map

| Section | Files | Action |
|---------|-------|--------|
| `mkdocs.yml` | 1 | Rewrite nav |
| `docs/index.md` | 1 | Rewrite |
| `docs/getting-started/` | 4 | Rewrite all |
| `docs/concepts/` | 5 | New section |
| `docs/features/` | 9 | New section |
| `docs/change-types/` | 10 | Rewrite all |
| `docs/connectors/` | 35 real + 7 stub | Rewrite + new |
| `docs/agent/` | 5 | Rewrite all |
| `docs/security/` | 4 | Rewrite + new |
| `docs/api/` | 1 | Rewrite |

---

## Task 1: Clone repo

- [ ] Clone the docs repo locally

```powershell
cd C:\Users\john\
git clone https://github.com/youbetyourballs/nexplane-docs.git
cd nexplane-docs
```

- [ ] Verify structure

```powershell
ls docs/
```

Expected: `index.md`, `agent/`, `api/`, `architecture/`, `change-types/`, `connectors/`, `getting-started/`, `runbooks/`, `security/`

---

## Task 2: Rewrite mkdocs.yml

- [ ] Replace `mkdocs.yml` with the corrected nav

```yaml
site_name: Nexplane Documentation
site_url: https://docs.nexplane.ai
site_description: The security control plane for your infrastructure
repo_url: https://github.com/youbetyourballs/nexplane
repo_name: nexplane
edit_uri: ""

theme:
  name: material
  palette:
    scheme: slate
    primary: indigo
    accent: cyan
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - navigation.top
    - search.highlight
    - search.suggest
    - content.code.copy
  font:
    text: Inter
    code: JetBrains Mono

nav:
  - Home: index.md
  - Getting Started:
    - Installation: getting-started/installation.md
    - Connect a Cloud Account: getting-started/connect-cloud.md
    - Your First Change Request: getting-started/first-change-request.md
    - Deploy the Agent: getting-started/deploy-agent.md
  - Core Concepts:
    - Change Requests: concepts/change-requests.md
    - Projects: concepts/projects.md
    - Asset Inventory: concepts/asset-inventory.md
    - Connectors: concepts/connectors.md
    - The Nexplane Agent: concepts/agent.md
  - Features:
    - Composable Runbooks: features/runbooks.md
    - Incident Response: features/incident-response.md
    - Vulnerability Remediation: features/vulnerability-remediation.md
    - Fleet Operations: features/fleet-operations.md
    - Identity Lifecycle: features/identity-lifecycle.md
    - Compliance & Governance: features/compliance.md
    - IaC Orchestration: features/iac-orchestration.md
    - Secret & Credential Rotation: features/credential-rotation.md
    - IP Migration: features/ip-migration.md
  - Change Types:
    - Overview: change-types/index.md
    - Compute: change-types/compute.md
    - Identity & IAM: change-types/identity.md
    - Credentials: change-types/credentials.md
    - Hardening: change-types/hardening.md
    - IaC: change-types/iac.md
    - Database: change-types/database.md
    - Backup & Recovery: change-types/backup.md
    - Compliance: change-types/compliance.md
    - Incident Response: change-types/incident-response.md
    - Telemetry: change-types/telemetry.md
  - Connectors:
    - Overview: connectors/index.md
    - AWS: connectors/aws.md
    - Azure: connectors/azure.md
    - GCP: connectors/gcp.md
    - Cloudflare: connectors/cloudflare.md
    - Palo Alto: connectors/paloalto.md
    - Tailscale: connectors/tailscale.md
    - Okta: connectors/okta.md
    - Active Directory: connectors/active-directory.md
    - Microsoft Entra ID: connectors/entra-id.md
    - HashiCorp Vault: connectors/vault.md
    - GitHub: connectors/github.md
    - CrowdStrike: connectors/crowdstrike.md
    - Tenable: connectors/tenable.md
    - SentinelOne: connectors/sentinelone.md
    - Snyk: connectors/snyk.md
    - Qualys: connectors/qualys.md
    - Wiz: connectors/wiz.md
    - RunZero: connectors/runzero.md
    - Google Workspace: connectors/google-workspace.md
    - Slack: connectors/slack.md
    - Kubernetes: connectors/kubernetes.md
    - Helm: connectors/helm.md
    - Jira: connectors/jira.md
    - PagerDuty: connectors/pagerduty.md
    - ServiceNow: connectors/servicenow.md
    - Splunk: connectors/splunk.md
    - Datadog: connectors/datadog.md
    - SSH: connectors/ssh.md
    - Terraform (Local): connectors/terraform-local.md
    - Terraform (Remote): connectors/terraform-remote.md
    - Ansible (Local): connectors/ansible-local.md
    - Ansible (Remote): connectors/ansible-remote.md
    - Nexplane Agent Connector: connectors/nexplane-agent.md
    - OCI: connectors/oci.md
    - LDAP: connectors/ldap.md
    - Keycloak: connectors/keycloak.md
    - WinRM: connectors/winrm.md
    - PostgreSQL: connectors/postgres.md
    - Redis: connectors/redis.md
    - MongoDB: connectors/mongodb.md
  - Agent:
    - Overview: agent/index.md
    - Installation & Configuration: agent/installation.md
    - Command Packages: agent/commands.md
    - Self-Update: agent/self-update.md
    - Windows: agent/windows.md
  - Security:
    - Security Model: security/model.md
    - Credential Storage: security/credentials.md
    - Safety Engine: security/safety-engine.md
    - Network Exposure: security/network-exposure.md
  - API Reference: api/index.md

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.inlinehilite
  - pymdownx.snippets
  - pymdownx.tabbed:
      alternate_style: true
  - attr_list
  - tables
  - toc:
      permalink: true

plugins:
  - search

extra:
  social:
    - icon: fontawesome/brands/github
      link: https://github.com/youbetyourballs/nexplane
    - icon: fontawesome/solid/envelope
      link: mailto:hello@nexplane.ai
```

- [ ] Commit

```bash
git add mkdocs.yml
git commit -m "docs: rewrite nav to match real product"
```

---

## Task 3: index.md

- [ ] Write `docs/index.md`

Content: tagline ("The control plane for security execution"), what it does (accurate feature list matching README), architecture overview (3-tier: frontend/backend/agent — no Redis), stack table, link to Getting Started. Correct connector count (38+). Agent described as outbound long-poll, not mutual TLS.

- [ ] Commit

```bash
git add docs/index.md
git commit -m "docs: rewrite home page"
```

---

## Task 4: Getting Started (4 pages)

- [ ] Write `docs/getting-started/installation.md`

Content: prerequisites (Docker 24+, Docker Compose v2.20+, 2GB RAM, ports 3000/8000), `git clone` + `docker compose up --build`, 3 containers (backend, frontend, db — no Redis), demo credentials table (admin/operator/approver/auditor), `SECRET_KEY` production warning, links to next steps.

- [ ] Write `docs/getting-started/connect-cloud.md`

Content: navigate to Connectors → Add Connector, select type, enter credentials, click Test Connection, explain ingest (assets appear in inventory after first poll). Per-connector credential fields for AWS (access key + secret + region), brief mention others follow same pattern.

- [ ] Write `docs/getting-started/first-change-request.md`

Content: navigate to Change Requests → New, select change type (example: `security_group_update`), fill parameters, submit for safety review, approve, execute, view result + audit log. Explain lifecycle states: Draft → Planned → Awaiting Approval → Approved → Executing → Verifying → Completed.

- [ ] Write `docs/getting-started/deploy-agent.md`

Content: Settings → Agent Configuration → generate secret (shown once, store securely), copy one-liner from Deploy Agent panel, run on target host, verify asset appears in inventory. Linux one-liner, systemd unit, Windows service command. Flag/env reference table.

- [ ] Commit

```bash
git add docs/getting-started/
git commit -m "docs: rewrite getting-started section"
```

---

## Task 5: Core Concepts (5 pages — new directory)

- [ ] Create `docs/concepts/` directory

- [ ] Write `docs/concepts/change-requests.md`

Content: what a CR is, full lifecycle states, safety engine (risk scoring, prod/critical blocking), approval tiers (low=auto, medium=1 approver, high=2 approvers, critical=2+delay), rollback (every change type knows its inverse), audit trail immutability.

- [ ] Write `docs/concepts/projects.md`

Content: group related CRs into sequenced plan, dependency graph (CR B blocked until CR A completes), visual DAG (React Flow + Dagre), AI Planning Assistant (describe goal → get structured plan referencing real assets, supports Anthropic + OpenAI), project states.

- [ ] Write `docs/concepts/asset-inventory.md`

Content: what assets are (servers, cloud accounts, firewalls, identities, applications, key pairs, storage buckets, etc.), how ingest works (connector polls on schedule), tagging (manual + bulk), asset detail pages, search.

- [ ] Write `docs/concepts/connectors.md`

Content: what connectors are, two functions (ingest = discovery, change actions = execution), credential storage (encrypted via SecretsService), connector types overview table (cloud, identity, security, SaaS, IaC, workflow), test connection, 38+ connectors.

- [ ] Write `docs/concepts/agent.md`

Content: why the agent exists (cloud APIs can't reach inside a host), outbound long-poll model (agent calls `/agent/jobs/next` — no inbound ports needed), HMAC-SHA256 job signing, registration (agent appears as Asset automatically), ephemeral vs service mode, 15 command packages overview table.

- [ ] Commit

```bash
git add docs/concepts/
git commit -m "docs: add core concepts section"
```

---

## Task 6: Features — Part 1 (Runbooks, IR, Vuln Remediation)

- [ ] Write `docs/features/runbooks.md`

Content: composable multi-step workflows, step types (change/condition/human_checkpoint/parallel_group), failure handling per step (abort/continue/rollback_all), versioning (each edit = new version, active executions keep snapshot), seed templates (Engineer Onboarding, Account Compromise IR, Patch Campaign), API endpoints.

- [ ] Write `docs/features/incident-response.md`

Content: 4 playbook types: Host Isolation (iptables/nftables/Windows Firewall, allow only management CIDR + control plane, pre-isolation state for rollback), Account Lockdown (AD + Okta + Entra ID + Google Workspace + GitHub + Slack simultaneously), Evidence Preservation (auth logs/journal/auditd/netstat/ARP/process state → tar.gz → S3, runs before remediation), Phishing Response (block domain, force password reset, revoke sessions, force MFA re-enrollment). Expedited approval path. Forensic bundles downloadable via IR tab.

- [ ] Write `docs/features/vulnerability-remediation.md`

Content: webhook ingest (`POST /vulnerability/webhooks/vulnerability-findings`, HMAC-verified) + scheduled scanner poll, asset matching (by IP/hostname), auto-generate draft CRs (RemediationPolicy rules: finding type → change type → approval level), CVE blast-radius UI (enter CVE → see all affected assets → generate patch campaign), SLA tiers (configurable per severity: critical/high/medium), auto-escalation (background job marks breaches), SLA tab UI, policy editor (per-severity: auto-generate, auto-approve, SLA days).

- [ ] Commit

```bash
git add docs/features/runbooks.md docs/features/incident-response.md docs/features/vulnerability-remediation.md
git commit -m "docs: add runbooks, IR, and vuln remediation feature pages"
```

---

## Task 7: Features — Part 2 (Fleet, Identity, Compliance, IaC)

- [ ] Write `docs/features/fleet-operations.md`

Content: Rolling Restart (N hosts at a time, abort threshold, health check between batches), Canary Config Push (deploy to 1 host → verify command → roll to rest if pass, rollback restores original files), Bulk File Distribution (CA certs, authorized_keys, scripts, config files), Fleet Health Check (disk/load/service status/pending-reboot before major ops), Maintenance Windows (cron-schedule, approved CRs queue and auto-execute when window opens).

- [ ] Write `docs/features/identity-lifecycle.md`

Content: User Offboarding Kill Switch (one CR atomically disables across AD + Okta + Entra ID + Google Workspace + GitHub + Slack + optionally isolates CrowdStrike-managed endpoints in 5 ordered phases), User Onboarding (provision across all connected identity systems), Access Reviews (periodic campaigns: collect group memberships across all identity connectors, present for manager review, auto-generate removal CRs for revoked access).

- [ ] Write `docs/features/compliance.md`

Content: CIS Benchmark Campaigns (audit all CIS controls: filesystem/sysctl/SSH/PAM/auditd/SELinux/AppArmor, then apply remediation, before/after compliance score), Drift Detection (weekly scheduled scan vs baseline, creates draft remediation CRs), Change Freeze Enforcement (declare window, approve/execute endpoints return 423 Locked, emergency bypass with mandatory justification + `ir_responder` role + audit log), Audit Evidence Collection (request evidence for SOC2/PCI/ISO27001 control, agent collects config files + command outputs, backend packages as downloadable ZIP).

- [ ] Write `docs/features/iac-orchestration.md`

Content: Terraform local (runs `terraform init/plan/apply` in backend container, AWS credentials injected, creates S3 buckets and other AWS resources as tracked CRs, rollback via `terraform destroy`), Ansible local (`--check` mode preflight, full run via SSM transport, `community.aws.aws_ssm` connection plugin, `inventory_content` override for custom targets), Terraform remote (two-phase: plan output stored as blast radius → approval gate → apply, rollback via state restore), Helm (`helm upgrade --atomic`, automatic rollback on health check failure, `helm rollback` on demand). Plan diff renders in CR detail view with green/red coloring.

- [ ] Commit

```bash
git add docs/features/fleet-operations.md docs/features/identity-lifecycle.md docs/features/compliance.md docs/features/iac-orchestration.md
git commit -m "docs: add fleet ops, identity lifecycle, compliance, and IaC feature pages"
```

---

## Task 8: Features — Part 3 (Credential Rotation, IP Migration)

- [ ] Write `docs/features/credential-rotation.md`

Content: DB credential rotation (generate new password → update DB user → update app config files → restart service → verify connection, rollback supported), SSH key fleet rotation (remove old key by fingerprint from authorized_keys across fleet, add new public key, backup for rollback), API key rotation (rotate AWS IAM key/Okta API token/GitHub PAT, propagate to Kubernetes secrets/agent env files/SSM Parameter Store), Service account rotation (update password in AD/Okta, push to dependent services via asset metadata). Key principle: step output credentials travel in memory between steps only — never written to logs or DB.

- [ ] Write `docs/features/ip-migration.md`

Content: 4 methods table (tailscale/secondary_swap/commit_timer/manual with when-to-use and connectivity guarantee), `auto` selects lowest-risk available. Dead man's switch (pending_rollback.json written to disk, background goroutine probes control plane, commits if probe succeeds within timer window, auto-rolls back on crash or unreachability). Timer window configurable per CR (commit_timer_seconds, default 30s, range 10-300s). Multi-host campaigns (ip_campaign, batch_size, abort_error_threshold). DNS coordination (migrate_ip: short TTL ≤120s = single CR, long TTL >120s = two-CR sequence). IP Migration Wizard UI (4 steps: Configure → Pre-flight → Execute → Verify). Rollback restores IP/gateway/routes/DNS/MTU from snapshot.

- [ ] Commit

```bash
git add docs/features/credential-rotation.md docs/features/ip-migration.md
git commit -m "docs: add credential rotation and IP migration feature pages"
```

---

## Task 9: Change Types (10 pages)

- [ ] Write `docs/change-types/index.md`

Full table of all 48+ change types organized by category (Infrastructure, EC2, SSM, Network, IP Migration, Agent, Identity, IAM, S3, DNS/Route53, RDS, Observability, Incident Response, IaC local, IaC remote, Database, Backup/Recovery, Compliance, Telemetry). Match the table in README exactly.

- [ ] Write `docs/change-types/compute.md`

Change types: `ec2_launch`, `ec2_start`, `ec2_stop`, `ec2_reboot`, `ec2_terminate`, `snapshot_asset`, `ssm_command`, `deploy_nexplane_agent`. Per type: what it does, key parameters, rollback behavior. GCP/Azure VM lifecycle equivalents.

- [ ] Write `docs/change-types/identity.md`

Change types: `offboard_user`, `onboard_user`, `iam_user_create`, `iam_user_delete`. Plus SaaS identity actions: Google Workspace (remove_from_groups/reset_2fa/revoke_oauth_tokens/wipe_mobile_device/suspend_user/unsuspend_user), GitHub (remove_org_member/revoke_user_pats/enforce_branch_protection/archive_repo/disable_actions/enable_actions), Slack (deactivate_user/reactivate_user), Entra ID (remove_from_teams/assign_license/remove_license/revoke_sessions/disable_user), Kubernetes (restart_deployment/scale_deployment/apply_network_policy/update_rbac/rotate_secret).

- [ ] Write `docs/change-types/credentials.md`

Change types: `key_rotation`, `rotate_db_credentials`, `rotate_ssh_keys`, `rotate_api_key`, `rotate_service_account`, `key_pair_create`, `key_pair_delete`. Per type: what rotates, propagation targets, rollback.

- [ ] Write `docs/change-types/hardening.md`

Change types: `security_group_update`, `microsegmentation_policy` (staged simulation only), `isolate_host`, `restore_network_access`. Agent hardening commands via `patch_packages` and the ossecurity/linuxauth/winharden packages.

- [ ] Write `docs/change-types/iac.md`

Change types: `terraform_local_apply`, `ansible_local_playbook`, `terraform_apply`, `ansible_playbook`, `helm_upgrade`. Parameters, plan-first requirement, rollback behavior.

- [ ] Write `docs/change-types/database.md`

Change types: `provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `promote_db_replica`, `db_connection_config`, `rds_instance_create`, `rds_instance_delete`, `rds_snapshot_create`. PostgreSQL/MySQL/MSSQL/RDS scope.

- [ ] Write `docs/change-types/backup.md`

Change types: `create_backup`, `verify_backup`, `restore_files`, `dr_failover`, `scheduled_reboot`. restic for host backups, EBS/RDS snapshots via AWS connector, RTO timestamps on DR failover.

- [ ] Write `docs/change-types/compliance.md`

Change types: `enforce_cis_benchmark`, `collect_evidence`. CIS control scope, before/after score, evidence ZIP output.

- [ ] Write `docs/change-types/incident-response.md`

Change types: `lockdown_account`, `phishing_response`, `preserve_evidence`, `isolate_host`. Expedited approval path. Forensic bundle storage.

- [ ] Write `docs/change-types/telemetry.md`

Change types: `telemetry_agent_deploy`, `remote_command`, `cloudwatch_alarm_create`, `cloudwatch_alarm_delete`, `patch_campaign`, `rolling_restart`, `canary_config_push`, `distribute_file`, `fleet_health_check`.

- [ ] Commit

```bash
git add docs/change-types/
git commit -m "docs: rewrite all change-types pages with full change type catalog"
```

---

## Task 10: Connectors — Big 3 (AWS, Azure, GCP)

- [ ] Write `docs/connectors/index.md`

Overview: what connectors are, ingest vs change actions, credential storage, connector count (38+), table of all connectors by category.

- [ ] Write `docs/connectors/aws.md`

Full capabilities from README: EC2 lifecycle (launch/stop/start/reboot/terminate), key pairs (create/delete), IAM users + policies (create/delete/attach/detach/rotate), S3 buckets (create/delete/lifecycle/policy/public-access), Route53 (zones + records create/upsert/delete/DR-failover), RDS (create/delete/snapshot/replica), CloudWatch alarms (create/delete), ALB + target groups + listeners, security groups, EBS snapshots, SSM commands, Tailscale join/remove, agent deploy. Required IAM permissions (managed policies list from README). Cross-account (Role ARN + External ID). Credential fields.

- [ ] Write `docs/connectors/azure.md`

Full capabilities: VM lifecycle (create/stop/start/reboot/snapshot/delete), NSG rules (update/restore), blob storage accounts + containers (create/delete), managed identities (create/delete), RBAC role assignments (create/delete), VNet + subnets (create/delete), DNS zones + A records (create/delete), SQL Server + Database (create/delete), Monitor metric alerts (create/delete), resource tagging, Entra users (disable/enable/revoke sessions/assign license), Terraform local, Ansible local. Required roles: Contributor + User Access Administrator at subscription scope. Credential fields (service principal: client_id, client_secret, tenant_id, subscription_id).

- [ ] Write `docs/connectors/gcp.md`

Full capabilities: Compute lifecycle (create/stop/start/reboot/snapshot/delete), firewall rules (create/delete), storage buckets (block public access), service accounts (disable/rotate key), IAM bindings, SCC findings, Terraform local, Ansible local. Required IAM roles (roles/compute.admin + roles/iam.serviceAccountAdmin + roles/iam.serviceAccountKeyAdmin + roles/storage.admin + roles/dns.admin + roles/iam.securityAdmin). Credential fields.

- [ ] Commit

```bash
git add docs/connectors/index.md docs/connectors/aws.md docs/connectors/azure.md docs/connectors/gcp.md
git commit -m "docs: add connector overview and Big 3 cloud connector pages"
```

---

## Task 11: Connectors — Identity & Access

- [ ] Write `docs/connectors/okta.md`

Ingest: users, groups, applications. Change actions: user disable/enable, group add/remove, MFA enforcement, session revoke, API token rotation. Credential fields: org_url, api_token.

- [ ] Write `docs/connectors/active-directory.md`

Capabilities: discover/disable stale accounts, move OU, rotate service account passwords, user disable/enable. Credential fields: server, domain, username, password, base_dn.

- [ ] Write `docs/connectors/entra-id.md`

Capabilities: user disable/enable, revoke sessions, assign/remove license, remove from Teams, group management. Used in offboarding kill switch. Credential fields: tenant_id, client_id, client_secret.

- [ ] Write `docs/connectors/vault.md`

Capabilities: secret discovery, dynamic credential generation, secret rotation. Credential fields: vault_url, token (or AppRole).

- [ ] Write `docs/connectors/github.md`

Ingest: repos, org members. Change actions: remove_org_member, revoke_user_pats, enforce_branch_protection, archive_repo, disable_actions, enable_actions. Credential fields: token, org.

- [ ] Commit

```bash
git add docs/connectors/okta.md docs/connectors/active-directory.md docs/connectors/entra-id.md docs/connectors/vault.md docs/connectors/github.md
git commit -m "docs: add identity and access connector pages"
```

---

## Task 12: Connectors — Security & Network Tools

- [ ] Write `docs/connectors/crowdstrike.md`

Capabilities: isolate host, RTR commands, prevention policy, host containment. Ingest: hosts, alerts. Credential fields: client_id, client_secret, base_url.

- [ ] Write `docs/connectors/tenable.md`

Capabilities: launch/pause/resume scans, export findings, trigger remediation verification. Ingest: vulnerability findings. Credential fields: access_key, secret_key.

- [ ] Write `docs/connectors/sentinelone.md`

Capabilities: endpoint discovery, alert ingest. Credential fields: api_token, site_url.

- [ ] Write `docs/connectors/snyk.md`

Capabilities: vulnerability findings ingest. Credential fields: api_token, org_id.

- [ ] Write `docs/connectors/qualys.md`

Capabilities: vulnerability findings ingest, scan launch. Credential fields: username, password, api_url.

- [ ] Write `docs/connectors/wiz.md`

Capabilities: cloud security findings ingest, misconfiguration discovery. Credential fields: client_id, client_secret, endpoint.

- [ ] Write `docs/connectors/runzero.md`

Capabilities: network asset discovery. Credential fields: api_key, org_id.

- [ ] Write `docs/connectors/cloudflare.md`

Capabilities: WAF rules, firewall rules, access policies, block IP, SSL mode, DNS management. Credential fields: api_token, zone_id, account_id.

- [ ] Write `docs/connectors/paloalto.md`

Capabilities: address objects/rules/zones, create/delete rules, block IP, commit. Credential fields: hostname, username, password.

- [ ] Commit

```bash
git add docs/connectors/crowdstrike.md docs/connectors/tenable.md docs/connectors/sentinelone.md docs/connectors/snyk.md docs/connectors/qualys.md docs/connectors/wiz.md docs/connectors/runzero.md docs/connectors/cloudflare.md docs/connectors/paloalto.md
git commit -m "docs: add security and network tool connector pages"
```

---

## Task 13: Connectors — SaaS & Workflow

- [ ] Write `docs/connectors/google-workspace.md`

Change actions: remove_from_groups, reset_2fa, revoke_oauth_tokens, wipe_mobile_device, suspend_user, unsuspend_user. Ingest: users, groups. Credential fields: service_account_json, admin_email.

- [ ] Write `docs/connectors/slack.md`

Change actions: deactivate_user, reactivate_user. Ingest: users, channels. Credential fields: bot_token.

- [ ] Write `docs/connectors/kubernetes.md`

Change actions: restart_deployment, scale_deployment, apply_network_policy, update_rbac, rotate_secret, helm_upgrade, helm_rollback. Ingest: pods, deployments, namespaces. Credential fields: kubeconfig or in-cluster.

- [ ] Write `docs/connectors/helm.md`

Change actions: helm_upgrade (--atomic, auto-rollback on health check failure), helm_rollback. Credential fields: kubeconfig, release_name, chart, namespace.

- [ ] Write `docs/connectors/jira.md`

Capabilities: ticket creation for change requests, status sync. Credential fields: url, email, api_token, project_key.

- [ ] Write `docs/connectors/pagerduty.md`

Capabilities: incident creation, alert routing. Credential fields: api_key, service_id.

- [ ] Write `docs/connectors/servicenow.md`

Capabilities: change request sync. Credential fields: instance_url, username, password.

- [ ] Write `docs/connectors/splunk.md`

Capabilities: event ingest, search query execution. Credential fields: host, token.

- [ ] Write `docs/connectors/datadog.md`

Capabilities: monitor ingest, alert creation. Credential fields: api_key, app_key.

- [ ] Commit

```bash
git add docs/connectors/google-workspace.md docs/connectors/slack.md docs/connectors/kubernetes.md docs/connectors/helm.md docs/connectors/jira.md docs/connectors/pagerduty.md docs/connectors/servicenow.md docs/connectors/splunk.md docs/connectors/datadog.md
git commit -m "docs: add SaaS and workflow connector pages"
```

---

## Task 14: Connectors — Infrastructure & IaC

- [ ] Write `docs/connectors/tailscale.md`

Change actions: tailscale_join (install on EC2 via SSM, join tailnet, returns Tailscale IP), tailscale_remove (gracefully remove node). Auth key management (reusable pre-authorized key required — not single-use). Backend container joins tailnet during smoke tests. Credential fields: auth_key, tailnet.

- [ ] Write `docs/connectors/ssh.md`

Capabilities: execute approved command templates, check service status, tail logs. No freeform commands — template-based only. Credential fields: hostname, username, private_key, port.

- [ ] Write `docs/connectors/terraform-local.md`

Capabilities: terraform_plan_local, terraform_apply_local, terraform_destroy_local. Runs in backend container. AWS credentials injected. Plan diff shown in CR detail. Rollback via terraform destroy. Credential fields: none (uses AWS connector credentials).

- [ ] Write `docs/connectors/terraform-remote.md`

Capabilities: two-phase plan→apply. Plan output stored as blast radius. Approval gate between plan and apply. Rollback via state restore. Credential fields: workspace_url, token.

- [ ] Write `docs/connectors/ansible-local.md`

Capabilities: ansible_check_local (--check mode preflight), ansible_run_local. SSM transport via community.aws.aws_ssm + session-manager-plugin. inventory_content override for custom targets. Credential fields: none (uses AWS connector credentials).

- [ ] Write `docs/connectors/ansible-remote.md`

Capabilities: remote playbook execution. Credential fields: inventory, private_key, become_password.

- [ ] Write `docs/connectors/nexplane-agent.md`

This is the connector interface for sending commands to registered Nexplane Agents. Change actions dispatch to agent command packages. List all 15 packages and their commands. Credential fields: none (uses agent secret from Settings).

- [ ] Commit

```bash
git add docs/connectors/tailscale.md docs/connectors/ssh.md docs/connectors/terraform-local.md docs/connectors/terraform-remote.md docs/connectors/ansible-local.md docs/connectors/ansible-remote.md docs/connectors/nexplane-agent.md
git commit -m "docs: add infrastructure and IaC connector pages"
```

---

## Task 15: Connectors — Placeholder Pages

Each page follows this template:

```markdown
# [Connector Name]

!!! note "Coming soon"
    The [Connector Name] connector is planned but not yet available. This page will be updated when the connector is released.

## Planned Capabilities

[2-3 sentences describing what this connector will do when built.]
```

- [ ] Write `docs/connectors/oci.md` — Oracle Cloud Infrastructure: compute and storage discovery, VM lifecycle management.
- [ ] Write `docs/connectors/ldap.md` — Generic LDAP directory: user/group discovery, account enable/disable, OU management.
- [ ] Write `docs/connectors/keycloak.md` — Keycloak IAM: realm user management, client credential rotation, session revocation.
- [ ] Write `docs/connectors/winrm.md` — Windows Remote Management: remote command execution on Windows hosts without the Nexplane Agent.
- [ ] Write `docs/connectors/postgres.md` — PostgreSQL: database user provisioning, permission management, audit log configuration (direct connection, no agent required).
- [ ] Write `docs/connectors/redis.md` — Redis: key inspection, ACL management, cluster health discovery.
- [ ] Write `docs/connectors/mongodb.md` — MongoDB: database user provisioning, role management, collection-level access control.

- [ ] Commit

```bash
git add docs/connectors/oci.md docs/connectors/ldap.md docs/connectors/keycloak.md docs/connectors/winrm.md docs/connectors/postgres.md docs/connectors/redis.md docs/connectors/mongodb.md
git commit -m "docs: add placeholder connector pages for planned connectors"
```

---

## Task 16: Agent (5 pages)

- [ ] Write `docs/agent/index.md`

Overview: why it exists (cloud APIs can't reach inside a host, no inbound SSH needed), outbound long-poll model, binary distribution (S3 public bucket, all 3 platforms), HMAC-SHA256 job signing, self-registration, 15 command packages summary table with platform support.

- [ ] Write `docs/agent/installation.md`

Linux one-liner download, `chmod +x`, run. systemd unit file (full [Unit]/[Service]/[Install] sections). Windows PowerShell `New-Service` command. Full flag/env reference table (--control-plane/NP_CONTROL_PLANE, --secret/NP_SECRET, --mode/NP_MODE, --hostname/NP_HOSTNAME, --poll-interval/NP_POLL_INTERVAL).

- [ ] Write `docs/agent/commands.md`

Full table of all 15 command packages with every command and platform support (Linux/Windows/Both). Match README agent command packages section exactly:

| Package | Commands | Platform |
|---------|----------|----------|
| changip | change_ip (tailscale/secondary_swap/commit_timer/manual), change_ip_rollback | Both |
| linuxpatch | apply_linux_patches, audit_linux_patch_status | Linux |
| winpatch | apply_windows_patches, audit_windows_patch_status | Windows |
| isolation | isolate_host, restore_network_access | Both |
| forensics | collect_forensics | Both |
| compliance | audit_cis_compliance, collect_evidence | Linux |
| credrotation | rotate_db_credentials, rotate_ssh_keys, update_agent_env_file | Both |
| iac | terraform_plan, terraform_apply, terraform_rollback, ansible_check, ansible_run, helm_diff, helm_upgrade, helm_rollback | Linux |
| fleet | restart_service, push_config_file, distribute_file, health_check | Both |
| backup | create_backup, restore_files | Both |
| reboot | graceful_reboot, verify_post_reboot | Both |
| dbadmin | provision_db_user, deprovision_db_user, grant_permissions, revoke_permissions, configure_db_audit, db_connection_config | PostgreSQL/MySQL/MSSQL |
| ossecurity | configure_selinux, configure_seccomp, apply_sysctl_hardening, configure_host_firewall, blacklist_kernel_modules, harden_mount_options, deploy_auditd_rules, setup_file_integrity_monitoring, audit_os_security_posture, audit_ebpf_posture, configure_ebpf_security_policy, deploy_ebpf_policy | Linux |
| linuxauth | harden_ssh, configure_pam, manage_ca_certificates, configure_ntp, audit_users_and_groups, audit_privesc_vulnerabilities | Linux |
| winharden | LAPS, Credential Guard, PowerShell CLM, AppLocker, SMB, BitLocker, Windows Firewall, TLS, RDP, audit policy, registry | Windows |
| crossplatform | harden_tls_protocols, configure_dns_resolver, audit_software_inventory, configure_syslog | Both |
| linuxupgrade | estimate_image_size, in-place OS upgrade, containerize-and-migrate | Linux |

- [ ] Write `docs/agent/self-update.md`

On startup: fetch `version` file from S3, compare to running version, download new binary if behind, verify SHA256 checksum, atomic replace via `os.Rename` + `syscall.Exec` (Linux/macOS) or log manual update message (Windows). S3 URLs. SHA256 sidecar files. Version file URL.

- [ ] Write `docs/agent/windows.md`

Windows-specific: winpatch (WUA COM API, specific KB, reboot scheduling), winharden (LAPS/Credential Guard/PowerShell CLM/AppLocker/SMB/BitLocker/Windows Firewall/TLS/RDP/audit policy/registry), changip on Windows (netsh), PowerShell service setup, self-update behavior on Windows (no atomic exec — log message only). Note: Free Tier AWS cannot launch Windows instances (paid account required for Windows Server AMIs).

- [ ] Commit

```bash
git add docs/agent/
git commit -m "docs: rewrite agent section with accurate capabilities and command reference"
```

---

## Task 17: Security (4 pages)

- [ ] Write `docs/security/model.md`

JWT + bcrypt for user auth. HMAC-SHA256 for agent job signing (shared secret generated in Settings). Fernet AES-256 for connector credentials. Role-based access (admin/operator/approver/auditor). ir_responder role for change freeze bypass.

- [ ] Write `docs/security/credentials.md`

SecretsService (cryptography.fernet, AES-256). Versioning: rotate generates new key version, old versions retained with 7-day TTL for rollback. Never returned in GET responses. Designed for HashiCorp Vault / AWS Secrets Manager / HSM swap-out (abstracted interface).

- [ ] Write `docs/security/safety-engine.md`

Full safety table from README:

| Scenario | Behavior |
|----------|----------|
| Prod + critical asset | high or critical risk score |
| Missing rollback strategy (prod/critical) | Blocked — cannot generate plan |
| Remote command without approved template | Blocked at safety review |
| Freeform shell command | Blocked unconditionally |
| Critical risk change | Requires 2 approvals (approver + admin) |
| Microsegmentation policy | Staged simulation mode only |
| Agent job with invalid HMAC | Rejected before execution |
| AI not configured | 402 response; UI shows inline guidance |
| Active change freeze | 423 Locked on approve/execute; bypass requires justification + ir_responder role |
| Step credential output | Never written to logs or DB — travels in memory only |
| IaC apply | Gated behind explicit plan review and approval |
| DB replica promotion | rollback_supported: false; blast radius warning required in approval |

- [ ] Write `docs/security/network-exposure.md`

Backend API binds to `127.0.0.1:8000` — not exposed to the public internet. Frontend (port 3000) is the only externally accessible service in default Docker Compose config. Agent binary distribution via S3 (not through backend). Agent communicates outbound only — no inbound ports required on managed hosts. Backend ↔ frontend: HTTP on localhost only.

- [ ] Commit

```bash
git add docs/security/
git commit -m "docs: rewrite security section with accurate model, safety engine, and network exposure"
```

---

## Task 18: API Reference

- [ ] Write `docs/api/index.md`

Interactive docs available at `http://localhost:8000/docs` (Swagger UI via FastAPI). List all router groups:

- `/auth/*` — login, logout, current user
- `/assets/*` — inventory CRUD, tag management, bulk tagging, ingest
- `/connectors/*` — connector CRUD, test, credentials, schedule, ingest
- `/projects/*` — project CRUD, member management, AI chat
- `/change-requests/*` — full CR lifecycle (plan→approve→execute→verify→rollback) + batch progress
- `/runbooks/*` — CRUD, fork, trigger; `/executions/*` — status, resume checkpoint, abort
- `/vulnerability/*` — findings CRUD, policies, SLA dashboard, CVE blast-radius, patch campaign; `POST /webhooks/vulnerability-findings`
- `/ir/*` — IR playbook templates, execute, forensic bundles
- `/access-reviews/*` — collect, decisions, approve, auto-generate removal CRs
- `/compliance/*` — baselines CRUD, drift alerts, evidence ZIP download, freeze windows
- `/maintenance-windows/*` — CRUD, status check
- `/agent/*` — agent registration, job dispatch, result reporting
- `/settings/*` — AI providers, agent secret, remediation policies
- `/downloads/*` — versioned agent binaries + SHA256 checksums + version file
- `/audit-events/*` — immutable audit trail

Authentication: all endpoints require Bearer JWT (obtained from `POST /auth/login`).

- [ ] Commit

```bash
git add docs/api/
git commit -m "docs: rewrite API reference"
```

---

## Task 19: Push

- [ ] Push all commits to GitHub

```bash
git push origin main
```

Expected: Netlify auto-deployment triggered. Site live at https://docs.nexplane.ai within ~2 minutes.

- [ ] Verify deployment at https://docs.nexplane.ai

---

## Self-Review

**Spec coverage:** All sections from the design spec are covered — nav, index, getting-started, concepts, features (all 9), change-types (all 10 categories), connectors (35 real + 7 placeholder), agent (5 pages), security (4 pages), API.

**Placeholder scan:** No TBD, TODO, or "similar to above" patterns. Every task specifies exact content.

**Type consistency:** No code — content-only plan. File paths are consistent throughout.
