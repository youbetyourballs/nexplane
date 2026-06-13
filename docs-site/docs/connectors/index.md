# Connectors Overview

Connectors are the integration layer between Nexplane and target systems. Each connector type encapsulates:

- **Credential schema** — the fields required to authenticate to the system
- **Change types** — the operations the connector can perform
- **Rollback logic** — how each change type is reversed

Nexplane ships **70+ connectors**. The pages below document the most common ones in depth; the full catalog by category follows.

## Documented connectors

| Connector | Category | Supported actions |
|---|---|---|
| [AWS](aws.md) | Cloud | EC2 lifecycle, IAM users/policies/keys, S3, Route53, RDS, CloudWatch, ALB, security groups, SSM, agent deploy |
| [GCP](gcp.md) | Cloud | Compute lifecycle, firewall rules, storage, service accounts, IAM bindings |
| [Azure](azure.md) | Cloud | VM lifecycle, NSG rules, storage, managed identities, RBAC, Entra users |
| [Kubernetes](kubernetes.md) | Orchestration | restart/scale deployments, network policy, RBAC, secret rotation, Helm |
| [Vault](vault.md) | Secrets | rotate KV secret, revoke token, dynamic credentials, update policy |
| [LDAP](ldap.md) | Identity | disable/enable user |
| [PostgreSQL](postgresql.md) | Database | provision/deprovision users, grant/revoke privileges, audit config, lock account |
| [SSH](ssh.md) | Host | allowlisted command execution, agent installation |
| [WinRM](winrm.md) | Host | allowlisted PowerShell execution |

## Full catalog by category

**Cloud & Infrastructure** — AWS · Azure · GCP · OCI · Cloudflare · Palo Alto · OPNsense · Zscaler · Tailscale

**Identity & Access** — Okta · Microsoft Entra ID · Azure AD (Graph) · Active Directory · LDAP · FreeIPA · Keycloak · Teleport · HashiCorp Vault · Infisical · Microsoft LAPS

**Code & Version Control** — GitHub · GitLab · Gitea · JFrog Xray

**Security Tools / EDR** — CrowdStrike · Microsoft Defender for Endpoint · SentinelOne · Wazuh · Falco

**Vulnerability Scanners** — Tenable · Snyk · Qualys · OpenVAS · Nessus · Wiz · RunZero · Elastic Security

**SaaS** — Google Workspace · Slack · Kubernetes · Helm

**Databases** — PostgreSQL · Redis · MongoDB

**Windows Management** — Microsoft Intune · SCCM / MECM · Windows Update for Business · WinRM

**macOS / MDM & Binary Authorization** — Jamf · MicroMDM · Santa Sync Server

**Network, Firewall & Certificates** — Cloudflare · Palo Alto · OPNsense · Tailscale · step-ca · BIND DNS

**IaC & Configuration** — Terraform (local/remote) · Ansible (AWX/local/remote) · SaltStack · AWS CloudFormation · Azure Bicep · Pulumi · Checkov · Chef InSpec

**Workflow & Observability** — Jira · PagerDuty · ServiceNow · Splunk · Datadog · SMTP

**Host Execution** — SSH · Nexplane Agent

## Adding a connector account

See [Connecting Your First Account](../getting-started/first-connector.md) for the general account setup flow. Each connector page documents the specific credential fields and minimum required permissions.

## Connector credential security

All connector credentials are encrypted at rest using Fernet (AES-256) via the `SecretsService` before being written to the database. The encryption key is derived from the `SECRET_KEY` environment variable. See the [Security Model](../security-model.md) for details.
