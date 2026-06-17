# Quick Start (Docker Compose)

This guide gets a fully functional Nexplane instance running on your local machine in under five minutes.

## Prerequisites

- Docker 24+ and Docker Compose v2
- Ports 3000 and 8000 available on localhost
- 2 GB free RAM

## Steps

### 1. Clone the repository

```bash
git clone https://github.com/youbetyourballs/nexplane.git
cd nexplane
```

### 2. Configure environment

Copy the example environment file and review the defaults. For a local evaluation you generally do not need to change anything.

```bash
cp .env.example .env
```

Key variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | dev key | JWT signing + Fernet credential-encryption key derivation — **change in production** (≥32 chars) |
| `DATABASE_URL` | internal Postgres | PostgreSQL connection string |
| `AI_MODEL` | `claude-sonnet-4-6` | AI model used by the planning assistant (Anthropic or OpenAI) |
| `WEBHOOK_SECRET` | dev key | HMAC key for vulnerability-scanner webhook verification |
| `INSTANCE_URL` | `http://localhost:8000` | Public instance URL used for OIDC redirect URIs and email |

### 3. Start the stack

```bash
docker compose up -d
```

This starts:

- **backend** — FastAPI control plane API on `http://localhost:8000` (bound to localhost — not exposed publicly)
- **frontend** — React UI on `http://localhost:3000`
- **db** — PostgreSQL

Wait about 15–20 seconds for migrations to complete. You can follow logs with:

```bash
docker compose logs -f backend
```

Look for `Application startup complete` before proceeding.

### 4. Log in

Open `http://localhost:3000` in your browser. The default stack seeds demo accounts you can log in with immediately:

| Email | Password | Role |
|-------|----------|------|
| admin@acme.example | admin123 | Admin |
| operator@acme.example | operator123 | Security Operator |
| approver@acme.example | approver123 | Approver |
| auditor@acme.example | auditor123 | Auditor |

!!! warning "Production"
    Replace the demo accounts with real users and set `SECRET_KEY` to a stable random value before exposing the instance — the auto-generated key changes on restart and invalidates all sessions.

### 5. Verify the installation

Interactive API docs are available at `http://localhost:8000/docs`.

## Next steps

- [Connect your first account](first-connector.md) — add AWS, Vault, LDAP, or another connector
- [Create your first change request](first-change-request.md)
