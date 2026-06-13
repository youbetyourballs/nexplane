# CHANGELOG

## 0.1.2 — Current

- **Authentication & SSO** — OIDC single sign-on via the authorization-code flow, with per-organization auth modes (`local` or `idp`) and optional auto-provisioning
- **Security Policy Auto-Generation** — observe → synthesize → diff → accept soak sessions that generate least-privilege seccomp/AppArmor/SELinux/eBPF policies from real host behavior
- **macOS agent (Apple Silicon)** — 23 macOS change types covering FileVault, Gatekeeper, Santa binary authorization, software updates, configuration profiles, CIS audit, and fleet ops
- **Production deployment** — `docker-compose.prod.yml` with an nginx TLS reverse proxy and a built frontend, plus a Helm chart for Kubernetes

## 0.1.1

- **Connector expansion to 70+** — cloud (OCI, Cloudflare, Palo Alto, OPNsense, Zscaler), identity (Okta, Entra ID, FreeIPA, Keycloak, Teleport, Infisical, LAPS), EDR (CrowdStrike, Defender, SentinelOne, Wazuh, Falco), vulnerability scanners (Tenable, Snyk, Qualys, Nessus, Wiz, RunZero, Elastic), IaC (Terraform, Ansible, SaltStack, CloudFormation, Bicep, Pulumi, Checkov, Chef InSpec), databases (Redis, MongoDB), Windows management (Intune, SCCM, WUfB, WinRM), and workflow/observability (Jira, PagerDuty, ServiceNow, Splunk, Datadog)
- **Projects & composable runbooks** — sequenced, dependency-linked initiatives and reusable multi-step workflows with conditional branching and human checkpoints
- **Incident response playbooks**, **vulnerability remediation pipeline** (SLA tiers, CVE blast-radius), **identity lifecycle**, **fleet operations**, **compliance & governance**, **IaC orchestration**, **backup & recovery**, and **IP migration**
- **Expanded agent command packages** — patching, hardening, IaC, fleet, forensics, compliance, database admin, eBPF, app discovery, and legacy-workload containerization

## 0.1.0 (2026-05-16) — Initial release

First public release of the Nexplane security change management platform.

- Control plane with backend API, React frontend, and PostgreSQL storage
- Change request lifecycle: draft → pending approval → approved → executing → completed
- Rollback support for all change types via before-state snapshot
- AI assistant for natural language to change type translation
- Connectors: AWS, GCP, Azure, Kubernetes, HashiCorp Vault, LDAP, PostgreSQL, SSH, WinRM
- AES-256 encryption for connector credentials at rest
- Agent support for host-level operations over outbound HTTPS
- Full audit log with before/after state for every executed change
- Docker Compose deployment for single-node installation
