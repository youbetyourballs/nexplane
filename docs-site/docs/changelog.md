# CHANGELOG

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
