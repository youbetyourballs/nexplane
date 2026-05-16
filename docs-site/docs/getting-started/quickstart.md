# Quick Start (Docker Compose)

This guide gets a fully functional Nexplane instance running on your local machine in under five minutes.

## Prerequisites

- Docker 24+ and Docker Compose v2
- Ports 3000 and 8000 available on localhost
- 2 GB free RAM

## Steps

### 1. Clone the repository

```bash
git clone https://github.com/nexplane/nexplane.git
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
| `SECRET_KEY` | generated | Django secret key — change in production |
| `DATABASE_URL` | sqlite:///db.sqlite3 | Use a Postgres URL for production |
| `ENCRYPTION_KEY` | generated | AES-256 key used to encrypt connector credentials |

### 3. Start the stack

```bash
docker compose up -d
```

This starts:

- **backend** — FastAPI/Django API on `http://localhost:8000`
- **frontend** — React UI on `http://localhost:3000`
- **db** — PostgreSQL (if configured) or uses SQLite by default

Wait about 15–20 seconds for migrations to complete. You can follow logs with:

```bash
docker compose logs -f backend
```

Look for `Application startup complete` before proceeding.

### 4. Create the admin account

Open `http://localhost:3000` in your browser. On first load you will be prompted to create an administrator account. Enter a username, email address, and password.

!!! note
    This admin account has full access to all connectors and change types. In production, create individual user accounts and assign roles.

### 5. Verify the installation

Navigate to **Settings → System** in the UI. You should see green status indicators for the API, database, and encryption service.

## Next steps

- [Connect your first account](first-connector.md) — add AWS, Vault, LDAP, or another connector
- [Create your first change request](first-change-request.md)
