# Design: nexplane-docs Full Rewrite

**Date:** 2026-05-16  
**Status:** Approved  
**Repo:** https://github.com/youbetyourballs/nexplane-docs

---

## Problem

The nexplane-docs MkDocs site (hosted at docs.nexplane.ai via Netlify) was scaffolded with placeholder content that is significantly inaccurate:

- Claims 14 connectors; actual count is 38+
- Lists a Redis container that doesn't exist
- Describes agent communication as mutual TLS; it's long-poll HTTP
- Nav includes placeholder connector pages (OCI, LDAP, Keycloak, WinRM, PostgreSQL, Redis, MongoDB) — these are planned, not yet built
- Missing major feature sections: Projects, Composable Runbooks, Incident Response, Vulnerability Remediation, Fleet Operations, Identity Lifecycle, Compliance & Governance, IaC Orchestration, Secret Rotation, IP Migration
- AWS connector page documents ~10 actions; real count is 40+
- Change types pages missing most of the 48+ types

## Goal

Rewrite all existing pages and write all missing pages so the docs site accurately reflects the actual Nexplane product. The `README.md` in the nexplane repo is the canonical source of truth for all capabilities, counts, and technical details.

---

## Navigation Structure (mkdocs.yml)

```yaml
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
    - Nexplane Agent: connectors/nexplane-agent.md
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
```

---

## Content Plan by Section

### Home (index.md)
Full rewrite. Accurate tagline ("The control plane for security execution"), correct connector count (38+), accurate architecture summary (no Redis, correct agent model), links to Getting Started.

### Getting Started (4 pages)
- **installation.md** — rewrite: remove Redis container, correct to 3 containers (backend, frontend, db), add demo credentials table, correct SECRET_KEY guidance
- **connect-cloud.md** — rewrite: walk through adding a connector with credentials, test connection
- **first-change-request.md** — rewrite: create a real CR (e.g., security_group_update), approve, execute, verify
- **deploy-agent.md** — rewrite: generate secret in Settings, copy install one-liner from Settings panel, verify agent appears as Asset

### Core Concepts (5 pages — new section)
New pages explaining:
- **change-requests.md** — lifecycle (Draft→Completed), safety engine, approval tiers, rollback
- **projects.md** — grouping CRs, dependency graph, AI planning assistant
- **asset-inventory.md** — asset types, connector ingest, tagging
- **connectors.md** — what connectors are, credential storage, ingest vs change actions
- **agent.md** — outbound poll model, HMAC auth, self-update, no inbound SSH

### Features (9 pages — all new)
Each page written from the corresponding README section:
- **runbooks.md** — step types, failure handling, versioning, seed templates
- **incident-response.md** — 4 playbook types, expedited approval, forensic bundles
- **vulnerability-remediation.md** — webhook ingest, asset matching, auto-CRs, CVE blast-radius, SLA tiers, auto-escalation
- **fleet-operations.md** — rolling restart, canary config push, bulk file distribution, fleet health check, maintenance windows
- **identity-lifecycle.md** — offboarding kill switch, onboarding, access reviews
- **compliance.md** — CIS benchmark campaigns, drift detection, change freeze, audit evidence
- **iac-orchestration.md** — Terraform local/remote, Ansible local/remote, Helm; plan diff UI
- **credential-rotation.md** — DB credentials, SSH keys, API keys, service accounts, in-memory propagation
- **ip-migration.md** — 4 methods, dead man's switch, multi-host campaigns, DNS coordination, IP Migration Wizard UI

### Change Types (10 pages)
- **index.md** — full table of all 48+ change types by category
- One page per category with descriptions and rollback notes

### Connectors (40 pages)
**Real connectors** (~34 pages): Full pages covering capabilities, credentials required, permissions, change actions, and ingest. AWS gets the most detail (40+ actions). Azure, GCP get similarly detailed pages. Smaller connectors (Cloudflare, Palo Alto, etc.) get focused pages.

**Placeholder connectors** (~7 pages — OCI, LDAP, Keycloak, WinRM, PostgreSQL, Redis, MongoDB): Stub pages with connector name, brief planned scope sentence, and "Coming soon" note.

### Agent (5 pages)
- **index.md** — overview, outbound poll model, binary distribution
- **installation.md** — one-liner, systemd unit, Windows service, flag/env reference
- **commands.md** — all 15 command packages with command lists and platform support table
- **self-update.md** — S3 version check, SHA256 verify, atomic replace
- **windows.md** — Windows-specific: winpatch, winharden, PowerShell service setup

### Security (4 pages)
- **model.md** — JWT auth, HMAC agent tokens, Fernet AES-256 secrets
- **credentials.md** — SecretsService, versioning, Vault/HSM swap-out
- **safety-engine.md** — full safety table from README (prod+critical asset blocking, remote command templates, change freeze, etc.)
- **network-exposure.md** — backend bound to 127.0.0.1, agent outbound-only, S3 for binary distribution

### API Reference (1 page)
Overview of endpoint groups with link to `http://localhost:8000/docs` for interactive docs. List all router groups from README.

---

## Implementation Approach

1. Clone `youbetyourballs/nexplane-docs` locally
2. Rewrite `mkdocs.yml` with the new nav
3. Write all pages in the order: index → getting-started → concepts → features → change-types → connectors → agent → security → api
4. Commit and push to main
5. Netlify auto-deploys

**Source of truth:** `README.md` in the nexplane repo. All capabilities, counts, change type lists, agent command tables, and connector capability tables come directly from there.

**Tone:** Technical, direct, no marketing filler. Same style as the README.

---

## Files Touched

| Action | Count |
|--------|-------|
| Rewrite existing | ~15 |
| New pages | ~35 |
| Stub (placeholder connectors) | 7 |
| **Total** | **~57** |
