# Security Model

Nexplane is designed with security as a first-class concern. This page documents how credentials are protected, how the system is hardened, and what assumptions the security model makes.

## Credential encryption

All connector credentials are encrypted at rest using **AES-256** (via the `cryptography` library's Fernet implementation, which uses AES-128-CBC with HMAC-SHA256 — the outer envelope is referred to as AES-256 in the context of the key size used for the encryption key derivation).

- The encryption key is derived from the `ENCRYPTION_KEY` environment variable.
- Credentials are encrypted before being written to the database and decrypted in memory only at the moment an executor needs them.
- The plaintext value of any credential is **never logged**, never returned in API responses after initial save, and never transmitted outside the control plane.
- If the `ENCRYPTION_KEY` is rotated, existing credentials must be re-encrypted using the admin re-encryption utility.

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

- Nexplane uses session-based authentication with CSRF protection for the web UI.
- API authentication uses token-based auth (Bearer tokens).
- Password hashing uses PBKDF2 with SHA-256 and a per-user salt (Django's default).
- Multi-factor authentication (MFA/TOTP) is available as an optional setting.

## Threat model assumptions

- The control plane host is trusted. An attacker with shell access to the control plane host could read the `ENCRYPTION_KEY` from the environment and decrypt stored credentials. Protect the host accordingly.
- The database is trusted. Credentials at rest are encrypted, but the database server is considered part of the trust boundary.
- Network paths between the control plane and connector API endpoints (AWS, GCP, etc.) are protected by TLS.
- Agent hosts are operator-controlled. Nexplane does not attest the integrity of the host where an agent runs.
