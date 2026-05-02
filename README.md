# Nexplane

**The control plane for security execution.**

Nexplane connects intent to action across your infrastructure — enabling security engineering and architecture teams to execute infrastructure changes safely, without routing work through sysadmin, network admin, or SRE queues.

> *Execute with Confidence*

---

## What It Does

Security teams identify issues and need to act on them: rotate compromised keys, isolate a compromised endpoint, tighten firewall rules after a scan, deploy an EDR sensor to unprotected hosts, configure syslog forwarding, enforce MFA. Today, all of that flows through tickets.

Nexplane gives security teams a governed execution layer:

- **Projects** — group related change requests into a sequenced plan with dependency tracking
- **AI Planning Assistant** — describe your goal, get a structured change plan with proposed change requests
- **Change Requests** — safety-reviewed, approval-gated, audited, with automatic rollback
- **Asset Inventory** — servers, cloud accounts, firewalls, identities, applications — discoverable via connectors
- **Connectors** — 38 integrations spanning cloud, identity, EDR, IaC, ticketing, and observability — with real API calls when credentials are configured
- **Nexplane Agent** — a cross-platform Go binary that runs on managed machines, reaches out to the control plane, and executes signed commands — no inbound SSH required

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  React Frontend  (Vite + TypeScript + Tailwind CSS)             │
│                                                                  │
│  Dashboard · Projects · Change Requests · Approvals ·           │
│  Asset Inventory · Connectors · Settings                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP/REST
┌───────────────────────────▼─────────────────────────────────────┐
│  FastAPI Backend  (Python 3.12)                                  │
│                                                                  │
│  Safety Engine · Planning Engine · AI Service · Audit Service   │
│  Secrets Service (Fernet AES-256, HSM/Vault-swappable)          │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Action Catalog  (per-connector JSON + executor modules)  │   │
│  │   Tier 1: direct_api  ·  Tier 3: agent  ·  Tier 5: ssh  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Connectors (real API + mock fallback)                           │
│  aws · azure · cloudflare · okta · paloalto · ssh               │
│  active_directory · crowdstrike · tenable · nexplane_agent      │
│                                                                  │
│  Agent API  (/agent/register · /agent/jobs/next · /agent/result)│
└───────────────────────────┬─────────────────────────────────────┘
                            │ SQLAlchemy async
┌───────────────────────────▼─────────────────────────────────────┐
│  PostgreSQL 16                                                    │
└──────────────────────────────────────────────────────────────────┘

                    ┌────────────────────────┐
                    │  Nexplane Agent (Go)    │
                    │  linux/amd64 · arm64   │
                    │  windows/amd64          │
                    │                         │
                    │  Outbound poll only —   │
                    │  no inbound SSH needed  │
                    └────────────┬────────────┘
                                 │ long-poll HTTP (outbound)
                                 └──► /agent/jobs/next
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, React Router v6 |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async, Pydantic |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Workflow | Temporal-pattern abstraction (asyncio MVP, Temporal-ready) |
| Auth | JWT + bcrypt |
| AI | Anthropic Claude + OpenAI (multi-provider, default configurable) |
| Secrets | `cryptography.fernet` (AES-256); abstracted for HSM/Vault swap-out |
| Agent | Go 1.22+, AWS SDK v2, `golang.org/x/sys` |
| Connector SDKs | boto3, azure-sdk, google-cloud-*, msal, hvac, kubernetes, falconpy, pytenable, pan-os-python, httpx, paramiko, checkov, google-api-python-client |
| Scheduler | APScheduler 3.x (in-process async, recurring ingest) |
| Graph | React Flow 11 + @dagrejs/dagre (project dependency visualization) |
| Deployment | Docker Compose |

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

Group change requests into sequenced, dependency-linked security initiatives. Built-in dependency graph prevents executing CR B before CR A completes.

Projects support:
- Manual assembly (add existing CRs or create inline)
- AI-assisted planning (describe the goal, get proposed change requests)
- Execution view with per-CR status tracking
- Draft → In Progress → Completed lifecycle

### AI Planning Assistant

Available on draft projects. Opens a conversational panel where an operator describes their goal (e.g. "microsegmentation for the payments subnet") and the AI:
1. Asks targeted clarifying questions referencing your actual asset inventory
2. Proposes a structured change plan as `<nexplane-proposal>` blocks
3. Lets operators add proposed CRs to the project with one click

Supports **Anthropic** (Claude) and **OpenAI** as AI providers. Configure API keys in Settings → AI Providers (admin only, encrypted at rest). Select the default provider per organization.

### Connectors and Ingest

Connectors come in two action types:
- **change** — push a configuration change to an external system
- **ingest** — pull asset and identity data into Nexplane's inventory

Each connector card in the UI shows:
- **Run Discovery** button for connectors that support ingest
- **Configure Credentials** button to store real API credentials (encrypted via SecretsService)
- **Schedule** button to set up recurring discovery (every 1h / 6h / 24h / weekly via APScheduler)

Discovered assets (servers, identities, applications, firewalls, cloud accounts) are upserted into the asset inventory by `(organization_id, name)` deduplication.

All connectors make **real API calls** when credentials are configured, and fall back to mock responses for demo mode when no credentials are set.

**Cloud & Infrastructure (10 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| AWS | boto3 | Discover EC2/IAM/S3/SGs; stop/start/terminate; enforce IMDSv2; GuardDuty; CloudTrail; rotate IAM keys |
| Azure | azure-sdk, msal | Discover VMs/NSGs/Entra users; disable accounts; revoke sessions; Defender alerts; policy compliance |
| GCP | google-cloud-* | Discover compute/IAM/storage/firewall/SAs; SCC findings; stop/start/delete; block public buckets |
| Cloudflare | httpx | Discover WAF/firewall/access policies; block IP; WAF rule actions; SSL mode |
| Palo Alto | pan-os-python | Discover address objects/rules/zones; create/delete firewall rules; block IP; commit |
| Active Directory | ldap3 | Discover domain admins/stale accounts/SPNs/GPOs; disable stale accounts; move OU |
| CrowdStrike | falconpy | Discover endpoints/alerts/vulns; isolate host; RTR commands; prevention policy updates |
| Tenable | pytenable | Discover assets/vulns/scan policies; launch/pause/resume scans; export reports |
| SSH | paramiko | Execute approved commands; check service status; tail logs |
| Nexplane Agent | (internal) | Full OS security hardening (Linux + Windows); migration; TLS; DNS; software inventory |

**Identity & Access (4 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| Okta | httpx | Discover users/groups/apps; suspend/deactivate; reset MFA/password; revoke sessions; force enrollment |
| Microsoft Entra ID | msal, httpx | Discover users/groups/apps/CA policies/privileged roles; disable/enable; reset MFA; block sign-in |
| HashiCorp Vault | hvac | Discover secret engines/auth methods/policies/leases; rotate secrets; revoke leases; seal vault |
| GitHub | httpx | Discover repos/alerts/Dependabot/org members; branch protection; suspend member; secret scanning |

**Security Tools / EDR (4 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| SentinelOne | httpx | Discover agents/threats/groups; isolate/reconnect; kill process; quarantine file; initiate scan |
| Microsoft Defender for Endpoint | msal, httpx | Discover machines/alerts/vulns/software; isolate; AV scan; initiate investigation |
| Snyk | httpx | Discover projects/issues/dependencies/container images; trigger tests; ignore issues |
| Qualys | httpx | Discover hosts/vulns/scan schedules/asset groups; launch scans; verify remediation |

**Container & Kubernetes (2 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| Kubernetes | kubernetes | Discover nodes/pods/workloads/RBAC/network policies; delete pod; cordon/drain node; patch deployment |
| Helm | kubernetes | Discover releases/history; rollback release; uninstall release |

**Cloud Security & Discovery (3 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| RunZero | httpx | Discover all network assets/services/wireless (finds unmanaged IoT/OT); trigger scans |
| Wiz | httpx (GraphQL) | Discover cloud resources; ingest issues/vulnerabilities/attack paths; resolve issues; accept risk |
| Zscaler | httpx | Discover users/policies/locations/ZPA apps; block URLs/IPs; suspend users; URL categories |

**IaC & Configuration Management (9 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| Terraform (HCP) | httpx | Discover workspaces/runs/state; plan/apply/destroy; lock/unlock; set variables |
| Ansible (AWX) | httpx | Discover inventories/hosts/job templates/jobs; launch jobs; ad-hoc commands; sync inventory |
| AWS CloudFormation | boto3 | Discover stacks/resources; drift detection; create/execute change sets; termination protection |
| Pulumi | httpx | Discover stacks/resources/history; cancel update; import resource; refresh stack |
| Helm | kubernetes | (see above) |
| Azure Bicep | azure-mgmt-resource | Discover deployments/operations; validate template; create/cancel/delete deployments |
| Checkov | subprocess | IaC security scan; secrets detection; compliance summary (CIS/NIST/PCI-DSS) |
| SaltStack | httpx | Discover minions/jobs; run states; allowlisted exec module functions; accept/reject keys |
| Chef InSpec | httpx | Discover nodes/compliance profiles/results; run compliance scans; assign profiles |

**Workflow & Observability (7 connectors)**

| Connector | SDK | Key capabilities |
|-----------|-----|-----------------|
| Jira | httpx | Discover projects/issues; create/transition issues; add comments; link issues; assign |
| PagerDuty | httpx | Discover services/incidents/on-call; create/resolve/acknowledge incidents; Events API |
| ServiceNow | httpx | Discover incidents/change requests/CMDB; create/update/resolve incidents; sync CMDB |
| Splunk | httpx | SPL search; discover saved searches/notables; send events to HEC; create alerts; suppress notables |
| Datadog | httpx | Discover hosts/monitors/security signals/log indexes; mute/unmute; create monitors; send events |
| Google Workspace | google-api-python-client | Discover users/groups/devices/admin roles/audit logs; suspend; reset password; revoke tokens; wipe device |

### Nexplane Agent

A cross-platform Go binary that reverses the connection direction: the agent runs on a managed machine and reaches out to the control plane. No inbound SSH required — the agent polls for jobs and executes signed commands.

**Security model:**
- Bearer token authentication (shared HMAC secret, generated in Settings)
- Each job payload signed with HMAC-SHA256; agent verifies before executing
- Agent registers itself — the managed machine appears as an Asset automatically

**Commands with rollback — infrastructure operations:**

| Command | What it does | Platform | Rollback |
|---------|-------------|---------|---------|
| `estimate_image_size` | Checks available disk space before imaging | Both | Read-only |
| `change_ip` | Changes interface IP (IPv4/IPv6/DHCP) | Both | Restore snapshot |
| `configure_syslog` | Forwards logs to a remote collector | Both | Restore config |
| `virtualize_for_migration` | Creates disk image with target IP pre-configured | Both | Delete image |
| `upload_image` | Uploads image to S3 (multipart, credential chain) | Both | Delete S3 object |
| `upgrade_linux_instance` | In-place package/kernel upgrade or containerize-and-migrate for major OS upgrades | Linux | Cloud or dd snapshot restore |

**Commands with rollback — Linux security hardening:**

| Command | What it does | Rollback |
|---------|-------------|---------|
| `configure_selinux` | Set SELinux mode; install/generate policy modules | Restore mode + unload modules |
| `configure_apparmor` | Load profile; set enforce/complain/disable mode | Restore previous profile state |
| `configure_seccomp` | Apply seccomp filter to systemd service or container | Remove drop-in, reload service |
| `apply_sysctl_hardening` | Apply CIS/STIG sysctl parameters (IP forward, SYN cookies, etc.) | Remove drop-in file, restore values |
| `configure_host_firewall` | Add/remove iptables/nftables/firewalld rules | Restore ruleset snapshot |
| `blacklist_kernel_modules` | Prevent loading of unnecessary/dangerous modules | Remove blacklist file |
| `harden_mount_options` | Apply noexec/nosuid/nodev to /tmp, /dev/shm | Restore fstab options |
| `deploy_auditd_rules` | Install CIS/STIG/custom auditd rule sets | Remove rules, reload auditd |
| `setup_file_integrity_monitoring` | Initialize AIDE/Tripwire baseline; run diff | Remove integrity database |
| `deploy_ebpf_policy` | Load and attach eBPF programs (kprobe, tc, XDP, LSM) | Detach and unload program |
| `configure_ebpf_security_policy` | Apply Cilium/Falco/Tetragon declarative policy | Delete/disable policy |
| `configure_pam` | Password complexity, account lockout, session limits | Restore PAM config backup |
| `harden_ssh` | Disable root login, key-only auth, cipher allowlist | Restore sshd_config, reload |
| `manage_ca_certificates` | Install/remove CA cert to OS trust store | Remove cert, re-run trust update |
| `configure_ntp` | Configure chrony/timesyncd/ntpd time sources | Restore config, restart service |

**Commands with rollback — Windows security hardening:**

| Command | What it does | Rollback |
|---------|-------------|---------|
| `configure_laps` | Enable/disable LAPS for local admin password rotation | Restore registry |
| `enable_credential_guard` | Enable VBS credential isolation | Restore registry (reboot required) |
| `enforce_powershell_clm` | Enforce PowerShell Constrained Language Mode | Restore registry or WDAC policy |
| `deploy_applocker_policy` | Deploy AppLocker allowlist (audit or enforce mode) | Restore previous effective policy |
| `harden_smb` | Disable SMBv1, require signing, disable guest access | Restore SMB configuration |
| `enable_bitlocker` | Enable BitLocker full disk encryption | Disable BitLocker (decrypt) |
| `configure_windows_firewall` | Add/remove firewall rules, set default actions | Restore full firewall policy export |
| `harden_tls_protocols` | Disable SSL/TLS 1.0/1.1, enforce cipher suite order | Restore SCHANNEL registry |
| `harden_rdp` | Require NLA, set encryption level, idle timeout | Restore RDP registry settings |
| `configure_windows_audit_policy` | Apply CIS/STIG/custom auditpol settings | Restore per-subcategory settings |
| `harden_registry` | Disable autorun, LM hash, WDigest, NTLMv1, etc. | Restore registry export |

**Commands with rollback — cross-platform:**

| Command | What it does | Platform | Rollback |
|---------|-------------|---------|---------|
| `manage_tls_certificates` | Deploy/renew/validate TLS certs (ACME, internal CA, manual) | Both | Restore previous cert files |
| `configure_dns_resolver` | Configure DoH/DoT/plain DNS resolvers | Both | Restore resolver config |

**Read-only ingest commands (no rollback needed):**

| Command | What it collects | Platform |
|---------|-----------------|---------|
| `audit_os_security_posture` | SELinux/AppArmor/seccomp state and active denials | Linux |
| `audit_ebpf_posture` | Loaded eBPF programs and attachment points | Linux |
| `audit_users_and_groups` | No-expiry accounts, UID 0 non-root, empty passwords, sudo members | Linux |
| `audit_privesc_vulnerabilities` | PwnKit, DirtyPipe, unexpected SUID, writable cron, sudo misconfig | Linux |
| `audit_scheduled_tasks` | All scheduled tasks; flags third-party, privileged, hidden, writable-binary tasks | Windows |
| `audit_software_inventory` | Installed packages, Store apps, features (dpkg/rpm/winget) | Both |

**Full migration workflow:**
```
CR 1: estimate_image_size     (agent)  — preflight: fail fast if no space
CR 2: virtualize_for_migration (agent) — dd image with target IP
CR 3: upload_image            (agent)  — push to S3
CR 4: launch EC2 instance     (AWS connector)
```

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
├── agent/                          # Nexplane Agent (Go)
│   ├── main.go                     # Entry point — config, fingerprint, register, poll
│   ├── go.mod
│   ├── Makefile                    # make build / make test
│   ├── config/                     # Flag + env var config (--control-plane, --secret, --mode)
│   ├── fingerprint/                # Stable machine ID (OS UUID → MAC hash fallback)
│   ├── agenthmac/                  # HMAC-SHA256 sign/verify for job payloads
│   ├── client/                     # HTTP client — register, poll, post result
│   ├── registration/               # Registration logic + IP collection
│   ├── poller/                     # Ephemeral (run-once) and service (loop) modes
│   ├── executor/                   # Command dispatcher
│   └── commands/
│       ├── estimatesize/           # Disk space preflight (Linux: statfs, Windows: GetDiskFreeSpaceEx)
│       ├── changip/                # IP change (nmcli/systemd-networkd/ifcfg/netsh)
│       ├── configsyslog/           # Syslog forwarding (rsyslog/syslog-ng/NXLog/WEF)
│       ├── virtualize/             # Disk imaging (dd/losetup/mount/VHD/robocopy)
│       ├── uploadimage/            # S3 multipart upload (AWS SDK v2)
│       ├── ossecurity/             # Linux MAC/kernel/integrity hardening (Spec 5a)
│       ├── ebpf/                   # Linux eBPF program management (Spec 5a)
│       ├── linuxauth/              # Linux PAM, SSH, user audit, privesc audit, CA certs, NTP (Spec 5b)
│       ├── winharden/              # Windows security hardening suite (Spec 5c)
│       ├── crossplatform/          # Cross-platform TLS, DNS, software inventory (Spec 5d)
│       └── linuxupgrade/           # Linux in-place and containerize-and-migrate upgrade (Spec 5e)
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── models/
│   │   │   ├── asset.py              # AssetType: server, identity, application, ...
│   │   │   ├── connector.py          # ConnectorType: 38 connectors across 6 categories
│   │   │   ├── connector_credential.py # Per-connector encrypted credential storage
│   │   │   ├── project.py            # Project + ProjectChangeRequest (dependency graph)
│   │   │   ├── agent.py              # AgentRegistration + AgentJob
│   │   │   ├── scheduled_ingest.py   # Recurring ingest schedule (APScheduler)
│   │   │   └── org_settings.py       # AI provider keys + agent secret (Fernet-encrypted)
│   │   ├── schemas/
│   │   │   ├── credential.py         # CredentialRead/Write, AIProvidersRead/Write
│   │   │   └── scheduled_ingest.py   # ScheduledIngestRead/Write
│   │   ├── routers/
│   │   │   ├── agent.py              # /agent/register, /agent/jobs/next, /agent/jobs/{id}/result
│   │   │   ├── projects.py           # Projects CRUD + /projects/{id}/ai/chat
│   │   │   ├── settings.py           # /settings/ai-providers, /settings/agent-secret
│   │   │   └── connectors.py         # Connectors + credentials + schedule + ingest
│   │   ├── services/
│   │   │   ├── ai_service.py         # Multi-provider AI integration + <nexplane-proposal> parsing
│   │   │   ├── ingest_service.py     # Asset upsert pipeline for ingest connectors
│   │   │   ├── scheduler_service.py  # APScheduler wrapper for recurring ingest jobs
│   │   │   ├── agent_hmac.py         # Job signing (matches agent/agenthmac)
│   │   │   ├── secrets_service.py    # Fernet AES-256 + JSON helpers (Vault/HSM-swappable)
│   │   │   ├── safety_engine.py
│   │   │   ├── planning_engine.py
│   │   │   └── connector_service.py  # Executor dispatch + credential injection
│   │   ├── connectors/
│   │   │   ├── catalog/              # Per-connector JSON catalogs with credential_fields
│   │   │   └── executors/            # Real API implementations (mock fallback if no creds)
│   │   └── tests/                    # 116 passing tests
│   ├── alembic/versions/             # 013 migrations (001→013)
│   └── seed.py                       # Demo data (org, users, assets, connectors, projects)
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Projects.tsx          # Project list
│       │   ├── ProjectDetail.tsx     # Member management + AI panel + List/Graph tab toggle
│       │   ├── Settings.tsx          # AI Providers (Anthropic/OpenAI) + Agent secret
│       │   ├── Connectors.tsx        # Connector cards + credentials + schedule + Run Discovery
│       │   ├── Assets.tsx            # Asset inventory with tag search
│       │   └── AssetDetail.tsx       # Full asset detail + edit
│       ├── components/
│       │   ├── AIPanel.tsx           # Conversational AI planning panel
│       │   ├── CredentialModal.tsx   # Per-connector credential configuration modal
│       │   ├── ScheduleModal.tsx     # Recurring ingest interval picker (1h/6h/24h/weekly)
│       │   ├── ProjectGraph/         # Dependency DAG (React Flow + Dagre auto-layout)
│       │   │   ├── index.tsx         # Graph wrapper with ReactFlowProvider
│       │   │   ├── CRNode.tsx        # Status-colored CR nodes
│       │   │   ├── AssetNode.tsx     # Type-colored asset pill nodes
│       │   │   ├── useGraphLayout.ts # Dagre layout hook + asset data fetching
│       │   │   └── graphUtils.ts     # Color maps and layout constants
│       │   ├── Sidebar.tsx
│       │   └── LogoMark.tsx          # SVG logo mark
│       └── types/api.ts              # Centralized TypeScript API types
│
├── docs/superpowers/
│   ├── specs/                      # Design specs (Specs 1–5e)
│   └── plans/                      # Implementation plans
│
└── docker-compose.yml
```

---

## Building the Agent

```bash
cd agent

# Run tests
go test ./...

# Build all targets
make build

# Outputs:
#   dist/nexplane-agent-linux-amd64
#   dist/nexplane-agent-linux-arm64
#   dist/nexplane-agent-windows-amd64.exe
```

### Running the Agent

First, generate an agent secret in **Settings → Agent Configuration** (admin only). Then:

**Ephemeral (via SSH, run once):**
```bash
./nexplane-agent-linux-amd64 \
  --control-plane https://nexplane.acme.example:8000 \
  --secret sk-agent-<your-secret> \
  --mode ephemeral
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
  -BinaryPathName "C:\nexplane\nexplane-agent.exe --mode service --poll-interval 30s" `
  -StartupType Automatic
Start-Service NexplaneAgent
```

**Environment variable equivalents:**

| Flag | Env var | Default |
|------|---------|---------|
| `--control-plane` | `NP_CONTROL_PLANE` | (required) |
| `--secret` | `NP_SECRET` | (required) |
| `--mode` | `NP_MODE` | `service` |
| `--poll-interval` | `NP_POLL_INTERVAL` | `30s` |

---

## Settings

| Setting | Who can configure | Notes |
|---------|------------------|-------|
| AI Providers (Anthropic, OpenAI) | Admin only | Configure API keys for each provider; select default. Encrypted at rest. |
| Agent secret | Admin only | Shared HMAC secret for agent authentication. Shown once on generation — store securely. |
| Connector credentials | Operator+ | Per-connector API credentials (AWS keys, Okta tokens, AD bind password, etc.). Encrypted at rest, never returned in GET responses. |

All secrets use `SecretsService` (Fernet AES-256), designed as a swappable interface for future HashiCorp Vault, AWS Secrets Manager, or HSM integration.

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
# Backend (116 tests)
cd backend
pytest

# Agent (11 packages)
cd agent
go test ./...
```

> **Note:** When running via Docker Compose on Windows, Vite's file watcher may not pick up changes. If edits don't appear after a hard refresh, run:
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
| Agent job with invalid HMAC | Rejected before execution, failure posted back to control plane |
| AI not configured | 402 response; UI shows inline guidance to admin |

---

## Workflow Architecture

The `ExecuteChangeWorkflow` mirrors Temporal's design principles:

- **Deterministic orchestration** — no side effects in the workflow function
- **Activity isolation** — all DB writes, connector calls, and I/O in Activities
- **Swap-ready** — replace `runner.py` with a `temporalio.client.Client` to run against a real Temporal cluster

---

## API Documentation

Interactive OpenAPI docs: `http://localhost:8000/docs`

Key endpoint groups:
- `/auth/*` — login, logout, current user
- `/assets/*` — inventory CRUD, tag management, bulk tagging, ingest
- `/connectors/*` — connector CRUD, test, ingest discovery
- `/projects/*` — project CRUD, member management, AI chat
- `/change-requests/*` — full CR lifecycle (plan → approve → execute → verify → rollback)
- `/agent/*` — agent registration, job dispatch, result reporting
- `/settings/*` — AI provider keys (Anthropic/OpenAI), agent secret
- `/connectors/{id}/credentials` — per-connector credential CRUD
- `/connectors/{id}/schedule` — recurring ingest schedule CRUD
- `/audit-events/*` — immutable audit trail
