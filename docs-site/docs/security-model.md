# Security Model

Nexplane is designed with security as a first-class concern. This page documents how credentials are protected, how the system is hardened, and what assumptions the security model makes.

## Credential encryption

All connector credentials are encrypted at rest using **Fernet (AES-256)** via the `SecretsService`.

- The encryption key is derived from the `SECRET_KEY` environment variable (the same key also underpins JWT signing).
- Credentials are encrypted before being written to the database and decrypted in memory only at the moment an executor needs them.
- The plaintext value of any credential is **never logged**, never returned in API responses after initial save, and never transmitted outside the control plane.
- Credentials produced mid-execution (new passwords, rotated keys) travel in process memory only between steps — never written to logs, the database, or temp files.

## Transport security

- All communication between the frontend and backend uses HTTPS in production. Configure TLS termination at your reverse proxy (nginx, Caddy, AWS ALB, etc.).
- Agent-to-control-plane communication is **outbound HTTPS** from the agent. The agent initiates the connection; the control plane never connects inbound to agents.
- Connector API calls (to AWS, GCP, Vault, etc.) use the target system's standard HTTPS endpoints.

## Principle of least privilege

Nexplane encourages operators to provision connector service accounts with the minimum permissions required for the specific change types they intend to use. Each connector page documents the minimum required permissions. Nexplane does not require or request administrative access to target systems.

## Allowlisted command execution

For SSH and WinRM connectors, Nexplane never executes arbitrary commands. All operations are defined in a server-side allowlist. Commands are rendered from parameterized templates and validated before execution. This prevents command injection even if an attacker were able to influence parameter values.

## Audit log

Every action in Nexplane is logged with:

- **Who** — the authenticated user who performed the action
- **What** — the change type, parameters, and target account
- **When** — UTC timestamp
- **Result** — success or failure with error details
- **Before state** — snapshot of the resource prior to modification (stored with the CR for rollback)

Audit log entries are append-only. Existing records cannot be modified or deleted through the application layer.

## Authentication

- **Local auth** — JWT + bcrypt. Users log in via `POST /auth/login` and receive a Bearer JWT required on every API call. This is the default for every organization.
- **OIDC single sign-on** — login can be delegated to an external identity provider via the authorization-code flow. Auth mode is switched per organization (`local` or `idp`) by an admin. See the [Authentication & SSO](https://docs.nexplane.ai/security/authentication/) reference for the full flow and user-provisioning rules.
- **Agent authentication** — each job payload dispatched to an agent is signed with an HMAC-SHA256 shared secret; the agent verifies the signature before executing and rejects unsigned or invalid jobs.
- **Role-based access control** — Admin, Security Operator, Approver, and Auditor roles, plus an `ir_responder` role that can bypass change-freeze windows during an active incident.

## Threat model assumptions

- The control plane host is trusted. An attacker with shell access to the control plane host could read the `SECRET_KEY` from the environment and decrypt stored credentials. Protect the host accordingly.
- The database is trusted. Credentials at rest are encrypted, but the database server is considered part of the trust boundary.
- Network paths between the control plane and connector API endpoints (AWS, GCP, etc.) are protected by TLS.
- Agent hosts are operator-controlled. Nexplane does not attest the integrity of the host where an agent runs.
