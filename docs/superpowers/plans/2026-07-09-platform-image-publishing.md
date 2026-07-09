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
| `packer/smoke_ami.py` | Create | Post-build smoke: launch instance → verify HTTP + auth + docker ps → terminate |
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
- Terminates test instance in a finally block (always cleans up)

- [ ] **Step 1: Write `packer/smoke_ami.py`**

```python
#!/usr/bin/env python3
"""
AMI smoke test — launch instance, verify platform is up, terminate.
Usage: python packer/smoke_ami.py --ami-id ami-xxx --key-name smoke-key --security-group-id sg-xxx
"""
import argparse
import subprocess
import sys
import time

import boto3
import requests

INSTANCE_TYPE = "t3.medium"
BOOT_TIMEOUT = 360   # seconds to wait for platform HTTP
POLL_INTERVAL = 15


def log(msg):
    print(f"[smoke-ami] {msg}", flush=True)


def wait_for_http(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ami-id", required=True)
    parser.add_argument("--key-name", required=True)
    parser.add_argument("--security-group-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    ec2 = boto3.client("ec2", region_name=args.region)
    instance_id = None

    try:
        log(f"Launching test instance from {args.ami_id}")
        resp = ec2.run_instances(
            ImageId=args.ami_id,
            InstanceType=INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            KeyName=args.key_name,
            SecurityGroupIds=[args.security_group_id],
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "nexplane-ami-smoke"}],
            }],
        )
        instance_id = resp["Instances"][0]["InstanceId"]
        log(f"Instance {instance_id} launched — waiting for status checks")

        waiter = ec2.get_waiter("instance_status_ok")
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 24})

        desc = ec2.describe_instances(InstanceIds=[instance_id])
        public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress")
        if not public_ip:
            raise RuntimeError("Instance has no public IP — launch in a public subnet")

        base_url = f"http://{public_ip}"
        log(f"Instance ready at {base_url} — waiting for platform HTTP (up to {BOOT_TIMEOUT}s)")

        if not wait_for_http(base_url + "/", BOOT_TIMEOUT):
            raise RuntimeError(f"Platform did not respond at {base_url}/ within {BOOT_TIMEOUT}s")
        log("GET / → 200 OK")

        # Auth smoke
        log("Testing default credentials")
        r = requests.post(
            f"{base_url}/api/auth/login",
            json={"email": "admin@nexplane.local", "password": "changeme"},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Login failed: {r.status_code} {r.text[:200]}")
        token = r.json().get("access_token") or r.json().get("token")
        if not token:
            raise RuntimeError(f"No token in login response: {r.text[:200]}")
        log("POST /api/auth/login → token received")

        # Basic API smoke
        r = requests.get(
            f"{base_url}/api/assets",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"GET /api/assets failed: {r.status_code}")
        log("GET /api/assets → 200 OK")

        # Container health via SSH
        log("Checking container health via SSH")
        ssh_cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
            "-i", f"{args.key_name}.pem",
            f"ubuntu@{public_ip}",
            "docker ps --format '{{.Names}}' --filter status=running",
        ]
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"SSH docker ps failed: {result.stderr}")
        running = result.stdout.strip().splitlines()
        log(f"Running containers: {running}")
        for expected in ("db", "backend", "webserver"):
            if not any(expected in name for name in running):
                raise RuntimeError(f"Expected container '{expected}' not running. Got: {running}")
        log("All expected containers running")

        log("SMOKE PASSED")

    finally:
        if instance_id:
            log(f"Terminating {instance_id}")
            ec2.terminate_instances(InstanceIds=[instance_id])
            waiter = ec2.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=[instance_id])
            log("Instance terminated")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[smoke-ami] FAILED: {e}", file=sys.stderr)
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
git commit -m "feat: AMI smoke test script"
```

---

## Task 6: GitHub Actions integration

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

## Task 7: End-to-end validation

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
