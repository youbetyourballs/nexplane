# EC2 Platform Migration Design

## Goal

Migrate the Nexplane dev platform from Windows laptop Docker Desktop to a permanent EC2 t3.medium instance in us-east-1, preserving all data (connectors, credentials, CRs, assets, secrets) with zero data loss.

## Problem Statement

Docker Desktop on Windows uses a 9P filesystem protocol to expose bind-mounted directories into containers. Background Python processes (uvicorn reload watcher, smoke test runner) get stuck in D-state (`p9_client_rpc` in wchan) reading from `/app`, causing:

- Smoke test runners hanging mid-run
- Docker daemon 500 errors requiring container restarts
- Network loss inside containers after Docker crashes
- EC2 API timeouts when network drops mid-smoke-run

Moving to native Linux Docker on EC2 eliminates the 9P layer entirely. An additional benefit: the platform and smoke test EC2 runners are now intra-VPC, so EC2 API calls, SSM commands, and runner connectivity go over AWS internal network rather than laptop → internet → AWS.

## Target Architecture

```
Your laptop (thin client)
  └─ VS Code Remote SSH ──→ EC2 t3.medium (Tailscale IP)
  └─ Browser ────────────→ EC2:3000 (frontend, via Tailscale)
                           EC2:8000 (backend API, via Tailscale)

EC2 t3.medium (us-east-1, NO public IP)
  ├─ Docker Engine (native Linux, no Desktop)
  ├─ docker-compose services:
  │   ├─ backend (FastAPI/uvicorn + tailscaled kernel TUN)
  │   ├─ frontend (React/Vite)
  │   ├─ db (Postgres 16)
  │   └─ mailhog
  └─ Tailscale (host-level, for SSH access)

Smoke test EC2 runners (us-east-1, same VPC)
  └─ Provisioned by platform via intra-VPC EC2 API calls (faster, no internet hop)
```

## Instance Spec

| Parameter | Value |
|-----------|-------|
| Instance type | t3.medium (2 vCPU, 4 GiB RAM) |
| Region | us-east-1 |
| AMI | Amazon Linux 2023 (latest) |
| EBS root volume | 30 GB gp3 |
| Public IP | **None** — Tailscale is the only ingress |
| Security group inbound | No rules (all inbound denied) |
| Security group outbound | Allow all (for Tailscale, AWS APIs, Docker pulls) |
| IAM instance profile | Same permissions as current smoke runner role (EC2, SSM, S3, IAM) |

## Data Migration Strategy

All data lives in two places:
1. **Postgres** — connectors, credentials, CRs, assets, users, everything
2. **Tailscale state volume** — backend's tailscaled auth state (re-issued, not migrated)

Migration path:
- `pg_dump` from the running Postgres container on the laptop
- Copy dump file over Tailscale SSH (laptop → EC2, no public IP needed)
- `pg_restore` into the EC2 Postgres container
- Tailscale auth key: generate a new reusable key from the tailnet admin console, inject into backend container on first start

## Configuration Changes

The main `docker-compose.yml` stays unchanged (portable, committed to git). A gitignored `.env` file and a `docker-compose.override.yml` on the EC2 instance provide EC2-specific overrides:

**`.env` (on EC2, gitignored):**
```env
SECRET_KEY=<strong-random-32-char-key>
TAILSCALE_AUTH_KEY=<reusable-key-from-tailnet-admin>
```

**`docker-compose.override.yml` (on EC2, gitignored):**
```yaml
services:
  backend:
    ports:
      - "0.0.0.0:8000:8000"
    environment:
      CORS_ORIGINS: "http://<tailscale-ip>:3000"
      SECRET_KEY: ${SECRET_KEY}
    command: >
      sh -c "tailscaled --state=/var/lib/tailscale-state/tailscaled.state &
             sleep 3 &&
             tailscale up --authkey=${TAILSCALE_AUTH_KEY} --hostname=nexplane-backend &&
             alembic upgrade head &&
             python seed.py &&
             uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/app --reload-exclude /app/tests --reload-exclude /app/alembic"
  frontend:
    ports:
      - "0.0.0.0:3000:3000"
    environment:
      VITE_API_URL: "http://<tailscale-ip>:8000"
```

Note: `<tailscale-ip>` is the EC2 instance's Tailscale IP (100.x.x.x), filled in after Tailscale joins the tailnet.

## Migration Phases

### Phase 1 — Provision EC2

1. Launch t3.medium via AWS Console or CLI:
   - AMI: Amazon Linux 2023 (64-bit x86)
   - No public IP (disable "Auto-assign public IP")
   - Security group: no inbound rules, outbound allow all
   - IAM instance profile: attach existing nexplane smoke runner role (or create equivalent with EC2/SSM/S3/IAM permissions)
   - EBS: 30 GB gp3 root volume
2. Connect via AWS SSM Session Manager (since no public IP, no SSH yet)
3. Install Tailscale on the EC2 host:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --authkey=<host-auth-key> --hostname=nexplane-dev
   ```
4. Verify EC2 appears in tailnet admin console with correct hostname
5. From laptop: `ssh ec2-user@<tailscale-ip>` — confirm SSH works over Tailscale

### Phase 2 — Install Docker

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# log out and back in for group to take effect
docker --version

# Install Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

### Phase 3 — Clone Repo and Configure

```bash
cd ~
git clone git@github.com:<org>/nexplane.git   # or use HTTPS
cd nexplane

# Create .env
cat > .env <<'EOF'
SECRET_KEY=<generate: python3 -c "import secrets; print(secrets.token_hex(32))">
TAILSCALE_AUTH_KEY=<reusable key from tailnet admin>
EOF

# Create docker-compose.override.yml with EC2 Tailscale IP filled in
# (get IP with: tailscale ip -4)
```

### Phase 4 — Build and Start Containers

```bash
# First build takes ~10-15 min (Go agent binaries, Terraform, Ansible, Python deps)
docker compose up -d --build

# Watch logs
docker compose logs -f backend
# Wait until: "Application startup complete"
```

### Phase 5 — Migrate Postgres Data

On the **laptop** (while old platform is still running):

```bash
# Dump from running Postgres container
docker exec nexplane-db-1 pg_dump -U nexplane -Fc nexplane > nexplane_dump.dump

# Copy to EC2 over Tailscale SSH
scp nexplane_dump.dump ec2-user@<tailscale-ip>:~/nexplane_dump.dump
```

On the **EC2 instance**:

```bash
# Stop backend to prevent writes during restore
docker compose stop backend

# Drop and recreate the database (seed.py already ran on first boot, need clean state)
docker exec nexplane-db-1 psql -U nexplane -c "DROP DATABASE nexplane;"
docker exec nexplane-db-1 psql -U nexplane -c "CREATE DATABASE nexplane;"

# Restore
docker exec -i nexplane-db-1 pg_restore -U nexplane -d nexplane < ~/nexplane_dump.dump

# Restart backend (alembic upgrade head will be a no-op since schema is restored)
docker compose start backend

# Verify
docker compose logs backend | grep "startup complete"
```

### Phase 6 — Verify Migration

1. Open browser: `http://<tailscale-ip>:3000`
2. Log in — confirm user accounts present
3. Navigate to Connectors — confirm all connectors and credentials present
4. Navigate to Change Requests — confirm CR history present
5. Navigate to Assets — confirm asset inventory present
6. Trigger a simple smoke test (e.g., AWS connector health check) — confirm intra-VPC connectivity works

### Phase 7 — Update Laptop Workflow

VS Code: install "Remote - SSH" extension, add host to `~/.ssh/config`:

```
Host nexplane-dev
    HostName <tailscale-ip>
    User ec2-user
    IdentityFile ~/.ssh/id_ed25519
```

Open VS Code → Remote Explorer → Connect to `nexplane-dev` → open `/home/ec2-user/nexplane`.

### Phase 8 — Decommission Laptop Docker

Once EC2 is verified:

```bash
# On laptop — stop all containers
docker compose down

# Optional: reclaim disk space
docker system prune -a --volumes
```

## Security Considerations

- **No public IP on EC2**: enforced at launch; security group has zero inbound rules. Tailscale is the sole ingress path.
- **SSH over Tailscale only**: no port 22 open to internet. Tailscale ACLs control who can reach the instance.
- **Secrets in `.env`**: gitignored, never committed. `SECRET_KEY` rotated to a real random value.
- **Tailscale auth key**: reusable key for the backend's internal tailscaled; stored only in `.env` on EC2.
- **IAM instance profile**: principle of least privilege — same scoped role used for smoke runners.

## Rollback Plan

If EC2 migration has issues:
1. Laptop Docker is still intact (we `docker compose down`, not `docker compose down -v`)
2. `docker compose up -d` on laptop restores the old platform
3. Data loss risk: zero — laptop DB is the source of truth until EC2 is verified and laptop is decommissioned

## What Doesn't Change

- `docker-compose.yml` (committed, unchanged)
- All backend code, smoke tests, executors
- Smoke test runner provisioning (`run_on_ec2.py`) — runners still launch fresh EC2 instances; they're just orchestrated from EC2 now instead of laptop
- Tailscale network topology for smoke runners
- AMI cache pattern for slow smoke infra
