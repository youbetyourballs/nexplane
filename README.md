# Nexplane — The control plane for infrastructure change

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Eliminate uncertainty before every infrastructure change.

---

## The four questions Nexplane answers

| Question | How Nexplane helps |
|----------|--------------------|
| **What do I have?** | Asset inventory across 70+ connectors — cloud, identity, network, endpoints, secrets — continuously synced via configured connectors |
| **What will happen if I change it?** | Impact simulation shows affected assets, dependencies, and blast radius before execution |
| **Who needs to approve it?** | Approval gates with role-based routing, audit trail, and human checkpoints |
| **Can I safely undo it?** | Every change ships with a rollback plan. Rollback is a first-class operation, not an afterthought |

---

## What is Nexplane?

Nexplane is an infrastructure change control platform for teams that need auditability, approval gates, and rollback on every change. It sits between your infrastructure connectors and the people (or AI agents) who want to modify them — enforcing a consistent lifecycle of discovery, planning, approval, execution, and rollback.

Security is the wedge use case: removing stale firewall rules, rotating secrets, remediating findings, and hardening endpoints. But the platform is built for any infrastructure change — DNS updates, VM operations, IAM modifications, certificate rotations, network policy changes, and more. If it touches infrastructure and needs an audit trail, Nexplane is the right home for it.

---

## Who it's for

- **Platform engineering** — standardize how infrastructure changes are proposed, reviewed, and executed across teams
- **Infrastructure engineering** — eliminate change anxiety with blast radius analysis and guaranteed rollback
- **Cloud engineering** — manage cloud config changes with approval gates, audit trail, and connector-native execution
- **SRE / production engineering** — tie every change to a rollback plan before execution; observe outcomes in real time
- **Network engineering** — execute and document network changes with pre/post verification steps
- **Security architecture** — harden infrastructure and track remediation through the full change lifecycle
- **Technology operations** — coordinate and approve cross-team infrastructure changes with a single control plane
- **Operational risk** — full audit trail, approval evidence, and rollback records for every change, queryable by LLMs

---

## The change lifecycle

```
Discover → Understand → Plan → Approve → Execute → Observe → Rollback → Document
```

| Stage | What happens |
|-------|-------------|
| **Discover** | Connectors ingest assets, findings, and configuration state |
| **Understand** | Asset graph, open findings, and blast radius inform the change |
| **Plan** | AI-assisted planning generates steps, prechecks, and rollback path |
| **Approve** | Role-based approval gates with human review and audit evidence |
| **Execute** | Connector-native execution against real infrastructure |
| **Observe** | Post-execution verification checks confirm the intended outcome |
| **Rollback** | One-click rollback using the stored pre-change snapshot |
| **Document** | Full audit trail: who requested, who approved, what changed, what rolled back |

---

## Execution hierarchy

When executing a change, Nexplane selects the safest available mechanism for each step — preferring high-fidelity, API-native methods over lower-level remote execution:

| Tier | Mechanism | Examples | Rollback model |
|------|-----------|----------|----------------|
| **1** | Native cloud / service APIs | AWS SDK, Azure SDK, GCP SDK, Okta API | Automatic — API call reversed via stored result |
| **2** | Infrastructure-as-Code | Terraform, Ansible, Helm, Pulumi | Explicit — requires a paired destroy/revert action |
| **3** | Management frameworks | SCCM, Jamf, Intune, Kubernetes | Agent-mediated — framework handles delivery |
| **5** | Raw remote execution | SSH, WinRM | Captured state — pre-change snapshot restored on rollback |

The planning engine always selects the lowest available tier for each step given connected connectors. Tier 5 execution carries an automatic risk-score penalty and requires explicit approval acknowledgement. Every executor at every tier is required to define a rollback path — either automatic reversal, a paired undo action, or pre-change state capture for reconstitution.

---

## Key capabilities

- **Asset inventory** — 70+ connectors across cloud, identity, network, endpoints, secrets, and observability
- **Asset graph** — dependency relationships, blast radius traversal, owner lookup
- **Impact simulation** — "what breaks if I change this?" before you execute
- **AI-assisted planning** — Claude generates execution steps, prechecks, and rollback plans
- **Approval gates** — role-based routing with human review; LLMs can propose but humans approve
- **Rollback center** — every change ships with a rollback plan; execute rollback in one step
- **Infrastructure memory** — "why does this exist?" provenance tracking for every asset and change
- **Recommendation engine** — "what should I fix next?" prioritized suggestions
- **MCP / LLM access** — full change lifecycle exposed via MCP for Claude and other LLM agents
- **Full audit trail** — every change request, approval, execution, and rollback is permanently recorded

---

## MCP / LLM access

Nexplane exposes its full change lifecycle via the Model Context Protocol (MCP). Connect Claude or any MCP-compatible LLM to discover assets, plan changes, route for approval, execute, and roll back — all through the same platform that human operators use.

**Example prompts:**
- *"List all production assets and identify which have open critical findings"*
- *"Create a change request to rotate the SSH key on host web-prod-01"*
- *"What changed on the payroll server in the last 30 days and who approved each change?"*
- *"What is the rollback plan for CR abc-123?"*
- *"Roll back the DNS change from yesterday"*

MCP endpoint: `https://<your-nexplane-host>/mcp` (SSE transport, Bearer token auth)

---

## Quick start

### One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/youbetyourballs/nexplane/master/install.sh | sh
```

Or with wget:

```bash
wget -qO- https://raw.githubusercontent.com/youbetyourballs/nexplane/master/install.sh | sh
```

The script checks for Docker, Docker Compose, git, and curl/wget — installing any that are missing — then clones the repo, generates a `.env` with random credentials, and starts all services. Takes about 2 minutes on a fresh machine.

### Manual install

```bash
git clone https://github.com/youbetyourballs/nexplane
cd nexplane
cp .env.example .env          # edit as needed
docker compose up -d
```

### Access

| Service | URL |
|---------|-----|
| UI | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| MCP | http://localhost:8000/mcp |

> ⚠️ The default Docker Compose config binds to localhost only. Never expose Nexplane to the public internet — use Tailscale or a site-to-site VPN for remote access.

Default credentials (demo only): `admin@nexplane.local` / `changeme`

---

## AWS AMI deployment

Nexplane publishes a public AMI to AWS us-east-1 with each release. Launch it directly from the EC2 console — no install script needed.

**Latest AMI (v1.2.20):** `ami-041b6384d4cf78d7e` (us-east-1)

[Launch in EC2 console](https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#LaunchInstanceWizard:ami=ami-041b6384d4cf78d7e)

### Default credentials

| Field | Value |
|-------|-------|
| Email | `admin@nexplane.local` |
| Password | `changeme` |

**Change the password immediately after first login.**

### SSH access

To edit configuration or environment variables, SSH into the instance using the key pair you selected at launch:

```bash
ssh -i /path/to/your-key.pem ec2-user@<instance-public-ip>
```

The instance's public IP is shown on the EC2 console under **Instances → your instance → Public IPv4 address**. Port 22 must be open in the instance's security group (it is by default if you used the quickstart template).

> ⚠️ **Restrict SSH access appropriately.** If the instance has a public IP, limit the security group's port 22 inbound rule to known source IPs rather than `0.0.0.0/0`. If the instance is on a private (RFC 1918) address, place it in a management or otherwise restricted subnet/VLAN with limited lateral reach. The preferred approach for any environment is to avoid exposing SSH publicly altogether — use a VPN (e.g. Tailscale, WireGuard, or a site-to-site VPN) or AWS SSM Session Manager to eliminate the public attack surface entirely.

### Configuration

AMI deployments are configured via `/opt/nexplane/docker-compose.ami.yml` on the instance. Edit this file and restart services to apply changes.

```bash
sudo nano /opt/nexplane/docker-compose.ami.yml
sudo docker compose -f /opt/nexplane/docker-compose.ami.yml up -d
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_EMAIL` | `admin@nexplane.local` | Initial admin login email |
| `ADMIN_PASSWORD` | `changeme` | Initial admin password |
| `SECRET_KEY` | `nexplane-default-secret-key-change-before-production` | JWT signing key — **must change in production** |
| `DEMO_MODE` | `true` | Seeds demo org data on first boot; set to `false` to start empty |
| `CORS_ORIGINS` | `*` | Allowed CORS origins; restrict to your frontend URL in production |
| `WEBHOOK_SECRET` | `nexplane-default-webhook-secret-change-before-production` | Inbound webhook HMAC secret |

> ⚠️ The AMI binds port 80 to all interfaces. Use a security group to restrict access — never expose Nexplane directly to the public internet. Tailscale or a site-to-site VPN is the recommended access path.

---

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, @tanstack/react-query |
| Backend | FastAPI, Python 3.12, SQLAlchemy (async), Alembic |
| Database | PostgreSQL 16 |
| Agent | Go (cross-platform: Linux, macOS, Windows) |
| MCP server | FastMCP via SSE transport |
| Secrets | Pluggable: Fernet (default), AWS Secrets Manager, HashiCorp Vault |
| Auth | JWT + OIDC support |
| Deployment | Docker Compose; Helm charts for Kubernetes |

The agent uses outbound long-poll — no inbound ports required on managed hosts. This eliminates inbound attack surface on managed infrastructure and removes the need for firewall rule changes on the managed side.

---

## Contributing

Nexplane is open source under AGPL-3.0. Contributions are welcome.

- **Bug reports and feature requests** — open an issue on [GitHub](https://github.com/youbetyourballs/nexplane/issues)
- **Pull requests** — fork the repo, make your change, open a PR against `master`
- **New connectors** — see the connector catalog in `backend/app/connectors/catalog/` for the JSON schema; executors live in `backend/app/connectors/executors/`
- **Questions** — [hello@nexplane.ai](mailto:hello@nexplane.ai)

External contributors will be asked to sign a CLA before we merge.

---

## Connectors (70+)

| Category | Connectors |
|----------|-----------|
| Cloud | AWS, Azure, GCP, OCI, Cloudflare |
| Identity & Access | Active Directory, Entra ID, Okta, Google Workspace, Keycloak, LDAP |
| Security | CrowdStrike, Wiz, Tenable, Snyk, SentinelOne, OpenVAS |
| IaC & Config | Terraform, Ansible, Pulumi, Helm, CloudFormation |
| Endpoints & MDM | Jamf, Intune, SCCM |
| Observability | Datadog, Splunk, Elastic, Datadog |
| Ticketing | Jira, ServiceNow, PagerDuty |
| Source Control | GitHub, GitLab |
| Other | SSH, WinRM, HashiCorp Vault, PostgreSQL, Redis, and more |
