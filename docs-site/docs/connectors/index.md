# Connectors Overview

Connectors are the integration layer between Nexplane and target systems. Each connector type encapsulates:

- **Credential schema** — the fields required to authenticate to the system
- **Change types** — the operations the connector can perform
- **Rollback logic** — how each change type is reversed

## Available connectors

| Connector | Category | Supported actions |
|---|---|---|
| [AWS](aws.md) | Cloud | disable/enable IAM user, rotate IAM key, block S3 public access, attach/detach IAM policy |
| [GCP](gcp.md) | Cloud | disable/enable service account, manage IAM bindings |
| [Azure](azure.md) | Cloud | disable/enable Entra user, manage role assignments |
| [Kubernetes](kubernetes.md) | Orchestration | patch resources, manage RBAC |
| [Vault](vault.md) | Secrets | rotate secret |
| [LDAP](ldap.md) | Identity | disable/enable user |
| [PostgreSQL](postgresql.md) | Database | revoke/restore privileges, lock/unlock account |
| [SSH](ssh.md) | Host | allowlisted command execution, agent installation |
| [WinRM](winrm.md) | Host | allowlisted PowerShell execution |

## Adding a connector account

See [Connecting Your First Account](../getting-started/first-connector.md) for the general account setup flow. Each connector page documents the specific credential fields and minimum required permissions.

## Connector credential security

All connector credentials are encrypted at rest using AES-256 before being written to the database. The encryption key is derived from the `ENCRYPTION_KEY` environment variable. See the [Security Model](../security-model.md) for details.
