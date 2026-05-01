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
- **Connectors** — integrations with AWS, Azure, Active Directory, CrowdStrike, Tenable, Palo Alto, Cloudflare, Okta, SSH
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
│  Mock Connectors                                                 │
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
| AI | Anthropic Claude (claude-sonnet-4-6) via `anthropic` SDK |
| Secrets | `cryptography.fernet` (AES-256); abstracted for HSM/Vault swap-out |
| Agent | Go 1.22+, AWS SDK v2, `golang.org/x/sys` |
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

Requires an Anthropic API key configured in Settings (admin only, encrypted at rest).

### Connectors and Ingest

Connectors come in two action types:
- **change** — push a configuration change to an external system
- **ingest** — pull asset and identity data into Nexplane's inventory

Each connector card in the UI shows a **Run Discovery** button for connectors that support ingest. Discovered assets (servers, identities, applications, firewalls, cloud accounts) are upserted into the asset inventory by `(organization_id, name)` deduplication.

| Connector | Ingest | Change actions |
|-----------|--------|----------------|
| AWS | VMs, snapshots | security groups, snapshots, key rotation |
| Azure | VMs, storage, NSGs | NSG rules, public blob access, storage keys |
| Cloudflare | — | DNS records |
| Active Directory | Computers, identities | disable/enable account, reset password, MFA enforcement |
| CrowdStrike | Endpoints, users, applications | deploy sensor, isolate host, contain process |
| Tenable | Assets, vulnerabilities, local accounts | trigger scan, verify remediation |
| Palo Alto | Traffic logs, security events | microsegmentation policy, chokepoint rules, traffic logging |
| Okta | — | key generation, distribution, revocation |
| SSH | — | agent install, SELinux policy, workload containerization |
| Nexplane Agent | Users/groups, privesc findings, software inventory, scheduled tasks, OS security posture | change IP, configure syslog, virtualize for migration, upload image, Linux/Windows security hardening, TLS certificate management, DNS resolver, instance upgrade |

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
│   │   │   ├── asset.py            # AssetType includes: server, identity, application, ...
│   │   │   ├── connector.py        # ConnectorType includes all 10 connectors
│   │   │   ├── project.py          # Project + ProjectChangeRequest (dependency graph)
│   │   │   ├── agent.py            # AgentRegistration + AgentJob
│   │   │   └── org_settings.py     # Anthropic API key + agent secret (Fernet-encrypted)
│   │   ├── schemas/
│   │   ├── routers/
│   │   │   ├── agent.py            # /agent/register, /agent/jobs/next, /agent/jobs/{id}/result
│   │   │   ├── projects.py         # Projects CRUD + /projects/{id}/ai/chat
│   │   │   ├── settings.py         # GET/PUT /settings/ai-key, POST /settings/agent-secret
│   │   │   └── connectors.py       # Connectors + POST /connectors/{id}/ingest/{action_id}
│   │   ├── services/
│   │   │   ├── ai_service.py       # Claude API integration + <nexplane-proposal> parsing
│   │   │   ├── ingest_service.py   # Asset upsert pipeline for ingest connectors
│   │   │   ├── agent_hmac.py       # Job signing (matches agent/agenthmac)
│   │   │   ├── secrets_service.py  # Fernet AES-256 (Vault/HSM-swappable interface)
│   │   │   ├── safety_engine.py
│   │   │   ├── planning_engine.py
│   │   │   └── connector_service.py
│   │   ├── connectors/
│   │   │   ├── catalog/            # Per-connector JSON action catalogs (10 connectors)
│   │   │   └── executors/          # Per-action executor modules
│   │   └── tests/                  # 116 passing tests
│   ├── alembic/versions/           # 006 migrations (001→006)
│   └── seed.py                     # Demo data (org, users, assets, connectors, projects)
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Projects.tsx        # Project list
│       │   ├── ProjectDetail.tsx   # Member management + AI panel + execution view
│       │   ├── Settings.tsx        # AI key + Agent secret management
│       │   ├── Connectors.tsx      # Connector cards + Test + Run Discovery
│       │   ├── Assets.tsx          # Asset inventory with tag search
│       │   └── AssetDetail.tsx     # Full asset detail + edit
│       ├── components/
│       │   ├── AIPanel.tsx         # Conversational AI planning panel
│       │   ├── Sidebar.tsx
│       │   └── LogoMark.tsx        # SVG logo mark
│       └── types/api.ts            # Centralized TypeScript API types
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
| Anthropic API key | Admin only | Required for AI planning assistant. Encrypted at rest, never returned in API responses. |
| Agent secret | Admin only | Shared HMAC secret for agent authentication. Shown once on generation — store securely. |

Both secrets use the same `SecretsService` (Fernet AES-256), designed as a swappable interface for future HashiCorp Vault, AWS Secrets Manager, or HSM integration.

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
- `/settings/*` — AI key, agent secret
- `/audit-events/*` — immutable audit trail
