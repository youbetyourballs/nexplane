# Nexplane — The control plane for infrastructure change

Eliminate uncertainty before every infrastructure change.

---

## The four questions Nexplane answers

| Question | How Nexplane helps |
|----------|--------------------|
| **What do I have?** | Asset inventory across 70+ connectors — cloud, identity, network, endpoints, secrets |
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

## Key capabilities

- **Asset inventory** — 70+ connectors across cloud, identity, network, endpoints, secrets, and observability
- **Asset graph** — dependency relationships, blast radius traversal, owner lookup *(coming soon)*
- **Impact simulation** — "what breaks if I change this?" before you execute *(coming soon)*
- **AI-assisted planning** — Claude generates execution steps, prechecks, and rollback plans
- **Approval gates** — role-based routing with human review; LLMs can propose but humans approve
- **Rollback center** — every change ships with a rollback plan; execute rollback in one step
- **Infrastructure memory** — "why does this exist?" provenance tracking for every asset and change *(coming soon)*
- **Recommendation engine** — "what should I fix next?" prioritized suggestions *(coming soon)*
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

```bash
# Clone and start
git clone https://github.com/youbetyourballs/nexplane
cd nexplane
cp .env.example .env          # edit as needed
docker compose up -d

# Access
# UI:      http://localhost:3000
# API:     http://localhost:8000
# Docs:    http://localhost:8000/docs
# MCP:     http://localhost:8000/mcp
```

Default credentials (demo only): `admin@acme.example` / `admin123`

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

The agent uses outbound long-poll — no inbound ports required on managed hosts.

---

## Connectors (70+)

Cloud: AWS, Azure, GCP, OCI, Cloudflare · Identity: Active Directory, Entra ID, Okta, Google Workspace, Keycloak, LDAP · Security: CrowdStrike, Wiz, Tenable, Snyk, SentinelOne · IaC: Terraform, Ansible, Pulumi, Helm · Endpoints: Jamf, Intune, SCCM · Observability: Datadog, Splunk, Elastic · Ticketing: Jira, ServiceNow, PagerDuty · Source control: GitHub, GitLab · And more.
