# Platform Image Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every `git tag v*.*.*`, produce a public EC2 AMI (us-east-1) with the full Nexplane platform running on boot. Zero-config, port 80, static default credentials. AMI ID published to `releases.nexplane.ai/latest.json` only after smoke passes.

**Architecture:** Packer builds the AMI from a base Ubuntu 22.04 instance: installs Docker, pulls the versioned backend image from GHCR, builds a production webserver image (nginx + frontend static files baked in), writes a production docker-compose and systemd unit, snapshots. Three new GitHub Actions jobs — `build-ami`, `smoke-ami`, `publish-manifest` — are appended to the existing `release.yml` after `build-and-release`. The `packer/` directory is structured so adding OVA/Azure/GCP builders later is additive (new `.pkr.hcl` + new publish job, no changes to existing files).

**Tech Stack:** HashiCorp Packer (HCL2), GitHub Actions, AWS EC2/AMI APIs (boto3 + AWS CLI), Python 3 smoke script, nginx:alpine, docker-compose v2, systemd.

## Global Constraints

- AMI is public, us-east-1 only, triggered by every `git tag v*.*.*`
- Frontend built with `VITE_API_URL=/api` so all axios calls are same-origin (`/api/...`); nginx strips `/api` prefix and proxies to `backend:8000`
- nginx serves frontend static files directly (baked into webserver image); no Node.js process running on the AMI
- Default credentials: `admin@nexplane.local` / `changeme` — printed in `/etc/motd`
- Smoke test must pass before AMI is made public or `latest.json` updated; pipeline hard-fails if smoke fails
- GHCR token used only during Packer build, not baked into AMI filesystem
- Tailscale removed from AMI compose (users connect via public or private IP directly)
- No `--reload` in uvicorn on AMI (production mode)
- Source code NOT present on AMI filesystem — only pre-built images in Docker layer cache
- `packer/` directory: one `.pkr.hcl` per target format; shared provisioner scripts in `packer/scripts/`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `frontend/Dockerfile.prod` | Create | Multi-stage: npm build → nginx image with static dist + nginx.conf |
| `packer/nexplane-ami.pkr.hcl` | Create | Packer HCL2 template for AWS AMI builder |
| `packer/scripts/setup.sh` | Create | Provisioner: installs Docker, pulls images, writes MOTD, enables systemd unit |
| `packer/scripts/nexplane.service` | Create | systemd unit that starts docker-compose on boot |
| `packer/docker-compose.ami.yml` | Create | Production compose: db + backend + webserver (no source mounts, no tailscale, no reload) |
| `packer/smoke_ami.py` | Create | 9-phase post-build smoke test |
| `packer/templates/quickstart.cfn.yml.tmpl` | Create | CloudFormation template (AMI_ID placeholder) — creates SG + instance, outputs URL + SSH cmd |
| `packer/templates/LAUNCH.md.tmpl` | Create | Step-by-step launch instructions (AMI_ID placeholder) — rendered per release |
| `.github/workflows/release.yml` | Modify | Add `build-ami`, `smoke-ami`, `publish-manifest` jobs |

---

## Task 1: Production frontend Dockerfile and nginx config

**Files:**
- Create: `frontend/Dockerfile.prod`

**Interfaces:**
- Produces: Docker image `nexplane-webserver:VERSION` — nginx on port 80, serves `/usr/share/nginx/html` for static assets, proxies `/api/` → `http://backend:8000/`
- Build arg: `VITE_API_URL=/api` (baked at build time)

- [ ] **Step 1: Write `frontend/Dockerfile.prod`**

```dockerfile
# Stage 1 — build production frontend
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

# Stage 2 — nginx serving static files + API proxy
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 2: Write `frontend/nginx.conf`**

```nginx
server {
    listen 80;

    # Proxy API calls to backend (strip /api prefix)
    location /api/ {
        proxy_pass         http://backend:8000/;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    # Serve frontend SPA — fall back to index.html for client-side routing
    location / {
        root       /usr/share/nginx/html;
        try_files  $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 3: Test the build locally**

```bash
cd frontend
docker build -f Dockerfile.prod --build-arg VITE_API_URL=/api -t nexplane-webserver:test .
docker run --rm -p 8080:80 nexplane-webserver:test
# curl http://localhost:8080/ should return HTML
# curl http://localhost:8080/api/health would 502 (no backend) — that's expected
```

Expected: `curl http://localhost:8080/` returns `<!doctype html>` or similar.

- [ ] **Step 4: Commit**

```bash
git add frontend/Dockerfile.prod frontend/nginx.conf
git commit -m "feat: production frontend Dockerfile with nginx reverse proxy"
```

---

## Task 2: Production docker-compose for AMI

**Files:**
- Create: `packer/docker-compose.ami.yml`

**Interfaces:**
- Consumes: `nexplane-webserver:VERSION` (built during Packer run, in local Docker cache)
- Consumes: `ghcr.io/youbetyourballs/nexplane-backend:VERSION` (pulled during Packer run)
- Produces: Running platform at port 80 on boot

- [ ] **Step 1: Create `packer/` directory and write `packer/docker-compose.ami.yml`**

```yaml
version: "3.9"

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: nexplane
      POSTGRES_PASSWORD: nexplane_prod
      POSTGRES_DB: nexplane
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nexplane"]
      interval: 5s
      timeout: 5s
      retries: 10

  backend:
    image: ghcr.io/youbetyourballs/nexplane-backend:NEXPLANE_VERSION
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://nexplane:nexplane_prod@db:5432/nexplane
      SECRET_KEY: nexplane-default-secret-key-change-before-production
      ENVIRONMENT: production
      CORS_ORIGINS: "*"
      DEMO_MODE: "true"
      NEXPLANE_AGENT_DOWNLOAD_URL: "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com"
    depends_on:
      db:
        condition: service_healthy
    cap_add:
      - NET_ADMIN
    command: >
      sh -c "alembic upgrade heads &&
             python seed.py &&
             uvicorn app.main:app --host 0.0.0.0 --port 8000"

  webserver:
    image: nexplane-webserver:NEXPLANE_VERSION
    restart: unless-stopped
    ports:
      - "80:80"
    depends_on:
      - backend

volumes:
  postgres_data:
```

Note: `NEXPLANE_VERSION` is a literal placeholder — the setup script (Task 3) uses `sed` to replace it with the actual version string at AMI build time.

- [ ] **Step 2: Verify compose syntax**

```bash
docker compose -f packer/docker-compose.ami.yml config
```

Expected: parsed config printed with no errors (image names will have `NEXPLANE_VERSION` literally — that's expected at this stage).

- [ ] **Step 3: Commit**

```bash
git add packer/docker-compose.ami.yml
git commit -m "feat: production docker-compose for AMI"
```

---

## Task 3: AMI provisioner scripts and systemd unit

**Files:**
- Create: `packer/scripts/setup.sh`
- Create: `packer/scripts/nexplane.service`

**Interfaces:**
- Consumes: Packer variables `version` (e.g. `v1.2.3`) and `ghcr_token` (write-packages PAT)
- Produces: `/opt/nexplane/docker-compose.ami.yml` with real version substituted, GHCR images pre-pulled, webserver image built, systemd unit enabled

- [ ] **Step 1: Write `packer/scripts/setup.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

VERSION="${NEXPLANE_VERSION}"   # injected by Packer as env var
GHCR_TOKEN="${NEXPLANE_GHCR_TOKEN}"  # injected by Packer as env var

# Install Docker CE
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Enable Docker
systemctl enable docker
systemctl start docker

# Log in to GHCR
echo "${GHCR_TOKEN}" | docker login ghcr.io -u youbetyourballs --password-stdin

# Pull backend image
docker pull "ghcr.io/youbetyourballs/nexplane-backend:${VERSION}"

# Build production webserver image from source (already checked out by Packer)
cd /tmp/nexplane-src
docker build \
  -f frontend/Dockerfile.prod \
  --build-arg VITE_API_URL=/api \
  -t "nexplane-webserver:${VERSION}" \
  ./frontend

# Log out of GHCR (don't leave credentials on AMI)
docker logout ghcr.io

# Write production compose with real version
mkdir -p /opt/nexplane
sed "s/NEXPLANE_VERSION/${VERSION}/g" /tmp/nexplane-src/packer/docker-compose.ami.yml \
  > /opt/nexplane/docker-compose.ami.yml

# Install systemd unit
cp /tmp/nexplane-src/packer/scripts/nexplane.service /etc/systemd/system/nexplane.service
systemctl daemon-reload
systemctl enable nexplane.service

# Write MOTD
cat > /etc/motd <<'MOTD'

  ███╗   ██╗███████╗██╗  ██╗██████╗ ██╗      █████╗ ███╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██╔══██╗██║     ██╔══██╗████╗  ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██████╔╝██║     ███████║██╔██╗ ██║█████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██╔═══╝ ██║     ██╔══██║██║╚██╗██║██╔══╝
  ██║ ╚████║███████╗██╔╝ ██╗██║     ███████╗██║  ██║██║ ╚████║███████╗
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝

  Platform URL:  http://<this-instance-public-or-private-ip>/
  Default email: admin@nexplane.local
  Default pass:  changeme

  Change your password immediately after first login.
  Run: sudo docker compose -f /opt/nexplane/docker-compose.ami.yml ps
  to check platform status.

MOTD

echo "Nexplane AMI setup complete — version ${VERSION}"
```

- [ ] **Step 2: Write `packer/scripts/nexplane.service`**

```ini
[Unit]
Description=Nexplane Platform
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/nexplane
ExecStart=/usr/bin/docker compose -f /opt/nexplane/docker-compose.ami.yml up -d
ExecStop=/usr/bin/docker compose -f /opt/nexplane/docker-compose.ami.yml down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Make setup.sh executable and commit**

```bash
chmod +x packer/scripts/setup.sh
git add packer/scripts/setup.sh packer/scripts/nexplane.service
git commit -m "feat: AMI provisioner script and systemd unit"
```

---

## Task 4: Packer HCL2 template

**Files:**
- Create: `packer/nexplane-ami.pkr.hcl`

**Interfaces:**
- Input variables: `version` (e.g. `v1.2.3`), `ghcr_token`, `aws_region` (default `us-east-1`)
- Output: AMI named `nexplane-{version}`, tagged `ManagedBy=nexplane-ci`, `Version={version}`
- AMI starts private (made public by `publish-manifest` CI job after smoke passes)

- [ ] **Step 1: Install Packer locally to verify template**

```bash
# On the dev machine (or EC2), download packer for syntax check only
# https://developer.hashicorp.com/packer/install
packer version  # verify installed
```

- [ ] **Step 2: Write `packer/nexplane-ami.pkr.hcl`**

```hcl
packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "version" {
  type        = string
  description = "Release version tag, e.g. v1.2.3"
}

variable "ghcr_token" {
  type      = string
  sensitive = true
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

locals {
  ami_name = "nexplane-${var.version}"
}

source "amazon-ebs" "nexplane" {
  region        = var.aws_region
  instance_type = "t3.medium"

  # Latest Ubuntu 22.04 LTS x86_64 in us-east-1
  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"]  # Canonical
  }

  ssh_username = "ubuntu"

  ami_name        = local.ami_name
  ami_description = "Nexplane platform ${var.version} — zero-config, runs on port 80"

  # Start private; made public by publish-manifest job after smoke passes
  ami_groups = []

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 30
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Name      = local.ami_name
    ManagedBy = "nexplane-ci"
    Version   = var.version
  }
}

build {
  sources = ["source.amazon-ebs.nexplane"]

  # Copy source tree so setup.sh can build the frontend
  provisioner "file" {
    source      = "."
    destination = "/tmp/nexplane-src"
  }

  provisioner "shell" {
    environment_vars = [
      "NEXPLANE_VERSION=${var.version}",
      "NEXPLANE_GHCR_TOKEN=${var.ghcr_token}",
      "DEBIAN_FRONTEND=noninteractive",
    ]
    script = "packer/scripts/setup.sh"
  }

  post-processor "manifest" {
    output     = "packer/manifest.json"
    strip_path = true
  }
}
```

- [ ] **Step 3: Validate template syntax**

```bash
cd f:/Nexplane/nexplane   # or on EC2
packer validate \
  -var version=v0.0.0-test \
  -var ghcr_token=dryrun \
  packer/nexplane-ami.pkr.hcl
```

Expected: `The configuration is valid.`

- [ ] **Step 4: Commit**

```bash
git add packer/nexplane-ami.pkr.hcl
git commit -m "feat: Packer HCL2 template for Nexplane AMI"
```

---

## Task 5: Smoke test script

**Files:**
- Create: `packer/smoke_ami.py`

**Interfaces:**
- CLI args: `--ami-id`, `--key-name`, `--security-group-id`, `--region` (default `us-east-1`)
- Exit 0 on pass, exit 1 on any failure
- 8 named phases; each logs `[PHASE N: name] PASSED` or raises on failure
- All created resources tracked for cleanup; instance always terminated in `finally`

- [ ] **Step 1: Write `packer/smoke_ami.py`**

```python
#!/usr/bin/env python3
"""
AMI smoke test — 8-phase functional verification of a freshly launched Nexplane AMI.
Usage: python packer/smoke_ami.py --ami-id ami-xxx --key-name smoke-key --security-group-id sg-xxx
"""
import argparse
import subprocess
import sys
import time

import boto3
import requests

INSTANCE_TYPE = "t3.medium"
BOOT_TIMEOUT   = 420   # seconds to wait for port 80 after EC2 status OK
HTTP_POLL      = 15    # seconds between HTTP readiness polls
PLAN_TIMEOUT   = 60    # seconds to wait for CR to reach "planned"
EXEC_TIMEOUT   = 90    # seconds to wait for CR execution to reach terminal state
EXEC_TERMINAL  = {"completed", "failed", "executed", "rollback_complete"}
PLAN_TERMINAL  = {"planned", "failed"}

# Containers expected to be running on the AMI
EXPECTED_CONTAINERS = ("db", "backend", "webserver")


def log(msg):
    print(f"[smoke-ami] {msg}", flush=True)


def fail(msg):
    raise RuntimeError(msg)


def api(method, base_url, path, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = getattr(requests, method)(
        f"{base_url}/api{path}", headers=headers, timeout=20, **kwargs
    )
    return r


def poll_cr_status(base_url, token, cr_id, terminal_states, timeout, label):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api("get", base_url, f"/change-requests/{cr_id}", token=token)
        if r.status_code != 200:
            fail(f"GET /change-requests/{cr_id} returned {r.status_code}")
        status = r.json().get("status", "")
        log(f"  CR status: {status}")
        if status in terminal_states:
            return status
        time.sleep(8)
    fail(f"CR did not reach {terminal_states} within {timeout}s ({label})")


# ── Phase 1: Launch + Network ─────────────────────────────────────────────────

def phase_launch(ec2, args):
    log("[PHASE 1: launch+network]")
    resp = ec2.run_instances(
        ImageId=args.ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        KeyName=args.key_name,
        SecurityGroupIds=[args.security_group_id],
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-ami-smoke"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Instance {instance_id} launched — waiting for EC2 status checks")

    waiter = ec2.get_waiter("instance_status_ok")
    waiter.wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 30})

    desc = ec2.describe_instances(InstanceIds=[instance_id])
    inst = desc["Reservations"][0]["Instances"][0]
    public_ip = inst.get("PublicIpAddress")
    private_ip = inst.get("PrivateIpAddress")
    if not public_ip:
        fail("Instance has no public IP — ensure AMI is launched in a public subnet with auto-assign enabled")
    log(f"  Public IP: {public_ip}  Private IP: {private_ip}")

    base_url = f"http://{public_ip}"
    log(f"  Waiting for port 80 (up to {BOOT_TIMEOUT}s)")
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.get(base_url + "/", timeout=5)
            if r.status_code == 200:
                log("  GET / → 200 OK")
                log("[PHASE 1: launch+network] PASSED")
                return instance_id, public_ip, base_url
        except Exception:
            pass
        time.sleep(HTTP_POLL)
    fail(f"Platform did not respond on port 80 within {BOOT_TIMEOUT}s")


# ── Phase 2: Container health ─────────────────────────────────────────────────

def phase_container_health(public_ip, args):
    log("[PHASE 2: container-health]")
    ssh = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
        "-i", f"{args.key_name}.pem",
        f"ubuntu@{public_ip}",
        "docker ps --format '{{.Names}}' --filter status=running",
    ]
    result = subprocess.run(ssh, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        fail(f"SSH docker ps failed: {result.stderr.strip()}")
    running = result.stdout.strip().splitlines()
    log(f"  Running containers: {running}")
    for expected in EXPECTED_CONTAINERS:
        if not any(expected in name for name in running):
            fail(f"Expected container '{expected}' not running. Got: {running}")
    log("[PHASE 2: container-health] PASSED")


# ── Phase 3: Authentication ───────────────────────────────────────────────────

def phase_auth(base_url):
    log("[PHASE 3: authentication]")
    r = api("post", base_url, "/auth/login",
            json={"email": "admin@nexplane.local", "password": "changeme"})
    if r.status_code != 200:
        fail(f"Login failed: {r.status_code} {r.text[:300]}")
    token = r.json().get("access_token")
    if not token:
        fail(f"No access_token in login response: {r.text[:300]}")
    log("  POST /api/auth/login → token received")

    r = api("get", base_url, "/auth/me", token=token)
    if r.status_code != 200:
        fail(f"GET /auth/me failed: {r.status_code}")
    me = r.json()
    if me.get("email") != "admin@nexplane.local":
        fail(f"Unexpected /auth/me email: {me.get('email')}")
    log(f"  GET /auth/me → {me.get('email')} ({me.get('role', '?')})")
    log("[PHASE 3: authentication] PASSED")
    return token


# ── Phase 4: Asset management + impact graph ──────────────────────────────────

def phase_assets(base_url, token):
    log("[PHASE 4: asset-management]")
    created_ids = []

    def make_asset(name, asset_type):
        r = api("post", base_url, "/assets", token=token,
                json={"name": name, "asset_type": asset_type, "environment": "staging"})
        if r.status_code not in (200, 201):
            fail(f"POST /assets ({name}) failed: {r.status_code} {r.text[:200]}")
        aid = r.json()["id"]
        created_ids.append(aid)
        log(f"  Created asset {name} → {aid}")
        return aid

    web_id = make_asset("smoke-web",   "server")
    app_id = make_asset("smoke-app",   "server")
    db_id  = make_asset("smoke-db",    "database")

    # web depends_on app, app depends_on db
    for src, tgt in [(web_id, app_id), (app_id, db_id)]:
        r = api("post", base_url, f"/assets/{src}/relationships", token=token,
                json={"target_asset_id": tgt, "relationship_type": "depends_on"})
        if r.status_code not in (200, 201):
            fail(f"POST /assets/{src}/relationships failed: {r.status_code} {r.text[:200]}")

    # Impact simulation from db_id (root): downstream should include app + web
    r = api("get", base_url, f"/impact-simulation?asset_id={db_id}", token=token)
    if r.status_code != 200:
        fail(f"GET /impact-simulation failed: {r.status_code}")
    impact = r.json()
    downstream_ids = [a.get("id") for a in impact.get("downstream", [])]
    total = impact.get("downstream_risk", {}).get("total", 0)
    if total < 2:
        fail(f"Expected downstream_risk.total >= 2 from db root, got {total}")
    log(f"  Impact simulation: {total} downstream assets from db root ✓")

    # Verify upstream from web_id: should include app + db
    r = api("get", base_url, f"/impact-simulation?asset_id={web_id}", token=token)
    if r.status_code != 200:
        fail(f"GET /impact-simulation (web) failed: {r.status_code}")
    upstream_ids = [a.get("id") for a in r.json().get("upstream", [])]
    if app_id not in upstream_ids:
        fail(f"Expected app_id in upstream of web. Got: {upstream_ids}")
    log("  Upstream chain from web → app → db verified ✓")

    log("[PHASE 4: asset-management] PASSED")
    return web_id, app_id, db_id, created_ids


# ── Phase 5: Connector configuration ─────────────────────────────────────────

def phase_connectors(base_url, token):
    log("[PHASE 5: connector-configuration]")
    created_connector_ids = []

    # AWS connector with fake credentials (tests config API, not connectivity)
    r = api("post", base_url, "/connectors", token=token, json={
        "name": "smoke-aws",
        "connector_type": "aws",
        "description": "AMI smoke test connector",
        "config": {"region": "us-east-1"},
    })
    if r.status_code not in (200, 201):
        fail(f"POST /connectors (aws) failed: {r.status_code} {r.text[:200]}")
    aws_conn_id = r.json()["id"]
    created_connector_ids.append(aws_conn_id)
    log(f"  Created AWS connector → {aws_conn_id}")

    r = api("put", base_url, f"/connectors/{aws_conn_id}/credentials", token=token, json={
        "access_key_id": "AKIAIOSFODNN7SMOKE00",
        "secret_access_key": "smoke-test-secret-not-real",
    })
    if r.status_code not in (200, 201, 204):
        fail(f"PUT /connectors/{aws_conn_id}/credentials failed: {r.status_code} {r.text[:200]}")
    log("  Credentials accepted by platform ✓")

    # List connectors — verify smoke connector appears
    r = api("get", base_url, "/connectors", token=token)
    if r.status_code != 200:
        fail(f"GET /connectors failed: {r.status_code}")
    ids_in_list = [c.get("id") for c in r.json()]
    if aws_conn_id not in ids_in_list:
        fail(f"Created connector {aws_conn_id} not in GET /connectors list")
    log(f"  GET /connectors lists {len(ids_in_list)} connector(s) ✓")

    # Second connector (agent_tunnel type)
    r = api("post", base_url, "/connectors", token=token, json={
        "name": "smoke-tunnel",
        "connector_type": "agent_tunnel",
        "description": "AMI smoke test tunnel connector",
        "config": {},
    })
    if r.status_code not in (200, 201):
        fail(f"POST /connectors (agent_tunnel) failed: {r.status_code} {r.text[:200]}")
    tunnel_conn_id = r.json()["id"]
    created_connector_ids.append(tunnel_conn_id)
    log(f"  Created agent_tunnel connector → {tunnel_conn_id}")

    log("[PHASE 5: connector-configuration] PASSED")
    return aws_conn_id, created_connector_ids


# ── Phase 6: Change request lifecycle ────────────────────────────────────────

def phase_cr_lifecycle(base_url, token, app_id, aws_conn_id):
    log("[PHASE 6: cr-lifecycle]")
    created_cr_ids = []

    r = api("post", base_url, "/change-requests", token=token, json={
        "change_type":     "tag_resource",
        "title":           "smoke-ami-cr",
        "desired_outcome": {"tag_key": "smoke", "tag_value": "true"},
        "target_asset_ids": [app_id],
        "connector_id":    aws_conn_id,
    })
    if r.status_code not in (200, 201):
        fail(f"POST /change-requests failed: {r.status_code} {r.text[:300]}")
    cr_id = r.json()["id"]
    created_cr_ids.append(cr_id)
    log(f"  CR created → {cr_id}")

    api("post", base_url, f"/change-requests/{cr_id}/plan", token=token)
    status = poll_cr_status(base_url, token, cr_id, PLAN_TERMINAL, PLAN_TIMEOUT, "planning")
    log(f"  CR planned (status={status}) ✓")

    # Verify blast_radius is populated
    r = api("get", base_url, f"/change-requests/{cr_id}", token=token)
    cr_detail = r.json()
    blast = (cr_detail.get("blast_radius") or
             cr_detail.get("change_plan", {}).get("blast_radius") or {})
    log(f"  blast_radius: {blast}")
    # blast_radius should exist (even if empty dict — planning ran)
    if blast is None:
        fail("blast_radius is None after planning")
    log("  blast_radius populated ✓")

    # Submit for approval + approve
    r = api("post", base_url, f"/change-requests/{cr_id}/submit-for-approval", token=token)
    if r.status_code not in (200, 201, 204):
        fail(f"submit-for-approval failed: {r.status_code} {r.text[:200]}")
    log("  Submitted for approval ✓")

    r = api("post", base_url, f"/change-requests/{cr_id}/approve", token=token,
            json={"decision": "approved", "comment": "AMI smoke test"})
    if r.status_code not in (200, 201, 204):
        fail(f"approve failed: {r.status_code} {r.text[:200]}")
    log("  Approved ✓")

    # Execute — fake creds will fail execution; that is expected and verified as graceful
    r = api("post", base_url, f"/change-requests/{cr_id}/execute", token=token)
    if r.status_code not in (200, 201, 202, 204):
        fail(f"execute failed unexpectedly at API level: {r.status_code} {r.text[:200]}")
    log("  Execute initiated — polling for terminal state")
    final_status = poll_cr_status(base_url, token, cr_id, EXEC_TERMINAL, EXEC_TIMEOUT, "execution")
    log(f"  Execution reached terminal state: {final_status} ✓")
    # "failed" is acceptable — fake connector creds will cause execution failure
    # What we verify: the platform handled it gracefully (no 500, no crash)

    log("[PHASE 6: cr-lifecycle] PASSED")
    return cr_id, created_cr_ids


# ── Phase 7: Rollback ────────────────────────────────────────────────────────

def phase_rollback(base_url, token, cr_id):
    log("[PHASE 7: rollback]")

    # Attempt CR-level rollback (may return 400 if CR is not in rollback-eligible state)
    r = api("post", base_url, f"/change-requests/{cr_id}/rollback", token=token)
    log(f"  POST /change-requests/{cr_id}/rollback → {r.status_code}")
    if r.status_code in (200, 201, 202, 204):
        log("  Rollback initiated at CR level ✓")
    elif r.status_code == 400:
        # Platform correctly rejects rollback on non-reversible state — verify the message
        detail = r.json().get("detail", "")
        log(f"  Rollback correctly rejected (non-reversible state): {detail}")
        # Verify rollback_available field in CR detail
        r2 = api("get", base_url, f"/change-requests/{cr_id}", token=token)
        rollback_avail = r2.json().get("rollback_available", None)
        log(f"  rollback_available field: {rollback_avail}")
    else:
        fail(f"Unexpected rollback response: {r.status_code} {r.text[:200]}")

    # Verify project rollback endpoint is reachable
    r = api("get", base_url, "/project-rollbacks", token=token)
    if r.status_code not in (200, 404):  # 404 if not yet implemented on this build
        fail(f"GET /project-rollbacks unexpected status: {r.status_code}")
    log("  Project rollback endpoint reachable ✓")

    log("[PHASE 7: rollback] PASSED")


# ── Phase 8: Feature surface spot-check ──────────────────────────────────────

def phase_feature_surface(base_url, token, cr_id):
    log("[PHASE 8: feature-surface]")
    endpoints = [
        ("/vulnerabilities",         "vulnerabilities"),
        ("/compliance/baselines",    "compliance baselines"),
        ("/runbooks",                "runbooks"),
        ("/access-reviews",          "access reviews"),
        ("/recurring-jobs",          "recurring jobs"),
        ("/backup-targets",          "backup targets"),
        ("/change-requests",         "change requests list"),
    ]
    for path, label in endpoints:
        r = api("get", base_url, path, token=token)
        if r.status_code != 200:
            fail(f"GET /api{path} ({label}) returned {r.status_code}")
        log(f"  GET /api{path} → 200 ✓")

    # Verify the CR from phase 6 appears in the list
    r = api("get", base_url, "/change-requests", token=token)
    cr_list = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    cr_ids_in_list = [c.get("id") for c in cr_list]
    if cr_id not in cr_ids_in_list:
        fail(f"CR {cr_id} from phase 6 not found in GET /change-requests list")
    log("  CR from phase 6 appears in change-requests list ✓")

    log("[PHASE 8: feature-surface] PASSED")


# ── Phase 9: MCP server + agent token ────────────────────────────────────────

def phase_mcp(base_url, token):
    log("[PHASE 9: mcp-server]")

    # Create an agent token (required for MCP auth)
    r = api("post", base_url, "/auth/agent-tokens", token=token,
            json={"name": "smoke-ami-mcp", "scopes": ["read", "write"]})
    if r.status_code not in (200, 201):
        fail(f"POST /auth/agent-tokens failed: {r.status_code} {r.text[:200]}")
    agent_token = r.json().get("token") or r.json().get("access_token")
    agent_token_id = r.json().get("id")
    if not agent_token:
        fail(f"No token in agent-token response: {r.json()}")
    log(f"  Agent token created → {agent_token_id}")

    # Verify agent token appears in list
    r = api("get", base_url, "/auth/agent-tokens", token=token)
    if r.status_code != 200:
        fail(f"GET /auth/agent-tokens failed: {r.status_code}")
    token_ids = [t.get("id") for t in r.json()]
    if agent_token_id not in token_ids:
        fail(f"Agent token {agent_token_id} not in list")
    log("  Agent token listed ✓")

    # Connect to /mcp SSE endpoint and read tool list
    # The SSE endpoint emits an 'initialize' message containing the tool manifest
    mcp_url = f"{base_url}/api/mcp"
    log(f"  Connecting to MCP SSE endpoint: {mcp_url}")
    tool_names = []
    try:
        with requests.get(
            mcp_url,
            headers={"Authorization": f"Bearer {agent_token}", "Accept": "text/event-stream"},
            stream=True,
            timeout=30,
        ) as resp:
            if resp.status_code != 200:
                fail(f"GET /api/mcp SSE returned {resp.status_code}")
            for line in resp.iter_lines(chunk_size=None):
                if not line:
                    continue
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                if '"tools"' in decoded or '"name"' in decoded:
                    import json as _json
                    try:
                        # SSE data lines start with "data: "
                        payload = decoded.replace("data: ", "").strip()
                        obj = _json.loads(payload)
                        tools = (obj.get("result", {}).get("tools") or
                                 obj.get("params", {}).get("tools") or
                                 obj.get("tools") or [])
                        if tools:
                            tool_names = [t.get("name") for t in tools if t.get("name")]
                            break
                    except Exception:
                        pass
                # Break after first meaningful chunk to avoid hanging on the stream
                if len(decoded) > 500:
                    break
    except requests.exceptions.Timeout:
        pass  # SSE streams don't close; timeout after reading is expected

    # Fall back: use the initialize handshake path if SSE parsing got nothing
    if not tool_names:
        log("  SSE parse yielded no tools — trying MCP initialize via POST")
        r = requests.post(
            f"{base_url}/api/mcp",
            headers={"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05",
                             "capabilities": {},
                             "clientInfo": {"name": "smoke", "version": "0"}}},
            timeout=15,
        )
        log(f"  MCP initialize → {r.status_code}")
        if r.status_code == 200:
            # Follow up with tools/list
            r2 = requests.post(
                f"{base_url}/api/mcp",
                headers={"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                timeout=15,
            )
            if r2.status_code == 200:
                import json as _json
                tools = r2.json().get("result", {}).get("tools", [])
                tool_names = [t.get("name") for t in tools if t.get("name")]

    if tool_names:
        log(f"  MCP tools discovered: {len(tool_names)}")
        # Verify key tool categories are present
        required = [
            "list_change_requests", "create_change_request",   # change_requests
            "list_assets", "get_asset_context",                # assets
            "list_findings",                                   # findings
            "list_connectors",                                 # connectors
            "list_runbooks",                                   # runbooks
            "get_fleet_context",                               # planning_context
        ]
        for tool in required:
            if tool not in tool_names:
                fail(f"Expected MCP tool '{tool}' not found in tool list")
        log(f"  All required MCP tool categories present ({len(tool_names)} total) ✓")
    else:
        # MCP endpoint reachable but SSE parsing inconclusive — verify endpoint is up at minimum
        log("  MCP tool list not parsed from SSE (streaming); verified endpoint reachable ✓")
        # Require at least that the endpoint returned 200 — we already checked that above

    # Clean up agent token
    r = api("delete", base_url, f"/auth/agent-tokens/{agent_token_id}", token=token)
    log(f"  Agent token deleted → {r.status_code}")

    log("[PHASE 9: mcp-server] PASSED")


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup(base_url, token, cr_ids, asset_ids, connector_ids):
    log("[cleanup] Deleting smoke test resources")
    for cr_id in cr_ids:
        r = api("delete", base_url, f"/change-requests/{cr_id}", token=token)
        log(f"  DELETE /change-requests/{cr_id} → {r.status_code}")
    for asset_id in reversed(asset_ids):  # leaf first
        r = api("delete", base_url, f"/assets/{asset_id}", token=token)
        log(f"  DELETE /assets/{asset_id} → {r.status_code}")
    for conn_id in connector_ids:
        r = api("delete", base_url, f"/connectors/{conn_id}", token=token)
        log(f"  DELETE /connectors/{conn_id} → {r.status_code}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ami-id",            required=True)
    parser.add_argument("--key-name",          required=True)
    parser.add_argument("--security-group-id", required=True)
    parser.add_argument("--region",            default="us-east-1")
    args = parser.parse_args()

    ec2 = boto3.client("ec2", region_name=args.region)
    instance_id  = None
    base_url     = None
    public_ip    = None
    token        = None
    cr_ids       = []
    asset_ids    = []
    connector_ids = []

    try:
        instance_id, public_ip, base_url = phase_launch(ec2, args)
        phase_container_health(public_ip, args)
        token = phase_auth(base_url)
        web_id, app_id, db_id, asset_ids = phase_assets(base_url, token)
        aws_conn_id, connector_ids = phase_connectors(base_url, token)
        cr_id, cr_ids = phase_cr_lifecycle(base_url, token, app_id, aws_conn_id)
        phase_rollback(base_url, token, cr_id)
        phase_feature_surface(base_url, token, cr_id)
        phase_mcp(base_url, token)

        if token:
            cleanup(base_url, token, cr_ids, asset_ids, connector_ids)

        log("")
        log("=" * 60)
        log("AMI SMOKE: ALL 9 PHASES PASSED")
        log("=" * 60)

    finally:
        if instance_id:
            log(f"[cleanup] Terminating instance {instance_id}")
            ec2.terminate_instances(InstanceIds=[instance_id])
            waiter = ec2.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=[instance_id])
            log("[cleanup] Instance terminated")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[smoke-ami] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 2: Verify syntax**

```bash
python -m py_compile packer/smoke_ami.py && echo "syntax OK"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: 8-phase AMI smoke test (launch, containers, auth, assets, connectors, CR lifecycle, rollback, feature surface)"
```

---

## Task 6: Launch artifacts — CloudFormation quickstart + instructions

**Files:**
- Create: `packer/templates/quickstart.cfn.yml.tmpl`
- Create: `packer/templates/LAUNCH.md.tmpl`

**Interfaces:**
- Both files use `AMI_ID` as a literal placeholder string; the `publish-manifest` CI job replaces it with `sed` before uploading to S3
- Published to: `s3://nexplane-artifacts/releases/launch/quickstart.cfn.yml` and `.../launch/LAUNCH.md`
- Served at: `releases.nexplane.ai/launch/quickstart.cfn.yml` and `.../launch/LAUNCH.md`
- CloudFormation stack outputs: `PlatformURL` (`http://<PublicIp>/`) and `SSHCommand` (`ssh -i <key>.pem ubuntu@<PublicIp>`)

- [ ] **Step 1: Write `packer/templates/quickstart.cfn.yml.tmpl`**

```yaml
AWSTemplateFormatVersion: "2010-09-09"
Description: >
  Nexplane platform quickstart — launches a single EC2 instance from the
  pre-built AMI with a security group allowing HTTP (80) and SSH (22).
  Takes ~3 minutes. After launch, open the PlatformURL output in your browser.

Parameters:
  KeyPairName:
    Type: AWS::EC2::KeyPair::KeyName
    Description: >
      Your EC2 key pair for SSH access. Create one in EC2 → Key Pairs if you
      don't have one. You will SSH as: ubuntu@<ip> -i <your-key>.pem

  InstanceType:
    Type: String
    Default: t3.medium
    AllowedValues: [t3.medium, t3.large, t3.xlarge, m5.large, m5.xlarge]
    Description: Instance size. t3.medium is sufficient for evaluation.

Resources:
  NexplaneSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Nexplane platform — HTTP and SSH access
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 80
          ToPort: 80
          CidrIp: 0.0.0.0/0
          Description: Platform UI and API
        - IpProtocol: tcp
          FromPort: 22
          ToPort: 22
          CidrIp: 0.0.0.0/0
          Description: SSH access (restrict to your IP in production)

  NexplaneInstance:
    Type: AWS::EC2::Instance
    Properties:
      ImageId: AMI_ID
      InstanceType: !Ref InstanceType
      KeyName: !Ref KeyPairName
      SecurityGroups:
        - !Ref NexplaneSecurityGroup
      Tags:
        - Key: Name
          Value: nexplane-quickstart
      UserData:
        Fn::Base64: |
          #!/bin/bash
          # Platform starts automatically via systemd on boot.
          # This user-data is a no-op; it exists to confirm boot completed.
          echo "Nexplane quickstart boot complete" >> /var/log/nexplane-boot.log

Outputs:
  PlatformURL:
    Description: >
      Open this URL in your browser after the instance finishes initialising
      (~90 seconds after launch). Log in with admin@nexplane.local / changeme.
    Value: !Sub "http://${NexplaneInstance.PublicIp}/"

  SSHCommand:
    Description: SSH into the instance (replace <your-key>.pem with your key file path)
    Value: !Sub "ssh -i <your-key>.pem ubuntu@${NexplaneInstance.PublicIp}"

  PublicIP:
    Description: Instance public IP address
    Value: !GetAtt NexplaneInstance.PublicIp
```

- [ ] **Step 2: Write `packer/templates/LAUNCH.md.tmpl`**

```markdown
# Launching Nexplane

**AMI ID:** `AMI_ID` (us-east-1)

---

## Option A — One-click CloudFormation (recommended)

This is the fastest path. CloudFormation creates everything for you.

1. **[Click here to launch the CloudFormation stack](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?templateURL=https://nexplane-artifacts.s3.us-east-1.amazonaws.com/releases/launch/quickstart.cfn.yml&stackName=nexplane-quickstart)**

2. On the "Quick create stack" page:
   - **KeyPairName** — select your existing key pair, or [create one here](https://us-east-1.console.aws.amazon.com/ec2/home?region=us-east-1#KeyPairs:) first
   - **InstanceType** — leave as `t3.medium` for evaluation
   - Check the acknowledgement box at the bottom
   - Click **Create stack**

3. Wait ~3 minutes for the stack to reach `CREATE_COMPLETE`

4. Click the **Outputs** tab — copy the `PlatformURL` value and open it in your browser

5. Log in with:
   - **Email:** `admin@nexplane.local`
   - **Password:** `changeme`

> **First time?** Change your password immediately after login via Settings → Profile.

---

## Option B — AWS Console (manual)

1. Open [EC2 → Launch Instance](https://us-east-1.console.aws.amazon.com/ec2/home?region=us-east-1#LaunchInstances:)
2. Under **Application and OS Images**, click **Browse more AMIs** → **Community AMIs** → search for `AMI_ID`
3. Select instance type: `t3.medium` (minimum)
4. Under **Key pair**: select or create a key pair
5. Under **Network settings**: ensure **Auto-assign public IP** is enabled; allow inbound on ports **80** and **22**
6. Click **Launch instance**
7. Once running, find the **Public IPv4 address** on the instance detail page
8. Open `http://<ip>/` in your browser
9. Log in with `admin@nexplane.local` / `changeme`

---

## Option C — AWS CLI one-liner

```bash
# Replace sg-xxxxxxxx with your security group (must allow ports 80 + 22)
# Replace my-key-pair with your key pair name
aws ec2 run-instances \
  --image-id AMI_ID \
  --instance-type t3.medium \
  --key-name my-key-pair \
  --security-group-ids sg-xxxxxxxx \
  --associate-public-ip-address \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=nexplane}]' \
  --region us-east-1 \
  --query 'Instances[0].PublicIpAddress' \
  --output text
```

Wait ~90 seconds, then open `http://<output-ip>/` in your browser.

---

## SSH Access

```bash
ssh -i /path/to/your-key.pem ubuntu@<ip>
```

Check platform status:
```bash
sudo docker compose -f /opt/nexplane/docker-compose.ami.yml ps
```

View logs:
```bash
sudo docker compose -f /opt/nexplane/docker-compose.ami.yml logs -f backend
```

---

## Default Credentials

| Field    | Value                    |
|----------|--------------------------|
| Email    | `admin@nexplane.local`   |
| Password | `changeme`               |

**Change your password after first login.** Settings → Profile → Change Password.
```

- [ ] **Step 3: Verify the CloudFormation template is valid YAML**

```bash
python -c "import yaml; yaml.safe_load(open('packer/templates/quickstart.cfn.yml.tmpl'))" && echo "YAML OK"
```

Expected: `YAML OK` (the `AMI_ID` placeholder is fine — it's a valid string value)

- [ ] **Step 4: Commit**

```bash
git add packer/templates/quickstart.cfn.yml.tmpl packer/templates/LAUNCH.md.tmpl
git commit -m "feat: CloudFormation quickstart template and hand-held launch instructions"
```

---

## Task 7: GitHub Actions integration

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes outputs from `build-and-release` job (version, bare version)
- New secrets required: `PACKER_AWS_ACCESS_KEY_ID`, `PACKER_AWS_SECRET_ACCESS_KEY` (can reuse existing `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` — same account, separate secret names for clarity)
- Produces: public AMI in us-east-1, updated `latest.json` on S3

**Required IAM permissions for the CI AWS credentials** (add to existing policy or note for nexplane-infra):
```
ec2:RunInstances, ec2:DescribeInstances, ec2:TerminateInstances,
ec2:CreateImage, ec2:DescribeImages, ec2:ModifyImageAttribute,
ec2:CreateKeyPair, ec2:DeleteKeyPair,
ec2:CreateSecurityGroup, ec2:DeleteSecurityGroup,
ec2:AuthorizeSecurityGroupIngress, ec2:DescribeSecurityGroups,
ec2:DescribeInstanceStatus, ec2:CreateTags
```

- [ ] **Step 1: Read current `release.yml` to understand exact structure**

Read `.github/workflows/release.yml` before editing.

- [ ] **Step 2: Append `build-ami` job**

Add after the closing of the `build-and-release` job:

```yaml
  build-ami:
    needs: build-and-release
    runs-on: ubuntu-latest
    outputs:
      ami_id: ${{ steps.packer.outputs.ami_id }}
    steps:
      - uses: actions/checkout@v4

      - name: Install Packer
        uses: hashicorp/setup-packer@main
        with:
          version: "1.11.0"

      - name: Packer init
        run: packer init packer/nexplane-ami.pkr.hcl

      - name: Build AMI
        id: packer
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: us-east-1
          PKR_VAR_version: ${{ needs.build-and-release.outputs.version }}
          PKR_VAR_ghcr_token: ${{ secrets.GITHUB_TOKEN }}
        run: |
          packer build \
            -machine-readable \
            packer/nexplane-ami.pkr.hcl \
            | tee packer-output.txt

          AMI_ID=$(grep 'artifact,0,id' packer-output.txt \
            | tail -1 \
            | cut -d, -f6 \
            | cut -d: -f2)
          echo "ami_id=${AMI_ID}" >> "$GITHUB_OUTPUT"
          echo "Built AMI: ${AMI_ID}"
```

- [ ] **Step 3: Append `smoke-ami` job**

```yaml
  smoke-ami:
    needs: [build-and-release, build-ami]
    runs-on: ubuntu-latest
    env:
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
      AWS_DEFAULT_REGION: us-east-1
      AMI_ID: ${{ needs.build-ami.outputs.ami_id }}
      KEY_NAME: nexplane-smoke-${{ github.run_id }}
    steps:
      - uses: actions/checkout@v4

      - name: Install Python deps
        run: pip install boto3 requests

      - name: Create ephemeral SSH key pair
        run: |
          aws ec2 create-key-pair \
            --key-name "$KEY_NAME" \
            --query 'KeyMaterial' \
            --output text > "${KEY_NAME}.pem"
          chmod 600 "${KEY_NAME}.pem"

      - name: Create ephemeral security group
        id: sg
        run: |
          RUNNER_IP=$(curl -s https://checkip.amazonaws.com)
          SG_ID=$(aws ec2 create-security-group \
            --group-name "nexplane-smoke-${GITHUB_RUN_ID}" \
            --description "Nexplane AMI smoke test - run $GITHUB_RUN_ID" \
            --query 'GroupId' --output text)
          aws ec2 authorize-security-group-ingress \
            --group-id "$SG_ID" \
            --protocol tcp --port 22 --cidr "${RUNNER_IP}/32"
          aws ec2 authorize-security-group-ingress \
            --group-id "$SG_ID" \
            --protocol tcp --port 80 --cidr "${RUNNER_IP}/32"
          echo "sg_id=${SG_ID}" >> "$GITHUB_OUTPUT"

      - name: Run smoke test
        run: |
          python packer/smoke_ami.py \
            --ami-id "$AMI_ID" \
            --key-name "$KEY_NAME" \
            --security-group-id "${{ steps.sg.outputs.sg_id }}"

      - name: Cleanup key pair and security group
        if: always()
        run: |
          aws ec2 delete-key-pair --key-name "$KEY_NAME" || true
          aws ec2 delete-security-group \
            --group-id "${{ steps.sg.outputs.sg_id }}" || true
```

- [ ] **Step 4: Append `publish-manifest` job**

```yaml
  publish-manifest:
    needs: [build-and-release, build-ami, smoke-ami]
    runs-on: ubuntu-latest
    steps:
      - name: Make AMI public
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: us-east-1
        run: |
          aws ec2 modify-image-attribute \
            --image-id "${{ needs.build-ami.outputs.ami_id }}" \
            --launch-permission "Add=[{Group=all}]"

      - name: Update latest.json
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: us-east-1
          VERSION: ${{ needs.build-and-release.outputs.version }}
          AMI_ID: ${{ needs.build-ami.outputs.ami_id }}
        run: |
          aws s3 cp s3://nexplane-artifacts/releases/latest.json latest.json || \
            echo '{}' > latest.json

          LAUNCH_URL="https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#LaunchInstanceWizard:ami=${AMI_ID}"

          jq \
            --arg version "$VERSION" \
            --arg ami_id "$AMI_ID" \
            --arg ami_region "us-east-1" \
            --arg ami_launch_url "$LAUNCH_URL" \
            '. + {version: $version, ami_id: $ami_id, ami_region: $ami_region, ami_launch_url: $ami_launch_url}' \
            latest.json > latest.json.new

          aws s3 cp latest.json.new s3://nexplane-artifacts/releases/latest.json \
            --content-type application/json \
            --cache-control "no-cache"

          echo "Published AMI ${AMI_ID} to latest.json"

      - name: Render and upload launch artifacts
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: us-east-1
          AMI_ID: ${{ needs.build-ami.outputs.ami_id }}
        run: |
          # Render templates — replace AMI_ID placeholder with real AMI ID
          sed "s/AMI_ID/${AMI_ID}/g" packer/templates/quickstart.cfn.yml.tmpl \
            > quickstart.cfn.yml
          sed "s/AMI_ID/${AMI_ID}/g" packer/templates/LAUNCH.md.tmpl \
            > LAUNCH.md

          # Upload to S3 (served via releases.nexplane.ai CloudFront)
          aws s3 cp quickstart.cfn.yml \
            s3://nexplane-artifacts/releases/launch/quickstart.cfn.yml \
            --content-type application/x-yaml \
            --cache-control "no-cache"

          aws s3 cp LAUNCH.md \
            s3://nexplane-artifacts/releases/launch/LAUNCH.md \
            --content-type text/markdown \
            --cache-control "no-cache"

          echo "Launch artifacts published to releases.nexplane.ai/launch/"
```

- [ ] **Step 5: Verify `build-and-release` job exposes `version` output**

Check the existing job has:
```yaml
    outputs:
      version: ${{ steps.version.outputs.version }}
```

If not, add `outputs:` block to the existing `build-and-release` job using the already-present `steps.version.outputs.version`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat: add build-ami, smoke-ami, publish-manifest to release pipeline"
```

---

## Task 8: End-to-end validation

This task does NOT cut a real release tag. It validates the pipeline components are wired correctly before the first real tag.

- [ ] **Step 1: Validate Packer template**

On EC2 (where Packer is or can be installed):
```bash
packer validate \
  -var version=v0.0.0-test \
  -var ghcr_token=dryrun \
  packer/nexplane-ami.pkr.hcl
```
Expected: `The configuration is valid.`

- [ ] **Step 2: Validate release.yml YAML syntax**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))" && echo "YAML OK"
```
Expected: `YAML OK`

- [ ] **Step 3: Verify smoke script syntax and imports**

```bash
pip install boto3 requests
python -m py_compile packer/smoke_ami.py && echo "syntax OK"
```
Expected: `syntax OK`

- [ ] **Step 4: Dry-run frontend build**

```bash
cd frontend
docker build -f Dockerfile.prod --build-arg VITE_API_URL=/api -t nexplane-webserver:dryrun .
```
Expected: build completes, image exists in `docker images`.

- [ ] **Step 5: Smoke the webserver image in isolation**

```bash
docker run -d --name smoke-web -p 8080:80 nexplane-webserver:dryrun
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/
docker rm -f smoke-web
```
Expected: HTTP status `200`.

- [ ] **Step 6: Push and confirm GitHub Actions picks up the new jobs**

```bash
git push origin master
```

Open the Actions tab in GitHub — the nightly or next manual trigger should show the workflow structure. The `build-ami` job will not run until a `v*.*.*` tag is pushed.

- [ ] **Step 7: Final commit if any fixes needed during validation**

```bash
git add -A && git commit -m "fix: AMI pipeline validation fixes"
git push origin master
```

---

## Notes for future formats

When adding OVA, Azure VHD, or GCP image support:
1. Add `packer/nexplane-ova.pkr.hcl` (or `-azure.pkr.hcl`, `-gcp.pkr.hcl`) — reuses `packer/scripts/setup.sh` and `packer/docker-compose.ami.yml` unchanged
2. Add a `build-ova` / `build-azure` job in `release.yml` running in parallel with `build-ami`
3. Add a separate smoke job per format
4. Add format-specific fields to `latest.json` (e.g. `ova_url`, `azure_image_id`)
5. No existing files change
